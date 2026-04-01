"""Podcast composition logic: strategy-driven smart-crop wide 16:9 → vertical 9:16.

For podcast videos, the face IS the main content — there is no PiP overlay.
Composition strategy (selected by the podcast strategy module):
  - "speaker_crop":     Crop centred on the transcript-aligned primary speaker.
  - "center_face_crop": Crop centred on the largest detected face (no transcript).
  - "center_crop":      Simple center crop from 16:9 → 9:16. No face used.

The compositor is intentionally dumb: all speaker-detection and crop-plan
decisions are made by modules/strategies/podcast_strategy.py, which returns
a PodcastFramePlan. This module applies that plan via FFmpeg (pure executor).

Both strategies produce a silent 1080×1920 composite (same as gameplay path).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from contracts.clip import ClipDefinition
from contracts.compositor import CompositeStream
from contracts.face import FaceDetectionResult
from contracts.ingestion import IngestionResult
from contracts.strategies import PodcastFramePlan

from core.gpu import resolve_gpu_settings

from ._helpers import (
    get_output_path as _get_output_path,
    atomic_ffmpeg as _atomic_ffmpeg,
)

logger = logging.getLogger(__name__)

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920


# ---------------------------------------------------------------------------
# Internal helpers (compositor remains dumb — plan is already resolved)
# ---------------------------------------------------------------------------


def _build_plan_filter(plan: PodcastFramePlan) -> str:
    """Build FFmpeg filter string from a PodcastFramePlan.

    Crops source video to plan.crop_{x,y,width,height}, then scales and pads
    to the target 1080×1920 output resolution.
    """
    return (
        f"crop={plan.crop_width}:{plan.crop_height}:{plan.crop_x}:{plan.crop_y},"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1"
    )


def _build_center_crop_filter(src_width: int, src_height: int) -> str:
    """Build FFmpeg filter for simple center crop → scale to 1080×1920.

    Used as the fallback when the strategy plan itself fails at FFmpeg level.
    """
    crop_w = int(round(src_height * (9.0 / 16.0)))
    if crop_w > src_width:
        crop_w = src_width
    crop_x = (src_width - crop_w) // 2
    return (
        f"crop={crop_w}:{src_height}:{crop_x}:0,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1"
    )


# ---------------------------------------------------------------------------
# Composition functions
# ---------------------------------------------------------------------------


def _compose_with_plan(
    source_path: str,
    output_path: str,
    clip: ClipDefinition,
    plan: PodcastFramePlan,
    fps: int,
    timeout: int,
    config: dict,
) -> None:
    """Produce podcast composite by applying a PodcastFramePlan via FFmpeg.

    The compositor executes the plan without re-evaluating any decisions.
    """
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0
    vf = _build_plan_filter(plan)
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


def _compose_center_fallback(
    source_path: str,
    output_path: str,
    clip: ClipDefinition,
    src_width: int,
    src_height: int,
    fps: int,
    timeout: int,
    config: dict,
) -> None:
    """Produce podcast composite with simple center crop (FFmpeg-level fallback)."""
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0
    vf = _build_center_crop_filter(src_width, src_height)
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
# Public entry point
# ---------------------------------------------------------------------------


def process_podcast(
    clip: ClipDefinition,
    face_result: FaceDetectionResult,
    ingestion_result: IngestionResult,
    config: dict,
    plan: Optional[PodcastFramePlan] = None,
) -> CompositeStream:
    """Compose a podcast clip into a silent 9:16 vertical composite.

    The compositor is a pure executor: it applies the pre-computed
    PodcastFramePlan via FFmpeg. All decision logic lives in the orchestrator
    (which calls modules/strategies/ and passes the result as a DTO).

    Idempotent: if the output file already exists, returns cached result.

    Args:
        clip:             Clip definition with scene references and timing.
        face_result:      Face detection output for the full video.
        ingestion_result: Source video metadata (path, resolution, fps).
        config:           Full pipeline configuration dict.
        plan:             Pre-computed PodcastFramePlan DTO from the orchestrator.
                          When None (e.g. tests bypassing orchestration), falls
                          back to a simple center-crop plan.

    Returns:
        CompositeStream DTO with output_path set to the composite MP4.

    Raises:
        RuntimeError: If composition fails after retry.
    """
    pipeline_config = config.get("pipeline", {})
    fps = int(pipeline_config.get("output_framerate", 30))
    ffmpeg_timeout = int(pipeline_config.get("ffmpeg_timeout", 300))

    source_path = ingestion_result.path
    src_width, src_height = ingestion_result.resolution
    output_path = _get_output_path(clip, config)

    # ── Defensive fallback: generate center-crop plan if none was provided ──
    if plan is None:
        crop_w = int(round(src_height * (9.0 / 16.0)))
        if crop_w > src_width:
            crop_w = src_width
        crop_x = (src_width - crop_w) // 2
        plan = PodcastFramePlan(
            crop_x=crop_x, crop_y=0,
            crop_width=crop_w, crop_height=src_height,
            speaker_face_id=None, layout="center_crop",
        )
    has_face = plan.speaker_face_id is not None
    layout = plan.layout

    # Idempotency: return cached result if composite already exists
    if os.path.exists(output_path):
        logger.info(
            "Podcast composite already exists, returning cached result",
            extra={
                "clip_id": clip.clip_id,
                "video_id": clip.video_id,
                "stage": "compositor",
                "status": "cached",
                "duration_ms": 0,
                "timestamp": "",
                "run_id": "",
            },
        )
        return CompositeStream(
            clip_id=clip.clip_id,
            video_id=clip.video_id,
            composite_path=output_path,
            source_audio_path=source_path,
            resolution=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
            layout=layout,
            duration_seconds=clip.duration,
            has_face=has_face,
            source_fps=float(fps),
            start_time_ms=clip.start_time,
        )

    logger.info(
        "Starting podcast composition",
        extra={
            "clip_id": clip.clip_id,
            "video_id": clip.video_id,
            "stage": "compositor",
            "status": "started",
            "layout": layout,
            "speaker_face_id": plan.speaker_face_id,
            "duration_ms": 0,
            "timestamp": "",
            "run_id": "",
        },
    )

    # ── Execution: apply plan via FFmpeg, fallback to center crop on error ──
    try:
        _compose_with_plan(
            source_path, output_path, clip,
            plan, fps, ffmpeg_timeout, config,
        )
    except RuntimeError:
        logger.warning(
            "Podcast plan crop failed; falling back to center crop",
            extra={
                "clip_id": clip.clip_id,
                "video_id": clip.video_id,
                "stage": "compositor",
                "status": "retry",
                "duration_ms": 0,
                "timestamp": "",
                "run_id": "",
            },
        )
        layout = "center_crop"
        has_face = False
        _compose_center_fallback(
            source_path, output_path, clip,
            src_width, src_height, fps, ffmpeg_timeout, config,
        )

    logger.info(
        "Podcast composition complete",
        extra={
            "clip_id": clip.clip_id,
            "video_id": clip.video_id,
            "stage": "compositor",
            "status": "completed",
            "output_path": output_path,
            "layout": layout,
            "duration_ms": 0,
            "timestamp": "",
            "run_id": "",
        },
    )

    return CompositeStream(
        clip_id=clip.clip_id,
        video_id=clip.video_id,
        composite_path=output_path,
        source_audio_path=source_path,
        resolution=(OUTPUT_WIDTH, OUTPUT_HEIGHT),
        layout=layout,
        duration_seconds=clip.duration,
        has_face=has_face,
        source_fps=float(fps),
        start_time_ms=clip.start_time,
    )
