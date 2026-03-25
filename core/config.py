"""YAML configuration loader with validation and environment variable overrides.

Loads config from config/config.yaml, validates required sections,
and applies SF_-prefixed environment variable overrides.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Required top-level sections in config.yaml
REQUIRED_SECTIONS = (
    "paths",
    "pipeline",
    "ingestion",
    "scene_splitter",
    "transcription",
    "face_detection",
    "scoring",
    "clip_builder",
    "hook_generator",
    "tts",
    "subtitle",
    "compositor",
    "renderer",
    "thumbnail",
    "metadata",
    "scheduler",
    "publisher",
    "channel",
)

# Required keys within each section
REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "paths": ("output_dir", "temp_dir", "database"),
    "pipeline": ("min_clip_duration", "max_clip_duration", "output_resolution", "output_framerate"),
    "ingestion": ("min_duration_seconds", "max_duration_seconds", "supported_formats"),
    "scoring": ("weights", "min_composite_score"),
}

# Environment variable prefix → config path mappings
ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "SF_OUTPUT_DIR": ("paths", "output_dir"),
    "SF_TEMP_DIR": ("paths", "temp_dir"),
    "SF_DATABASE": ("paths", "database"),
    "SF_TRANSCRIPTION_MODEL": ("transcription", "model_size"),
    "SF_TRANSCRIPTION_LANGUAGE": ("transcription", "language"),
    "SF_FFMPEG_TIMEOUT": ("pipeline", "ffmpeg_timeout"),
}


def load_config(config_path: str = "config/config.yaml") -> dict[str, Any]:
    """Load and validate configuration from YAML file.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Validated configuration dictionary.

    Raises:
        FileNotFoundError: If config file does not exist.
        ValueError: If config is invalid or missing required fields.
    """
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    if not isinstance(config, dict):
        raise ValueError(f"Configuration must be a YAML mapping, got {type(config).__name__}")

    _validate_config(config)
    config = _apply_env_overrides(config)

    return config


def _validate_config(config: dict[str, Any]) -> None:
    """Validate that all required sections and keys exist.

    Raises:
        ValueError: If any required section or key is missing.
    """
    missing_sections = [s for s in REQUIRED_SECTIONS if s not in config]
    if missing_sections:
        raise ValueError(
            f"Missing required configuration sections: {', '.join(sorted(missing_sections))}"
        )

    errors: list[str] = []
    for section, keys in sorted(REQUIRED_KEYS.items()):
        if section not in config:
            continue
        for key in keys:
            if key not in config[section]:
                errors.append(f"{section}.{key}")

    if errors:
        raise ValueError(
            f"Missing required configuration keys: {', '.join(sorted(errors))}"
        )


def _apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Apply SF_-prefixed environment variable overrides to config.

    Environment variables take precedence over YAML values.
    Numeric strings are converted to int/float as appropriate.
    """
    for env_var, path in sorted(ENV_OVERRIDES.items()):
        env_val = os.environ.get(env_var)
        if env_val is None:
            continue

        converted_val: Any = _convert_env_value(env_val)

        # Navigate to the parent dict and set the value
        current = config
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[path[-1]] = converted_val

        logger.debug(
            "Environment override applied",
            extra={"env_var": env_var, "config_path": ".".join(path)},
        )

    return config


def _convert_env_value(value: str) -> Any:
    """Convert environment variable string to appropriate Python type."""
    if value.lower() in ("true", "false"):
        return value.lower() == "true"

    try:
        return int(value)
    except ValueError:
        pass

    try:
        return float(value)
    except ValueError:
        pass

    return value
