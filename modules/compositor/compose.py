"""Main composition logic: face + gameplay → 9:16 vertical composite.

Receives ClipDefinition, FaceDetectionResult, and IngestionResult.
Determines layout mode, builds FFmpeg filter chain, and produces
a silent intermediate composite video at 1080×1920.

Layout rules:
  - avg face_visible_ratio >= 0.3 across clip's scenes → face_gameplay_split
  - avg face_visible_ratio <  0.3                      → gameplay_only_zoom
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional

from contracts.clip import ClipDefinition
from contracts.compositor import CompositeStream
from contracts.face import FaceBBox, FaceDetectionResult, SceneFaceData
from contracts.ingestion import IngestionResult

from .face_crop import build_face_crop_filter
from .fallback import build_fallback_filter, build_fallback_filter_simple
from .gameplay_crop import build_gameplay_crop_filter

logger = logging.getLogger(__name__)

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
GAMEPLAY_HEIGHT = 1248   # 65% of 1920
FACE_HEIGHT = 672        # 35% of 1920
FACE_VISIBILITY_THRESHOLD = 0.3
ZOOM_FACTOR = 1.2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_output_path(clip: ClipDefinition, config: dict) -> str:
    """Construct the output path for the composite video file."""
    output_dir = config.get("paths", {}).get("output_dir", "output")
    clip_dir = os.path.join(
        os.path.abspath(output_dir), clip.video_id, "clips", clip.clip_id
    )
    os.makedirs(clip_dir, exist_ok=True)
    return os.path.join(clip_dir, "composite.mp4")


def _get_clip_scenes_face_data(
    clip: ClipDefinition,
    face_result: FaceDetectionResult,
) -> list[SceneFaceData]:
    """Return face data entries whose scene_id matches any of the clip's scenes."""
    clip_scene_ids = {s.scene_id for s in clip.scenes}
    return [fd for fd in face_result.scene_data if fd.scene_id in clip_scene_ids]


def _compute_average_face_visibility(scene_face_data: list[SceneFaceData]) -> float:
    """Compute mean face_visible_ratio across provided scenes."""
    if not scene_face_data:
        return 0.0
    return sum(s.face_visible_ratio for s in scene_face_data) / len(scene_face_data)


def _pick_representative_bbox(
    scene_face_data: list[SceneFaceData],
) -> Optional[FaceBBox]:
    """Pick the EMA-smoothed bbox from the scene with highest face visibility.

    Sorted by (-face_visible_ratio, scene_id) for determinism.
    Returns None when no scene has a valid average_bbox.
    """
    candidates = [
        s
        for s in sorted(
            scene_face_data,
            key=lambda s: (-s.face_visible_ratio, s.scene_id),
        )
        if s.average_bbox is not None
    ]
    return candidates[0].average_bbox if candidates else None


def _run_ffmpeg(args: list[str], timeout: int = 300) -> None:
    """Execute an FFmpeg command, raising RuntimeError on failure."""
    cmd = ["ffmpeg", "-y"] + args
    logger.debug("FFmpeg command: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed (exit {result.returncode}): {result.stderr[:500]}"
        )


