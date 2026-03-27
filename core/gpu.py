"""GPU configuration resolver for NVIDIA optimized mode.

Centralizes all GPU-related configuration resolution so that individual
modules only need to call resolve_gpu_settings(config) to get the correct
encoding/transcription parameters for either CPU or GPU mode.

The gpu section in config.yaml is entirely optional. When absent or
gpu.enabled is False, all settings default to CPU-only values.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Default GPU configuration — matches config.yaml gpu section
_GPU_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "encoder": "h264_nvenc",
    "preset": "p4",
    "rc": "vbr",
    "cq": 20,
    "cq_fallback": 28,
    "spatial_aq": 1,
    "temporal_aq": 1,
    "aq_strength": 8,
    "rc_lookahead": 20,
    "maxrate": "10M",
    "bufsize": "20M",
    "profile": "high",
    "pix_fmt": "yuv420p",
    "transcription_device": "cuda",
    "transcription_compute_type": "float16",
}


def resolve_gpu_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve GPU settings from config, applying defaults for missing keys.

    Args:
        config: Full pipeline configuration dict.

    Returns:
        Dict with resolved GPU settings including:
            - enabled: bool
            - transcription_device: str ("cpu" or "cuda")
            - transcription_compute_type: str ("int8" or "float16")
            - ffmpeg_encoder: str (codec name)
            - ffmpeg_encode_args: list[str] (primary quality encoding args)
            - ffmpeg_encode_args_fallback: list[str] (lower quality fallback)
    """
    gpu_cfg = config.get("gpu", {})
    enabled = bool(gpu_cfg.get("enabled", _GPU_DEFAULTS["enabled"]))

    if not enabled:
        return _cpu_settings(config)

    return _gpu_settings(gpu_cfg, config)


def _cpu_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Return CPU-only settings derived from existing renderer config."""
    renderer_cfg = config.get("renderer", {})
    codec = renderer_cfg.get("codec", "libx264")
    crf = renderer_cfg.get("crf", 20)
    preset = renderer_cfg.get("preset", "medium")

    return {
        "enabled": False,
        "transcription_device": "cpu",
        "transcription_compute_type": "int8",
        "ffmpeg_encoder": codec,
        "ffmpeg_encode_args": [
            "-c:v", codec,
            "-crf", str(crf),
            "-preset", preset,
            "-profile:v", "high",
        ],
        "ffmpeg_encode_args_fallback": [
            "-c:v", codec,
            "-crf", str(crf + 8),
            "-preset", "fast",
            "-profile:v", "high",
        ],
    }


def _gpu_settings(gpu_cfg: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Return NVIDIA GPU-accelerated settings."""

    def _get(key: str) -> Any:
        return gpu_cfg.get(key, _GPU_DEFAULTS[key])

    encoder = _get("encoder")
    cq = int(_get("cq"))
    cq_fallback = int(_get("cq_fallback"))
    preset = _get("preset")
    rc = _get("rc")
    spatial_aq = int(_get("spatial_aq"))
    temporal_aq = int(_get("temporal_aq"))
    aq_strength = int(_get("aq_strength"))
    rc_lookahead = int(_get("rc_lookahead"))
    maxrate = _get("maxrate")
    bufsize = _get("bufsize")
    profile = _get("profile")
    pix_fmt = _get("pix_fmt")

    primary_args = [
        "-c:v", encoder,
        "-preset", preset,
        "-rc", rc,
        "-cq", str(cq),
        "-spatial_aq", str(spatial_aq),
        "-temporal_aq", str(temporal_aq),
        "-aq-strength", str(aq_strength),
        "-rc-lookahead", str(rc_lookahead),
        "-maxrate", maxrate,
        "-bufsize", bufsize,
        "-profile:v", profile,
        "-pix_fmt", pix_fmt,
    ]

    fallback_args = [
        "-c:v", encoder,
        "-preset", "p1",
        "-rc", rc,
        "-cq", str(cq_fallback),
        "-profile:v", profile,
        "-pix_fmt", pix_fmt,
    ]

    logger.debug(
        "GPU mode enabled",
        extra={
            "stage": "startup",
            "video_id": "",
            "encoder": encoder,
            "preset": preset,
            "cq": cq,
        },
    )

    return {
        "enabled": True,
        "transcription_device": _get("transcription_device"),
        "transcription_compute_type": _get("transcription_compute_type"),
        "ffmpeg_encoder": encoder,
        "ffmpeg_encode_args": primary_args,
        "ffmpeg_encode_args_fallback": fallback_args,
    }
