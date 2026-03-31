"""Main composition logic: face + gameplay → 9:16 vertical composite.

Receives ClipDefinition, FaceDetectionResult, and IngestionResult.
Determines layout mode, builds FFmpeg filter chain, and produces
a silent intermediate composite video at 1080×1920.

Layout rules (configurable via compositor.default_layout):
  - "split" (default): gameplay top (65%) + face/reaction bottom (35%)
    Gameplay occupies the upper portion; face cam occupies the lower portion.
    Uses detected face bbox when available; falls back to a
    configurable source region (compositor.face_region) when
    face detection has no valid bbox.
  - "gameplay_only": full-gameplay with blurred background fill.
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

from core.gpu import resolve_gpu_settings

from .face_crop import build_face_crop_filter, estimate_pip_region
from .fallback import build_fallback_filter, build_fallback_filter_simple
from .gameplay_crop import build_gameplay_crop_filter

logger = logging.getLogger(__name__)

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
GAMEPLAY_HEIGHT = 1248   # 65% of 1920 — gameplay section (top)
FACE_HEIGHT = 672        # 35% of 1920 — face/reaction section (bottom)
FACE_VISIBILITY_THRESHOLD = 0.3
DEFAULT_ZOOM_FACTOR = 1.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_output_path(clip: ClipDefinition, config: dict) -> str:
    """Construct the output path for the composite video file.

    Uses ``shorts-{N}`` (1-based) directory naming derived from
    ``clip.clip_index`` for user-friendly output folders.
    """
    output_dir = config.get("paths", {}).get("output_dir", "output")
    video_dir_name = config.get("_runtime", {}).get("video_dir_name", clip.video_id)
    folder_name = f"shorts-{clip.clip_index + 1}"
    clip_dir = os.path.join(
        os.path.abspath(output_dir), video_dir_name, "clips", folder_name
    )
    os.makedirs(clip_dir, exist_ok=True)
    return os.path.join(clip_dir, "composite.mp4")


def _infer_face_bbox(
    compositor_config: dict,
    face_result: Optional[FaceDetectionResult] = None,
) -> FaceBBox:
    """Infer a face bounding box for PiP cropping.

    Resolution order:
      1. If face_region == "auto" and face_result has an estimated_pip_bbox
         (video-level aggregate or skin-tone scan), use that.
      2. If face_region is a named position, use the predefined coordinates.
      3. If face_region == "auto" with no detection data, fall back to
         "top_left" (face cam is typically in the upper-left of the source).
    """
    region = compositor_config.get("face_region", "auto")

    if region == "auto":
        if face_result is not None and face_result.estimated_pip_bbox is not None:
            return face_result.estimated_pip_bbox
        # No detection data — default to top_left (face cam overlay in source)
        region = "top_left"

    # Named PiP positions covering all common face cam placements.
    # Widths are 30-35% of frame to match real PiP overlays (not just face area).
    region_map = {
        "bottom_left": (0.0, 0.60, 0.30, 0.40),
        "bottom_center": (0.35, 0.60, 0.30, 0.40),
        "bottom_middle": (0.35, 0.60, 0.30, 0.40),
        "bottom_right": (0.70, 0.60, 0.30, 0.40),
        "middle_left": (0.0, 0.30, 0.30, 0.40),
        "middle_right": (0.70, 0.30, 0.30, 0.40),
        "upper_middle_left": (0.0, 0.10, 0.30, 0.40),
        "center": (0.25, 0.25, 0.50, 0.50),
        "top_left": (0.0, 0.0, 0.30, 0.40),
        "top_right": (0.70, 0.0, 0.30, 0.40),
    }
    x, y, w, h = region_map.get(region, region_map["bottom_left"])
    return FaceBBox(
        x=x, y=y, width=w, height=h, confidence=1.0, timestamp_ms=0,
    )


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
    logger.info(
        "Running FFmpeg compositor",
        extra={
            "stage": "compositor",
            "video_id": "",
            "timeout": timeout,
        },
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        logger.error(
            "FFmpeg compositor exited with error",
            extra={
                "stage": "compositor",
                "video_id": "",
                "exit_code": result.returncode,
                "stderr": result.stderr[-1500:],
            },
        )
        raise RuntimeError(
            f"FFmpeg failed (exit {result.returncode}): {result.stderr[-1500:]}"
        )


def _atomic_ffmpeg(args: list[str], output_path: str, timeout: int = 300) -> None:
    """Run FFmpeg writing to a .tmp file then atomically rename on success."""
    base, ext = os.path.splitext(output_path)
    tmp_path = f"{base}.tmp{ext}"
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
    config: dict,
    zoom_factor: float = 1.5,
) -> None:
    """Produce split gameplay/face composite with face zoom via FFmpeg."""
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0

    face_filter = build_face_crop_filter(
        "[fc_in]", "[face]", bbox, src_width, src_height, zoom_factor,
    )
    gameplay_filter = build_gameplay_crop_filter(
        "[gp_in]", "[gameplay]", OUTPUT_WIDTH, GAMEPLAY_HEIGHT,
        bbox=bbox, src_width=src_width, src_height=src_height,
    )
    # gameplay (top, 65%) + face/reaction (bottom, 35%)
    filter_complex = (
        f"[0:v]split=2[gp_in][fc_in];"
        f"{gameplay_filter};"
        f"{face_filter};"
        f"[gameplay][face]vstack=inputs=2,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=disable,"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1[v]"
    )

    gpu_settings = resolve_gpu_settings(config)
    args = [
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", source_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-an",
    ] + gpu_settings["ffmpeg_encode_args"] + [
        "-r", str(fps),
        output_path,
    ]
    _atomic_ffmpeg(args, output_path, timeout=timeout)


def _compose_split_layout_simple(
    source_path: str,
    output_path: str,
    clip: ClipDefinition,
    bbox: FaceBBox,
    src_width: int,
    src_height: int,
    fps: int,
    timeout: int,
    config: dict,
) -> None:
    """Simplified split layout used on first retry — same PiP crop, no zoom trim."""
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0

    # Use the PiP bbox directly with zoom=1.0 (no inward trim)
    face_filter = build_face_crop_filter(
        "[fc_in]", "[face]", bbox, src_width, src_height, zoom=1.0,
    )
    gameplay_filter = build_gameplay_crop_filter(
        "[gp_in]", "[gameplay]", OUTPUT_WIDTH, GAMEPLAY_HEIGHT,
        bbox=bbox, src_width=src_width, src_height=src_height,
    )
    # gameplay (top, 65%) + face/reaction (bottom, 35%)
    filter_complex = (
        f"[0:v]split=2[gp_in][fc_in];"
        f"{gameplay_filter};"
        f"{face_filter};"
        f"[gameplay][face]vstack=inputs=2,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=disable,"
        f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1[v]"
    )

    gpu_settings = resolve_gpu_settings(config)
    args = [
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", source_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-an",
    ] + gpu_settings["ffmpeg_encode_args_fallback"] + [
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
    config: dict,
) -> None:
    """Produce full-gameplay fallback layout with Ken Burns effect."""
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0

    filter_complex = build_fallback_filter("[0:v]", "[v]", clip.duration, fps)

    gpu_settings = resolve_gpu_settings(config)
    args = [
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", source_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-an",
    ] + gpu_settings["ffmpeg_encode_args"] + [
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
    config: dict,
) -> None:
    """Simplified fallback layout (no zoompan) used on first retry."""
    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0

    filter_complex = build_fallback_filter_simple("[0:v]", "[v]")

    gpu_settings = resolve_gpu_settings(config)
    args = [
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", source_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-an",
    ] + gpu_settings["ffmpeg_encode_args_fallback"] + [
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
    compositor_config = config.get("compositor", {})
    fps = int(pipeline_config.get("output_framerate", 30))
    ffmpeg_timeout = int(pipeline_config.get("ffmpeg_timeout", 300))
    zoom_factor = float(compositor_config.get("face_zoom_factor", DEFAULT_ZOOM_FACTOR))

    source_path = ingestion_result.path
    src_width, src_height = ingestion_result.resolution
    output_path = _get_output_path(clip, config)

    # Layout selection: honour explicit config, then face detection
    default_layout = compositor_config.get("default_layout", "split")

    scene_face_data = _get_clip_scenes_face_data(clip, face_result)
    avg_visibility = _compute_average_face_visibility(scene_face_data)
    detected_bbox = _pick_representative_bbox(scene_face_data) if scene_face_data else None

    if default_layout == "gameplay_only":
        # Explicitly requested gameplay-only mode
        has_face = False
        layout = "gameplay_only_zoom"
    elif avg_visibility >= FACE_VISIBILITY_THRESHOLD and detected_bbox is not None:
        # Face detection provided a confident bbox
        has_face = True
        layout = "face_gameplay_split"
    else:
        # Default split: use detected bbox if available, otherwise
        # infer face region from compositor.face_region config
        has_face = True
        layout = "face_gameplay_split"

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
        bbox = detected_bbox
        if bbox is None:
            # No per-scene bbox — use video-level PiP estimate (from region
            # voting) or config fallback
            bbox = _infer_face_bbox(compositor_config, face_result)
        elif bbox.width < 0.15 or bbox.height < 0.18:
            # Tiny face bbox from MediaPipe — expand to full PiP overlay
            bbox = estimate_pip_region(bbox, src_width, src_height)
        # else: bbox is already a full PiP region from region voting
        try:
            _compose_split_layout(
                source_path, output_path, clip,
                bbox, src_width, src_height, fps, ffmpeg_timeout,
                config, zoom_factor,
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
                bbox, src_width, src_height, fps, ffmpeg_timeout,
                config,
            )

    if not has_face:
        try:
            _compose_fallback_layout(
                source_path, output_path, clip, fps, ffmpeg_timeout,
                config,
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
                source_path, output_path, clip, fps, ffmpeg_timeout,
                config,
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
