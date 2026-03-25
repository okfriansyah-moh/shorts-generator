"""Scene activity scoring via FFmpeg frame differencing.

Samples frames at 1 fps using FFmpeg, computes average pixel difference
between consecutive frames, and returns raw (pre-normalisation) values.
Falls back to 0.0 on any FFmpeg error without crashing the pipeline.
"""

from __future__ import annotations

import logging
import subprocess

from contracts.scene import SceneList

logger = logging.getLogger(__name__)


def compute_scene_activities(
    scene_list: SceneList,
    file_path: str,
) -> dict[str, float]:
    """Compute raw scene activity for every scene via frame differencing.

    Args:
        scene_list: Full scene list for the video.
        file_path: Absolute path to the source video file.

    Returns:
        Dict mapping scene_id → raw average pixel difference (before
        video-wide min-max normalisation).  Scenes that fail return 0.0.
    """
    activities: dict[str, float] = {}
    for scene in scene_list.scenes:
        start_s = scene.start_time / 1000.0
        duration_s = scene.duration
        try:
            activity = _compute_single_scene_activity(file_path, start_s, duration_s)
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


# Frame dimensions used for activity computation (low-res for performance).
_FRAME_WIDTH = 64
_FRAME_HEIGHT = 36
_FRAME_SIZE = _FRAME_WIDTH * _FRAME_HEIGHT
_FFMPEG_TIMEOUT = 30


def _compute_single_scene_activity(
    file_path: str,
    start_s: float,
    duration_s: float,
) -> float:
    """Extract 1 fps grayscale frames and return average inter-frame diff.

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
            "-",
        ],
        capture_output=True,
        timeout=_FFMPEG_TIMEOUT,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg exited with code {result.returncode}: "
            f"{result.stderr.decode(errors='replace')[:200]}"
        )

    raw = result.stdout
    if len(raw) < 2 * _FRAME_SIZE:
        return 0.0

    n_frames = len(raw) // _FRAME_SIZE
    diffs: list[float] = []
    for i in range(1, n_frames):
        prev = raw[(i - 1) * _FRAME_SIZE : i * _FRAME_SIZE]
        curr = raw[i * _FRAME_SIZE : (i + 1) * _FRAME_SIZE]
        diff = sum(abs(a - b) for a, b in zip(prev, curr)) / _FRAME_SIZE
        diffs.append(diff)

    return sum(diffs) / len(diffs) if diffs else 0.0
