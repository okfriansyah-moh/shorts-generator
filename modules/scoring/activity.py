"""Scene activity scoring — per-scene frame differencing via FFmpeg."""
from __future__ import annotations

import logging
import subprocess

from contracts.scene import SceneList

logger = logging.getLogger(__name__)

# Fallback constants used only when config is unavailable.
_DEFAULT_FRAME_WIDTH = 64
_DEFAULT_FRAME_HEIGHT = 36
_DEFAULT_FRAME_SIZE = _DEFAULT_FRAME_WIDTH * _DEFAULT_FRAME_HEIGHT
_DEFAULT_FFMPEG_TIMEOUT = 30


def compute_scene_activities(
    scene_list: SceneList,
    file_path: str,
    config: dict | None = None,
) -> dict[str, float]:
    """Compute raw scene activity for every scene via frame differencing.

    Returns a dict mapping scene_id -> raw average pixel difference (before
    video-wide min-max normalisation). Scenes that fail return 0.0.
    """
    scoring_cfg = (config or {}).get("scoring", {})
    frame_w = int(scoring_cfg.get("activity_frame_width", _DEFAULT_FRAME_WIDTH))
    frame_h = int(scoring_cfg.get("activity_frame_height", _DEFAULT_FRAME_HEIGHT))
    timeout = int(scoring_cfg.get("scoring_ffmpeg_timeout", _DEFAULT_FFMPEG_TIMEOUT))
    frame_size = frame_w * frame_h

    activities: dict[str, float] = {}
    for scene in scene_list.scenes:
        start_s = scene.start_time / 1000.0
        duration_s = scene.duration
        try:
            activity = _compute_single_scene_activity(file_path, start_s, duration_s, frame_w, frame_h, frame_size, timeout)
        except Exception as exc:
            logger.warning(
                "Scene activity computation failed — using 0.0",
                extra={
                    "scene_id": scene.scene_id,
                    "file_path": file_path,
                    "error": str(exc),
                    "stage": "scoring",
                },
            )
            activity = 0.0
        activities[scene.scene_id] = activity
    return activities


def _compute_single_scene_activity(
    file_path: str,
    start_s: float,
    duration_s: float,
    frame_w: int = _DEFAULT_FRAME_WIDTH,
    frame_h: int = _DEFAULT_FRAME_HEIGHT,
    frame_size: int = _DEFAULT_FRAME_SIZE,
    timeout: int = _DEFAULT_FFMPEG_TIMEOUT,
) -> float:
    """Compute average inter-frame pixel difference for a scene segment.

    Uses FFmpeg to extract grayscale frames at low resolution, then computes
    the mean absolute difference between consecutive frames.
    Returns 0.0 when fewer than two frames are available.
    Raises RuntimeError on FFmpeg non-zero exit code (caught by caller).
    """
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_s:.3f}",
        "-i", file_path,
        "-t", f"{duration_s:.3f}",
        "-vf", f"scale={frame_w}:{frame_h}",
        "-vsync", "vfr",
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

    raw = result.stdout
    if len(raw) < 2 * frame_size:
        return 0.0

    n_frames = len(raw) // frame_size
    diffs: list[float] = []
    for i in range(1, n_frames):
        prev = raw[(i - 1) * frame_size : i * frame_size]
        curr = raw[i * frame_size : (i + 1) * frame_size]
        diff = sum(abs(a - b) for a, b in zip(prev, curr)) / frame_size
        diffs.append(diff)

    return sum(diffs) / len(diffs) if diffs else 0.0
