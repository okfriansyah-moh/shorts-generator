#!/usr/bin/env python3
"""Shorts Factory — Pipeline Entry Point.

Parses arguments, loads configuration, verifies dependencies,
initializes the database, and launches the pipeline.

Usage:
    python3 run_pipeline.py <video_file_path>
    python3 run_pipeline.py --output /path/to/output <video_file_path>
    python3 run_pipeline.py --no-face-detection <video_file_path>
    python3 run_pipeline.py --local-only <video_file_path>
    python3 run_pipeline.py --tts <video_file_path>
    python3 run_pipeline.py --gameplay-only <video_file_path>
    python3 run_pipeline.py --config config/config.yaml <video_file_path>
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from core.account_loader import load_account_config
from core.config import load_config
from core.dependencies import check_all_dependencies
from core.logging import configure_logging
from core.orchestrator import PIPELINE_STAGES, Orchestrator
from database.adapter import DatabaseAdapter
from database.connection import initialize_database

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Shorts Factory — Transform long-form videos into YouTube Shorts.",
    )
    parser.add_argument(
        "video_path",
        help="Path to the input video file.",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to configuration file (default: config/config.yaml).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: INFO).",
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        default=False,
        help="Enable NVIDIA GPU acceleration (requires NVENC-capable GPU).",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="DIR",
        help="Output directory for generated clips (default: value from config.yaml).",
    )
    parser.add_argument(
        "--no-face-detection",
        action="store_true",
        default=False,
        dest="no_face_detection",
        help="Skip face detection and use gameplay-only compositor layout.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        default=False,
        dest="local_only",
        help="Generate clips locally without scheduling or publishing to YouTube.",
    )
    parser.add_argument(
        "--tts",
        action="store_true",
        default=False,
        help="Mix TTS narration audio with original gameplay audio (default: original audio only).",
    )
    parser.add_argument(
        "--gameplay-only",
        action="store_true",
        default=False,
        dest="gameplay_only",
        help="Use gameplay-only layout with blurred background (default: split face+gameplay).",
    )
    parser.add_argument(
        "--video-type",
        default=None,
        choices=["gameplay", "podcast", "sports_tennis", "sports_football", "sports_padel"],
        dest="video_type",
        help="Video type. Selects per-type config overlays and compositor strategy.",
    )
    parser.add_argument(
        "--sports-layout",
        default=None,
        choices=["sports_center_crop", "sports_letterbox", "sports_action_crop"],
        dest="sports_layout",
        help="Override the default compositor layout for sports video types.",
    )
    parser.add_argument(
        "--account",
        default=None,
        metavar="NAME",
        help="Account name (folder under config/accounts/). Loads per-account config overrides.",
    )
    return parser.parse_args(argv)


def validate_video_path(video_path: str) -> str:
    """Validate that the video file exists and return its absolute path.

    Raises:
        SystemExit: If the file does not exist.
    """
    abs_path = os.path.abspath(video_path)
    if not os.path.isfile(abs_path):
        logger.critical(
            "Video file not found",
            extra={"stage": "startup", "video_id": "", "file_path": abs_path},
        )
        sys.exit(1)
    return abs_path


def setup_output_dirs(config: dict) -> None:
    """Create output directories as specified in config."""
    output_dir = config["paths"]["output_dir"]
    temp_dir = config["paths"]["temp_dir"]
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)


# Registry mapping video_type → ordered list of overlay maps.
# Each entry is a list of {config_key: base_section} dicts applied in order
# (later layers win). Sports types use two layers: shared sports_* then
# sport-specific sports_<subtype>_*.
_OVERLAY_REGISTRY: dict[str, list[dict[str, str]]] = {
    "podcast": [
        {
            "podcast_ingestion": "ingestion",
            "podcast_scene_splitter": "scene_splitter",
            "podcast_face_detection": "face_detection",
            "podcast_scoring": "scoring",
            "podcast_compositor": "compositor",
        },
    ],
    "sports_tennis": [
        # Layer 1: shared sports defaults
        {
            "sports_ingestion": "ingestion",
            "sports_scene_splitter": "scene_splitter",
            "sports_face_detection": "face_detection",
            "sports_scoring": "scoring",
            "sports_compositor": "compositor",
        },
        # Layer 2: tennis-specific overrides (applied on top of layer 1)
        {
            "sports_tennis_ingestion": "ingestion",
            "sports_tennis_scene_splitter": "scene_splitter",
            "sports_tennis_face_detection": "face_detection",
            "sports_tennis_scoring": "scoring",
            "sports_tennis_compositor": "compositor",
        },
    ],
    "sports_football": [
        # Layer 1: shared sports defaults
        {
            "sports_ingestion": "ingestion",
            "sports_scene_splitter": "scene_splitter",
            "sports_face_detection": "face_detection",
            "sports_scoring": "scoring",
            "sports_compositor": "compositor",
        },
        # Layer 2: football-specific overrides
        {
            "sports_football_ingestion": "ingestion",
            "sports_football_scene_splitter": "scene_splitter",
            "sports_football_face_detection": "face_detection",
            "sports_football_scoring": "scoring",
            "sports_football_compositor": "compositor",
        },
    ],
    "sports_padel": [
        # Layer 1: shared sports defaults
        {
            "sports_ingestion": "ingestion",
            "sports_scene_splitter": "scene_splitter",
            "sports_face_detection": "face_detection",
            "sports_scoring": "scoring",
            "sports_compositor": "compositor",
        },
        # Layer 2: padel-specific overrides
        {
            "sports_padel_ingestion": "ingestion",
            "sports_padel_scene_splitter": "scene_splitter",
            "sports_padel_face_detection": "face_detection",
            "sports_padel_scoring": "scoring",
            "sports_padel_compositor": "compositor",
        },
    ],
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge *overlay* into *base*, returning a new dict.

    For each key in overlay:
    - If the value in both dicts is itself a dict, recurse.
    - Otherwise, the overlay value wins (replaces the base value).
    Keys present only in *base* are preserved unchanged.
    """
    merged: dict = dict(base)
    for key, overlay_val in overlay.items():
        base_val = merged.get(key)
        if isinstance(base_val, dict) and isinstance(overlay_val, dict):
            merged[key] = _deep_merge(base_val, overlay_val)
        else:
            merged[key] = overlay_val
    return merged