def _atomic_ffmpeg(args: list[str], output_path: str, timeout: int = 300) -> None:
    """Run FFmpeg writing to a .tmp file then atomically rename on success."""
    tmp_path = output_path + ".tmp"
    patched = [tmp_path if a == output_path else a for a in args]
    try:
        _run_ffmpeg(patched, timeout=timeout)
        os.replace(tmp_path, output_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ---------------------------------------------------------------------------
# Layout composers
# ---------------------------------------------------------------------------


def _compose_split_layout(
    source_path: str,
    output_path: str,
    clip: ClipDefinition,
    bbox: FaceBBox,
    src_width: int,
    src_height: int,
    fps: int,
    timeout: int,
) -> None:
    """Produce split gameplay/face composite with face zoom via FFmpeg."""
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0

    gameplay_filter = build_gameplay_crop_filter(
        "[0:v]", "[gameplay]", OUTPUT_WIDTH, GAMEPLAY_HEIGHT
    )
    face_filter = build_face_crop_filter(
        "[0:v]", "[face]", bbox, src_width, src_height, ZOOM_FACTOR
    )
    filter_complex = (
        f"{gameplay_filter};"
        f"{face_filter};"
        f"[gameplay][face]vstack=inputs=2[v]"
    )

    args = [
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", source_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-an",
        "-c:v", "libx264",
        "-crf", "20",
        "-preset", "medium",
        "-r", str(fps),
        output_path,
    ]
    _atomic_ffmpeg(args, output_path, timeout=timeout)


def _compose_split_layout_simple(
    source_path: str,
    output_path: str,
    clip: ClipDefinition,
    src_width: int,
    src_height: int,
    fps: int,
    timeout: int,
) -> None:
    """Simplified split layout without zoom used on first retry."""
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0

    gameplay_filter = build_gameplay_crop_filter(
        "[0:v]", "[gameplay]", OUTPUT_WIDTH, GAMEPLAY_HEIGHT
    )
    # Center-crop to 1080×672 aspect without bbox zoom
    face_crop_w = min(src_width, int(src_height * OUTPUT_WIDTH / FACE_HEIGHT))
    face_crop_h = min(src_height, int(face_crop_w * FACE_HEIGHT / OUTPUT_WIDTH))
    face_x = max(0, (src_width - face_crop_w) // 2)
    face_y = max(0, (src_height - face_crop_h) // 2)
    face_filter = (
        f"[0:v]"
        f"crop={face_crop_w}:{face_crop_h}:{face_x}:{face_y},"
        f"scale={OUTPUT_WIDTH}:{FACE_HEIGHT}"
        f"[face]"
    )
    filter_complex = (
        f"{gameplay_filter};"
        f"{face_filter};"
        f"[gameplay][face]vstack=inputs=2[v]"
    )

    args = [
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", source_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-an",
        "-c:v", "libx264",
        "-crf", "28",
        "-preset", "fast",
        "-r", str(fps),
        output_path,
    ]
    _atomic_ffmpeg(args, output_path, timeout=timeout)


def _compose_fallback_layout(
    source_path: str,
    output_path: str,
    clip: ClipDefinition,
    fps: int,
    timeout: int,
) -> None:
    """Produce full-gameplay fallback layout with Ken Burns effect."""
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0

    filter_complex = build_fallback_filter("[0:v]", "[v]", clip.duration, fps)

    args = [
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", source_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-an",
        "-c:v", "libx264",
        "-crf", "20",
        "-preset", "medium",
        "-r", str(fps),
        output_path,
    ]
    _atomic_ffmpeg(args, output_path, timeout=timeout)


def _compose_fallback_layout_simple(
    source_path: str,
    output_path: str,
    clip: ClipDefinition,
    fps: int,
    timeout: int,
) -> None:
    """Simplified fallback layout (no zoompan) used on first retry."""
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0

    filter_complex = build_fallback_filter_simple("[0:v]", "[v]")

    args = [
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", source_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-an",
        "-c:v", "libx264",
        "-crf", "28",
        "-preset", "fast",
        "-r", str(fps),
        output_path,
    ]
    _atomic_ffmpeg(args, output_path, timeout=timeout)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def process(
    clip: ClipDefinition,
    face_result: FaceDetectionResult,
    ingestion_result: IngestionResult,
    config: dict,
) -> CompositeStream:
    """Compose a clip into a silent 9:16 vertical composite video.

    Determines layout from face visibility, invokes FFmpeg, and returns
    a CompositeStream DTO pointing to the intermediate composite file.

    Idempotent: if the output file already exists, returns cached result.

    Args:
        clip: Clip definition with scene references and timing.
        face_result: Face detection output for the full video.
        ingestion_result: Source video metadata (path, resolution, fps).
        config: Full pipeline configuration dict.

    Returns:
        CompositeStream DTO with output_path set to the composite MP4.

    Raises:
        RuntimeError: If composition fails after one retry.
    """
    pipeline_config = config.get("pipeline", {})
    fps = int(pipeline_config.get("output_framerate", 30))
    ffmpeg_timeout = int(pipeline_config.get("ffmpeg_timeout", 300))

    source_path = ingestion_result.path
    src_width, src_height = ingestion_result.resolution
    output_path = _get_output_path(clip, config)

    scene_face_data = _get_clip_scenes_face_data(clip, face_result)
    avg_visibility = _compute_average_face_visibility(scene_face_data)
    has_face = avg_visibility >= FACE_VISIBILITY_THRESHOLD
    layout = "face_gameplay_split" if has_face else "gameplay_only_zoom"

    # Idempotency: return cached result if composite already exists
    if os.path.exists(output_path):
        logger.info(
            "Composite already exists, returning cached result",
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
        "Starting composition",
        extra={
            "clip_id": clip.clip_id,
            "video_id": clip.video_id,
            "stage": "compositor",
            "status": "started",
            "layout": layout,
            "avg_face_visibility": avg_visibility,
            "duration_ms": 0,
            "timestamp": "",
            "run_id": "",
        },
    )

    if has_face:
        bbox = _pick_representative_bbox(scene_face_data)
        if bbox is not None:
            try:
                _compose_split_layout(
                    source_path, output_path, clip,
                    bbox, src_width, src_height, fps, ffmpeg_timeout,
                )
            except RuntimeError:
                logger.warning(
                    "Split layout failed; retrying with simpler filters",
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
                _compose_split_layout_simple(
                    source_path, output_path, clip,
                    src_width, src_height, fps, ffmpeg_timeout,
                )
        else:
            # has_face=True but no valid bbox found; fall through to fallback
            has_face = False
            layout = "gameplay_only_zoom"

    if not has_face:
        try:
            _compose_fallback_layout(
                source_path, output_path, clip, fps, ffmpeg_timeout
            )
        except RuntimeError:
            logger.warning(
                "Fallback layout failed; retrying with simpler filters",
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
            _compose_fallback_layout_simple(
                source_path, output_path, clip, fps, ffmpeg_timeout
            )

    logger.info(
        "Composition complete",
        extra={
            "clip_id": clip.clip_id,
            "video_id": clip.video_id,
            "stage": "compositor",
            "status": "completed",
            "output_path": output_path,
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
