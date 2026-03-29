"""Scene image quality scoring via Laplacian variance (sharpness detection).

Extracts a few frames per scene using FFmpeg, computes Laplacian variance
(a proxy for focus / sharpness), and returns raw quality values.
Higher variance = sharper image = better visual quality for clips.

Falls back to 0.0 on any error without crashing the pipeline.
"""

from __future__ import annotations

import logging
import struct
import subprocess

from contracts.scene import SceneList

logger = logging.getLogger(__name__)

# Frame dimensions used for quality computation (low-res for performance).
_FRAME_WIDTH = 320
_FRAME_HEIGHT = 180
_FRAME_SIZE = _FRAME_WIDTH * _FRAME_HEIGHT
_FFMPEG_TIMEOUT = 30


def compute_scene_qualities(
    scene_list: SceneList,
    file_path: str,
) -> dict[str, float]:
    """Compute raw image quality for every scene via Laplacian variance.

    Args:
        scene_list: Full scene list for the video.
        file_path: Absolute path to the source video file.

    Returns:
        Dict mapping scene_id -> raw Laplacian variance (before
        video-wide min-max normalisation). Scenes that fail return 0.0.
    """
    qualities: dict[str, float] = {}
    for scene in scene_list.scenes:
        start_s = scene.start_time / 1000.0
        duration_s = scene.duration
        try:
            quality = _compute_single_scene_quality(file_path, start_s, duration_s)
        except Exception as exc:
            logger.warning(
                "Scene quality computation failed — using 0.0",
                extra={
                    "scene_id": scene.scene_id,
                    "error": str(exc)[:200],
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
) -> float:
    """Extract 1 fps grayscale frames and compute average Laplacian variance.

    Laplacian variance measures the amount of edges/detail in an image.
    Higher values indicate sharper, higher-quality frames.

    Raises RuntimeError on FFmpeg failure so the caller can catch and fall
    back to 0.0.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-ss", str(start_s),
            "-t", str(duration_s),
            "-i", file_path,
            "-vf", f"fps=1,scale={_FRAME_WIDTH}:{_FRAME_HEIGHT}",
            "-vsync", "vfr",
            "-f", "rawvideo",
            "-pix_fmt", "gray",
            "-loglevel", "error",
            "-",
        ],
        capture_output=True,
        timeout=_FFMPEG_TIMEOUT,
    )

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr[:200]}")

    raw = result.stdout
    if len(raw) < _FRAME_SIZE:
        return 0.0

    num_frames = len(raw) // _FRAME_SIZE
    total_variance = 0.0

    for i in range(num_frames):
        offset = i * _FRAME_SIZE
        frame = raw[offset : offset + _FRAME_SIZE]
        variance = _laplacian_variance(frame, _FRAME_WIDTH, _FRAME_HEIGHT)
        total_variance += variance

    return total_variance / num_frames if num_frames > 0 else 0.0


def _laplacian_variance(
    gray_bytes: bytes,
    width: int,
    height: int,
) -> float:
    """Compute Laplacian variance of a grayscale frame (raw bytes).

    Uses a 3×3 Laplacian kernel: [0,1,0],[1,-4,1],[0,1,0]
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
