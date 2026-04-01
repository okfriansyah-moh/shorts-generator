"""Shared compositor helpers used by both gameplay and podcast paths.

Contains common functions for output path construction, FFmpeg execution,
atomic file writing, and per-clip face data extraction.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.clip import ClipDefinition
    from contracts.face import FaceDetectionResult, SceneFaceData

logger = logging.getLogger(__name__)


def get_output_path(clip: "ClipDefinition", config: dict) -> str:
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


def run_ffmpeg(args: list[str], timeout: int = 300) -> None:
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


def atomic_ffmpeg(args: list[str], output_path: str, timeout: int = 300) -> None:
    """Run FFmpeg writing to a .tmp file then atomically rename on success."""
    base, ext = os.path.splitext(output_path)
    tmp_path = f"{base}.tmp{ext}"
    patched = [tmp_path if a == output_path else a for a in args]
    try:
        run_ffmpeg(patched, timeout=timeout)
        os.replace(tmp_path, output_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def get_clip_scenes_face_data(
    clip: "ClipDefinition",
    face_result: "FaceDetectionResult",
) -> list["SceneFaceData"]:
    """Return face data entries whose scene_id matches any of the clip's scenes."""
    clip_scene_ids = {s.scene_id for s in clip.scenes}
    return [fd for fd in face_result.scene_data if fd.scene_id in clip_scene_ids]
