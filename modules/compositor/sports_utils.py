"""Universal crop filter builders for sports compositors.

Shared by all per-sport compositor modules (sports_tennis.py, sports_football.py).
Contains the three layout implementations with no sport-specific logic:

  - letterbox      — full 16:9 frame inside 9:16 with black bars top/bottom
  - center_crop    — center column cropped to 9:16, scaled to 1080×1920
  - action_crop    — crop window anchored on pre-computed SportsFramePlan coords

All filter builders return an FFmpeg -vf string (no leading/trailing semicolons).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from contracts.compositor import CompositeStream
from contracts.strategies import SportsFramePlan

from core.gpu import resolve_gpu_settings

from ._helpers import (
    get_output_path as _get_output_path,
    atomic_ffmpeg as _atomic_ffmpeg,
)

if TYPE_CHECKING:
    from contracts.clip import ClipDefinition
    from contracts.face import FaceDetectionResult
    from contracts.ingestion import IngestionResult

logger = logging.getLogger(__name__)

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920


# ---------------------------------------------------------------------------
# Filter builders (pure string functions — no I/O, no side effects)
# ---------------------------------------------------------------------------


def build_sports_letterbox_filter(
    src_width: int,
    src_height: int,
    out_width: int = OUTPUT_WIDTH,
    out_height: int = OUTPUT_HEIGHT,
) -> str:
    """Scale 16:9 source to fit inside out_width×out_height, padded with black bars."""
    return (
        f"scale={out_width}:-2:flags=lanczos,"
        f"pad={out_width}:{out_height}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1"
    )


def build_sports_center_crop_filter(
    src_width: int,
    src_height: int,
    out_width: int = OUTPUT_WIDTH,
    out_height: int = OUTPUT_HEIGHT,
) -> str:
    """Center-crop 16:9 source to 9:16, scale to out_width×out_height (no black bars)."""
    crop_w = int(round(src_height * (9.0 / 16.0)))
    if crop_w > src_width:
        crop_w = src_width
    crop_x = (src_width - crop_w) // 2
    return (
        f"crop={crop_w}:{src_height}:{crop_x}:0,"
        f"scale={out_width}:{out_height}:flags=lanczos,"
        f"setsar=1"
    )


def build_sports_action_crop_filter(
    src_width: int,
    src_height: int,
    plan: SportsFramePlan,
    out_width: int = OUTPUT_WIDTH,
    out_height: int = OUTPUT_HEIGHT,
) -> str:
    """Crop source at the action-tracked window from a SportsFramePlan, scale to out_width×out_height."""
    return (
        f"crop={plan.crop_width}:{plan.crop_height}:{plan.crop_x}:{plan.crop_y},"
        f"scale={out_width}:{out_height}:flags=lanczos,"
        f"setsar=1"
    )


# ---------------------------------------------------------------------------
# Shared composition executor
# ---------------------------------------------------------------------------


def compose_sports(
    source_path: str,
    output_path: str,
    clip: "ClipDefinition",
    vf: str,
    fps: int,
    timeout: int,
    config: dict,
) -> None:
    """Execute an FFmpeg sports composition with the given -vf filter string.

    Shared by all three layout paths — each layout builds its own vf string
    and delegates execution here. Uses atomic_ffmpeg for safe writes.
    """
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0
    gpu_settings = resolve_gpu_settings(config)
    args = [
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", source_path,
        "-vf", vf,
        "-an",
    ] + gpu_settings["ffmpeg_encode_args"] + [
        "-r", str(fps),
        output_path,
    ]
    _atomic_ffmpeg(args, output_path, timeout=timeout)


# ---------------------------------------------------------------------------
# Shared entry-point used by per-sport modules
# ---------------------------------------------------------------------------


def process_sports(
    clip: "ClipDefinition",
    face_result: "FaceDetectionResult",
    ingestion_result: "IngestionResult",
    config: dict,
    plan: SportsFramePlan | None,
    sport: str,
    default_layout: str,
) -> CompositeStream:
    """Compose a sports clip into a silent 9:16 vertical composite.

    Called by per-sport modules (process_sports_tennis, process_sports_football)
    with their sport identifier and default layout. The per-sport modules handle
    any sport-specific pre/post logic; this function is the shared executor.

    Layout resolution order:
      1. config["compositor"]["override_layout"]  — from --sports-layout CLI flag
      2. config["compositor"]["default_layout"]   — from config/config.yaml overlay
      3. default_layout argument                  — per-sport fallback

    Idempotent: returns cached CompositeStream if output already exists.
    """
    pipeline_config = config.get("pipeline", {})
    fps = int(pipeline_config.get("output_framerate", 30))
    ffmpeg_timeout = int(pipeline_config.get("ffmpeg_timeout", 300))

    compositor_config = config.get("compositor", {})
    layout = (
        compositor_config.get("override_layout")
        or compositor_config.get("default_layout")
        or default_layout
    )
    out_w = int(compositor_config.get("output_width", OUTPUT_WIDTH))
    out_h = int(compositor_config.get("output_height", OUTPUT_HEIGHT))

    source_path = ingestion_result.path
    src_width, src_height = ingestion_result.resolution
    output_path = _get_output_path(clip, config)

    if os.path.exists(output_path):
        logger.info(
            "Sports composite already exists, returning cached result",
            extra={
                "clip_id": clip.clip_id,
                "video_id": clip.video_id,
                "stage": "compositor",
                "status": "cached",
                "sport": sport,
                "layout": layout,
            },
        )
        return CompositeStream(
            clip_id=clip.clip_id,
            video_id=clip.video_id,
            composite_path=output_path,
            source_audio_path=source_path,
            resolution=(out_w, out_h),
            layout=layout,
            duration_seconds=clip.duration,
            has_face=False,
            source_fps=float(fps),
            start_time_ms=clip.start_time,
        )

    # Build the filter string for the chosen layout
    if layout == "sports_letterbox":
        vf = build_sports_letterbox_filter(src_width, src_height, out_w, out_h)
    elif layout == "sports_action_crop" and plan is not None:
        vf = build_sports_action_crop_filter(src_width, src_height, plan, out_w, out_h)
    else:
        # center_crop is the safe default for any unknown/None-plan case
        if layout == "sports_action_crop" and plan is None:
            logger.warning(
                "sports_action_crop requested but no SportsFramePlan provided; "
                "falling back to center_crop",
                extra={"clip_id": clip.clip_id, "stage": "compositor", "sport": sport},
            )
            layout = "sports_center_crop"
        vf = build_sports_center_crop_filter(src_width, src_height, out_w, out_h)

    logger.info(
        "Compositing sports clip",
        extra={
            "clip_id": clip.clip_id,
            "video_id": clip.video_id,
            "stage": "compositor",
            "sport": sport,
            "layout": layout,
            "tracking_method": plan.tracking_method if plan else "none",
        },
    )

    compose_sports(source_path, output_path, clip, vf, fps, ffmpeg_timeout, config)

    return CompositeStream(
        clip_id=clip.clip_id,
        video_id=clip.video_id,
        composite_path=output_path,
        source_audio_path=source_path,
        resolution=(out_w, out_h),
        layout=layout,
        duration_seconds=clip.duration,
        has_face=False,
        source_fps=float(fps),
        start_time_ms=clip.start_time,
    )
