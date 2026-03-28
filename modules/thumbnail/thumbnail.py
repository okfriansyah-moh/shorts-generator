"""Thumbnail generation module — extracts best frame and adds hook text overlay.

Uses FFmpeg to extract a frame at the hook moment (15% into clip), applies
visual enhancement (saturation, contrast), and burns in a text overlay.
Output is a 1280×720 JPEG.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile

from contracts.clip import ClipDefinition
from contracts.face import FaceDetectionResult
from contracts.hook import HookResult
from contracts.ingestion import IngestionResult
from contracts.thumbnail import ThumbnailResult

logger = logging.getLogger(__name__)


def _run_ffmpeg(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Run FFmpeg with -y flag, capture output, and enforce timeout."""
    cmd = ["ffmpeg", "-y"] + args
    logger.debug("FFmpeg command: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg error (exit {result.returncode}): {result.stderr[:300]}"
        )
    return result


def _select_timestamp(clip: ClipDefinition) -> float:
    """Return thumbnail frame timestamp (seconds) at 15% into clip (hook moment).

    Deterministic: same clip always produces the same timestamp.
    """
    start_s = clip.start_time / 1000.0
    duration_s = (clip.end_time - clip.start_time) / 1000.0
    return start_s + duration_s * 0.15


def _select_timestamp_ms(clip: ClipDefinition) -> int:
    """Return thumbnail frame timestamp in milliseconds at 15% into clip."""
    duration_ms = clip.end_time - clip.start_time
    return clip.start_time + int(duration_ms * 0.15)


def _has_face(face_result: FaceDetectionResult | None) -> bool:
    """Return True if any face was detected in the clip."""
    if face_result is None:
        return False
    return any(
        len(sd.bounding_boxes) > 0
        for sd in face_result.scene_data
    )


def _build_text_overlay(hook_result: HookResult, max_words: int) -> str:
    """Return uppercase hook words truncated to max_words."""
    words = hook_result.hook_text.split()[:max_words]
    return " ".join(words).upper()


def _build_vf_filter(
    text_file: str,
    saturation: float,
    contrast: float,
    font_size: int,
) -> str:
    """Build FFmpeg -vf filter chain for thumbnail generation."""
    return (
        "scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,"
        f"eq=saturation={saturation:.4f}:contrast={contrast:.4f},"
        f"drawtext=textfile={text_file}:fontsize={font_size}:"
        "fontcolor=white:bordercolor=black:borderw=4:"
        "x=(w-text_w)/2:y=h-text_h-40"
    )


def _jpeg_quality_to_qscale(quality: int) -> int:
    """Convert JPEG quality percentage (1-100) to FFmpeg q:v scale (1-31).

    FFmpeg's -q:v 1 = best quality, 31 = worst.
    q = round(1 + (100 - quality) / (100 / 30))
    """
    return max(1, min(31, round(1 + (100 - quality) * 30 / 100)))


def process(
    clip: ClipDefinition,
    face_result: FaceDetectionResult | None,
    hook_result: HookResult,
    ingestion_result: IngestionResult,
    config: dict,
    output_dir: str,
) -> ThumbnailResult:
    """Generate a 1280×720 JPEG thumbnail for the clip.

    Extracts a frame at 15% into the clip (hook moment), applies saturation
    and contrast enhancement, burns in the hook text as an overlay, and saves
    as JPEG quality 90.

    Idempotent: if the output JPEG already exists, returns cached result
    without re-running FFmpeg.

    Args:
        clip: ClipDefinition DTO for the clip being processed.
        face_result: Face detection results (optional, not used for frame
            selection but accepted to match orchestrator call signature).
        hook_result: Hook text for overlay. Up to max_text_words used.
        ingestion_result: Source video metadata (path used for frame extraction).
        config: Full pipeline config dict.
        output_dir: Root output directory (e.g. ``output/``).

    Returns:
        ThumbnailResult DTO with path to the generated thumbnail.

    Raises:
        RuntimeError: If FFmpeg fails or produces an invalid output file.
    """
    thumb_cfg = config.get("thumbnail", {})
    max_words = thumb_cfg.get("max_text_words", 3)
    saturation = thumb_cfg.get("saturation_boost", 1.15)
    contrast = thumb_cfg.get("contrast_boost", 1.10)
    font_size = thumb_cfg.get("font_size", 72)
    quality = thumb_cfg.get("quality", 90)

    thumbnail_dir = os.path.join(output_dir, clip.video_id, "thumbnails")
    os.makedirs(thumbnail_dir, exist_ok=True)
    output_path = os.path.join(thumbnail_dir, f"shorts-{clip.clip_index + 1}.jpg")

    # Idempotency: return cached result if already generated.
    if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
        text_overlay = _build_text_overlay(hook_result, max_words)
        logger.info(
            "Thumbnail already exists; returning cached result",
            extra={
                "clip_id": clip.clip_id,
                "video_id": clip.video_id,
                "stage": "thumbnail",
                "status": "cached",
            },
        )
        return ThumbnailResult(
            clip_id=clip.clip_id,
            image_path=output_path,
            resolution=(1280, 720),
            text_overlay=text_overlay,
            face_visible=_has_face(face_result),
            frame_timestamp_ms=_select_timestamp_ms(clip),
            frame_score=0.0,
        )

    timestamp = _select_timestamp(clip)
    text_overlay = _build_text_overlay(hook_result, max_words)
    q_scale = _jpeg_quality_to_qscale(quality)

    tmp_text_file = None
    try:
        # Write text to a temp file to avoid shell-escaping issues in drawtext.
        fd, tmp_text_file = tempfile.mkstemp(suffix=".txt", prefix="thumb_text_")
        with os.fdopen(fd, "w") as fh:
            fh.write(text_overlay)

        vf = _build_vf_filter(tmp_text_file, saturation, contrast, font_size)

        _run_ffmpeg([
            "-ss", f"{timestamp:.3f}",
            "-i", ingestion_result.path,
            "-frames:v", "1",
            "-vf", vf,
            "-q:v", str(q_scale),
            output_path,
        ])
    finally:
        if tmp_text_file and os.path.exists(tmp_text_file):
            os.unlink(tmp_text_file)

    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Thumbnail generation produced no output: {output_path}")

    logger.info(
        "Thumbnail generated",
        extra={
            "clip_id": clip.clip_id,
            "video_id": clip.video_id,
            "stage": "thumbnail",
            "status": "ok",
            "output_path": output_path,
        },
    )
    return ThumbnailResult(
        clip_id=clip.clip_id,
        image_path=output_path,
        resolution=(1280, 720),
        text_overlay=text_overlay,
        face_visible=_has_face(face_result),
        frame_timestamp_ms=_select_timestamp_ms(clip),
        frame_score=0.0,
    )