def _apply_video_type_overrides(config: dict) -> None:
    """Merge video-type-specific config overlays based on video_type.

    Looks up the video_type in _OVERLAY_REGISTRY and applies each layer's
    overlay map in order via deep merge. Later layers win over earlier ones;
    both win over the base config. Sections absent from a layer are skipped
    (partial overrides work — only specified keys are replaced).

    This function is a no-op for 'gameplay' (or absent video_type).
    """
    video_type = config.get("video_type", "gameplay")
    layers = _OVERLAY_REGISTRY.get(video_type)
    if not layers:
        return

    for layer in layers:
        for overlay_key, base_key in layer.items():
            overlay = config.get(overlay_key)
            if overlay is None:
                continue
            base = config.get(base_key, {})
            config[base_key] = _deep_merge(base, overlay)

    logger.info(
        "Video type config overlays applied",
        extra={"stage": "startup", "video_id": "", "video_type": video_type},
    )


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the pipeline."""
    from core.config import load_dotenv as _load_dotenv
    _load_dotenv()
    args = parse_args(argv)

    # Configure logging first
    configure_logging(level=args.log_level)

    logger.info(
        "Shorts Factory starting",
        extra={"stage": "startup", "video_id": ""},
    )

    # Load configuration
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        logger.critical(
            "Configuration error",
            extra={"stage": "startup", "video_id": "", "error": str(exc)},
        )
        return 1

    # Apply per-account config overrides when --account is given
    if args.account:
        try:
            config = load_account_config(
                args.account,
                config,
                project_root=os.path.dirname(os.path.abspath(__file__)),
            )
        except (FileNotFoundError, ValueError) as exc:
            logger.critical(
                "Account config error",
                extra={"stage": "startup", "video_id": "", "error": str(exc)},
            )
            return 1

    # Apply --gpu CLI override
    if args.gpu:
        if "gpu" not in config:
            config["gpu"] = {}
        config["gpu"]["enabled"] = True

    # Apply --output CLI override
    if args.output:
        output_dir = os.path.abspath(args.output)
        config["paths"]["output_dir"] = output_dir
        config["paths"]["temp_dir"] = os.path.join(output_dir, "temp")
        config["paths"]["database"] = os.path.join(output_dir, "shorts_factory.db")

    # Apply --no-face-detection CLI override
    if args.no_face_detection:
        if "face_detection" not in config:
            config["face_detection"] = {}
        config["face_detection"]["skip"] = True

    # Apply --local-only CLI override
    if args.local_only:
        if "pipeline" not in config:
            config["pipeline"] = {}
        config["pipeline"]["local_only"] = True

    # Apply --tts CLI override
    if args.tts:
        if "renderer" not in config:
            config["renderer"] = {}
        config["renderer"]["audio_source"] = "mixed"

    # Apply --gameplay-only CLI override
    if args.gameplay_only:
        if "compositor" not in config:
            config["compositor"] = {}
        config["compositor"]["default_layout"] = "gameplay_only"

    # Apply --video-type CLI override and merge type-specific config overlays
    if args.video_type:
        config["video_type"] = args.video_type
    _apply_video_type_overrides(config)

    # Apply --sports-layout CLI override (sets override_layout in compositor config)
    if args.sports_layout:
        if "compositor" not in config:
            config["compositor"] = {}
        config["compositor"]["override_layout"] = args.sports_layout

    # Check dependencies
    check_all_dependencies(config)

    # Validate video file
    video_path = validate_video_path(args.video_path)

    # Setup output directories
    setup_output_dirs(config)

    # Initialize database
    db_path = config["paths"]["database"]
    try:
        conn = initialize_database(db_path)
    except RuntimeError as exc:
        logger.critical(
            "Database initialization failed",
            extra={"stage": "startup", "video_id": "", "error": str(exc)},
        )
        return 1

    logger.info(
        "Infrastructure ready",
        extra={
            "stage": "startup",
            "video_id": "",
            "database": db_path,
            "video_path": video_path,
            "stages": len(PIPELINE_STAGES),
        },
    )

    # Run pipeline through implemented stages
    adapter = DatabaseAdapter(conn)
    orchestrator = Orchestrator(config=config, adapter=adapter, video_path=video_path)
    result = orchestrator.run()

    conn.close()

    if result is None:
        logger.critical(
            "Pipeline execution failed",
            extra={"stage": "startup", "video_id": ""},
        )
        return 1

    logger.info(
        "Pipeline finished",
        extra={
            "stage": "startup",
            "video_id": result.video_id,
            "scene_count": len(result.scene_list.scenes),
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
