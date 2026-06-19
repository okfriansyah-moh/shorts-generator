"""Scene quality scoring — image sharpness via Laplacian variance."""
from __future__ import annotations

import logging
import struct
import subprocess

from contracts.scene import SceneList

logger = logging.getLogger(__name__)

# Fallback constants used only when config is unavailable.
_DEFAULT_FRAME_WIDTH = 320
_DEFAULT_FRAME_HEIGHT = 180
_DEFAULT_FFMPEG_TIMEOUT = 30


def compute_scene_qualities(
    scene_list: SceneList,
    file_path: str,
    config: dict | None = None,
) -> dict[str, float]:
    """Compute raw image quality for every scene via Laplacian variance.

    Returns a dict mapping scene_id -> raw Laplacian variance (before
    video-wide min-max normalisation). Scenes that fail return 0.0.
    """
    scoring_cfg = (config or {}).get("scoring", {})
    frame_w = int(scoring_cfg.get("quality_frame_width", _DEFAULT_FRAME_WIDTH))
    frame_h = int(scoring_cfg.get("quality_frame_height", _DEFAULT_FRAME_HEIGHT))
    timeout = int(scoring_cfg.get("scoring_ffmpeg_timeout", _DEFAULT_FFMPEG_TIMEOUT))

    qualities: dict[str, float] = {}
    for scene in scene_list.scenes:
        start_s = scene.start_time / 1000.0
        duration_s = scene.duration
        try:
            quality = _compute_single_scene_quality(file_path, start_s, duration_s, frame_w, frame_h, timeout)
        except Exception as exc:
            logger.warning(
                "Scene quality computation failed — using 0.0",
                extra={
                    "scene_id": scene.scene_id,
                    "file_path": file_path,
                    "error": str(exc),
                    "stage": "scoring",
                },
            )
            quality = 0.0
        qualities[scene.scene_id] = quality
    return qualities


def _compute_single_scene_quality(
    file_path: str,
    start_s: float,
    duration_s: float,
    frame_w: int = _DEFAULT_FRAME_WIDTH,
    frame_h: int = _DEFAULT_FRAME_HEIGHT,
    timeout: int = _DEFAULT_FFMPEG_TIMEOUT,
) -> float:
    """Extract a representative grayscale frame and compute Laplacian variance.

    Raises RuntimeError on FFmpeg failure (caught by caller).
    Returns 0.0 when no frame is extractable.
    """
    midpoint = start_s + duration_s / 2.0
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{midpoint:.3f}",
        "-i", file_path,
        "-vframes", "1",
        "-vf", f"scale={frame_w}:{frame_h}",
        "-f", "rawvideo",
        "-pix_fmt", "gray",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg exited with code {result.returncode}: "
            f"{result.stderr.decode(errors='replace')[:200]}"
        )

    return _laplacian_variance(result.stdout, frame_w, frame_h)


def _laplacian_variance(gray_bytes: bytes, width: int, height: int) -> float:
    """Compute Laplacian variance of a grayscale frame (raw bytes).

    Uses a 3x3 Laplacian kernel: [0,1,0],[1,-4,1],[0,1,0]
    applied to the interior pixels (excluding edges).
    This is a pure-Python implementation to avoid requiring OpenCV.
    """
    if len(gray_bytes) != width * height:
        return 0.0

    pixels = struct.unpack(f"{width * height}B", gray_bytes)
    total = 0.0
    count = 0
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            idx = y * width + x
            lap = (
                -4 * pixels[idx]
                + pixels[idx - 1]
                + pixels[idx + 1]
                + pixels[idx - width]
                + pixels[idx + width]
            )
            total += lap * lap
            count += 1
    return total / count if count > 0 else 0.0
