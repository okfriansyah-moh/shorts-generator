"""Sports action-crop strategy for Shorts Factory.

Determines the best 9:16 crop anchor for a sports clip using a hybrid
tracking cascade. Each method is tried in order; the first to succeed is used:

  1. Face centroid  — reuses FaceDetectionResult (free, already computed)
  2. MediaPipe Pose — athlete body pose centroid via mediapipe.solutions.pose
  3. Motion energy  — highest-motion region via FFmpeg thumbnail + Pillow diff
  4. Center         — emergency fallback (0.5, 0.5)

Entry point:
    generate_plan(clip, face_result, ingestion_result, config) -> SportsFramePlan

All paths return a SportsFramePlan with pixel crop coordinates guaranteed to
be a 9:16 rect clamped within the source dimensions. Deterministic: same
inputs always produce the same output.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import Optional

from contracts.clip import ClipDefinition
from contracts.face import FaceDetectionResult, SceneFaceData
from contracts.ingestion import IngestionResult
from contracts.strategies import SportsFramePlan

logger = logging.getLogger(__name__)

_VERTICAL_ASPECT: float = 9.0 / 16.0

# ── Algorithm defaults (all overridable via config.sports_strategy) ────────────

_DEFAULT_MIN_FACE_VISIBILITY: float = 0.2
_DEFAULT_EMA_ALPHA: float = 0.3
_DEFAULT_POSE_SAMPLE_FPS: int = 1
_DEFAULT_MOTION_SAMPLE_FPS: int = 2
_DEFAULT_MOTION_THUMB_WIDTH: int = 64
_DEFAULT_MOTION_THUMB_HEIGHT: int = 36


# ── Helpers: crop rect arithmetic ─────────────────────────────────────────────


def _center_crop_rect(src_width: int, src_height: int) -> tuple[int, int, int, int]:
    """Return (crop_x, crop_y, crop_w, crop_h) for a centered 9:16 crop."""
    crop_w = int(round(src_height * _VERTICAL_ASPECT))
    if crop_w > src_width:
        crop_w = src_width
    crop_x = (src_width - crop_w) // 2
    return crop_x, 0, crop_w, src_height


def _anchor_to_crop_rect(
    anchor_x: float,
    anchor_y: float,
    src_width: int,
    src_height: int,
) -> tuple[int, int, int, int]:
    """Convert normalised anchor (0–1) to a 9:16 pixel crop rect.

    Centres a full-height 9:16 crop window on the anchor point, clamped
    to source bounds. Mirrors podcast_strategy._compute_crop_rect logic.
    """
    crop_h = src_height
    crop_w = int(round(src_height * _VERTICAL_ASPECT))
    if crop_w > src_width:
        crop_w = src_width
        crop_h = src_height

    anchor_px = anchor_x * src_width
    crop_x = int(round(anchor_px - crop_w * 0.5))
    crop_x = max(0, min(crop_x, src_width - crop_w))
    crop_y = 0

    return crop_x, crop_y, crop_w, crop_h


# ── Method 1: Face centroid ────────────────────────────────────────────────────


def _try_face_centroid(
    clip: ClipDefinition,
    face_result: FaceDetectionResult,
    src_width: int,
    src_height: int,
    min_face_visibility: float,
) -> Optional[tuple[float, float]]:
    """Return normalised (anchor_x, anchor_y) from face centroid, or None.

    Filters clip scenes by face_visible_ratio >= min_face_visibility, then
    computes a visibility-weighted average of the face bbox horizontal centres.
    Vertical anchor is fixed at 0.5 (full-height crop).
    """
    clip_scene_ids = {s.scene_id for s in clip.scenes}
    active: list[SceneFaceData] = [
        fd for fd in face_result.scene_data
        if fd.scene_id in clip_scene_ids
        and fd.face_visible_ratio >= min_face_visibility
        and fd.average_bbox is not None
    ]

    if not active:
        return None

    total_weight = sum(fd.face_visible_ratio for fd in active)
    if total_weight == 0.0:
        return None

    anchor_x = sum(
        fd.face_visible_ratio * (fd.average_bbox.x + fd.average_bbox.width * 0.5)  # type: ignore[union-attr]
        for fd in active
    ) / total_weight

    return float(anchor_x), 0.5


# ── Method 2: MediaPipe Pose ───────────────────────────────────────────────────


def _try_pose_centroid(
    clip: ClipDefinition,
    ingestion_result: IngestionResult,
    src_width: int,
    src_height: int,
    sample_fps: int,
    ffmpeg_timeout: int,
) -> Optional[tuple[float, float]]:
    """Return normalised (anchor_x, anchor_y) from MediaPipe Pose, or None.

    Extracts keyframes at sample_fps, runs Pose detection on each, averages
    the visible landmark centroids across keyframes.
    """
    try:
        import mediapipe as mp  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except ImportError:
        logger.debug("MediaPipe/Pillow/NumPy not available; skipping pose tracking")
        return None

    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0
    duration = end_sec - start_sec
    if duration <= 0:
        return None

    n_frames = max(1, int(duration * sample_fps))

    centroids: list[tuple[float, float]] = []

    with tempfile.TemporaryDirectory(prefix="sf_pose_") as tmpdir:
        frame_pattern = os.path.join(tmpdir, "frame_%04d.jpg")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-i", ingestion_result.path,
            "-vf", f"fps={sample_fps}",
            "-vframes", str(n_frames),
            "-q:v", "5",
            frame_pattern,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=ffmpeg_timeout
            )
            if result.returncode != 0:
                logger.debug("Pose frame extraction failed: %s", result.stderr[-500:])
                return None
        except subprocess.TimeoutExpired:
            logger.debug("Pose frame extraction timed out")
            return None

        mp_pose = mp.solutions.pose
        with mp_pose.Pose(static_image_mode=True, min_detection_confidence=0.5) as pose:
            frame_files = sorted(
                f for f in os.listdir(tmpdir) if f.endswith(".jpg")
            )
            for fname in frame_files:
                fpath = os.path.join(tmpdir, fname)
                try:
                    img = Image.open(fpath).convert("RGB")
                    img_array = np.array(img)
                    results = pose.process(img_array)
                    if results.pose_landmarks:
                        lm = results.pose_landmarks.landmark
                        visible = [p for p in lm if p.visibility > 0.5]
                        if visible:
                            cx = sum(p.x for p in visible) / len(visible)
                            cy = sum(p.y for p in visible) / len(visible)
                            centroids.append((float(cx), float(cy)))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Pose frame %s failed: %s", fname, exc)

    if not centroids:
        return None

    avg_x = sum(c[0] for c in centroids) / len(centroids)
    avg_y = sum(c[1] for c in centroids) / len(centroids)
    return float(avg_x), float(avg_y)


# ── Method 3: Motion energy ────────────────────────────────────────────────────


def _try_motion_energy(
    clip: ClipDefinition,
    ingestion_result: IngestionResult,
    thumb_width: int,
    thumb_height: int,
    sample_fps: int,
    ffmpeg_timeout: int,
) -> Optional[tuple[float, float]]:
    """Return normalised (anchor_x, anchor_y) from motion energy analysis, or None.

    Extracts small thumbnail frames at sample_fps, computes per-pixel abs diff
    between consecutive frames using Pillow, sums diffs per column to find the
    highest-motion horizontal position. Vertical anchor fixed at 0.5.
    """
    try:
        from PIL import Image, ImageChops  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except ImportError:
        logger.debug("Pillow/NumPy not available; skipping motion energy tracking")
        return None

    start_sec = clip.start_time / 1000.0
    end_sec = clip.end_time / 1000.0
    duration = end_sec - start_sec
    if duration <= 0:
        return None

    n_frames = max(2, int(duration * sample_fps))

    with tempfile.TemporaryDirectory(prefix="sf_motion_") as tmpdir:
        frame_pattern = os.path.join(tmpdir, "frame_%04d.jpg")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-i", ingestion_result.path,
            "-vf", f"fps={sample_fps},scale={thumb_width}:{thumb_height}",
            "-vframes", str(n_frames),
            "-q:v", "8",
            frame_pattern,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=ffmpeg_timeout
            )
            if result.returncode != 0:
                logger.debug("Motion frame extraction failed: %s", result.stderr[-500:])
                return None
        except subprocess.TimeoutExpired:
            logger.debug("Motion frame extraction timed out")
            return None

        frame_files = sorted(
            f for f in os.listdir(tmpdir) if f.endswith(".jpg")
        )
        if len(frame_files) < 2:
            return None

        try:
            frames = [
                Image.open(os.path.join(tmpdir, f)).convert("L")
                for f in frame_files
            ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Motion frame load failed: %s", exc)
            return None

        # Sum pixel diffs across all consecutive frame pairs
        diff_sum = np.zeros((thumb_height, thumb_width), dtype=np.float32)
        for i in range(len(frames) - 1):
            diff = ImageChops.difference(frames[i], frames[i + 1])
            diff_sum += np.array(diff, dtype=np.float32)

        # Column sum → find peak motion column
        col_sums = diff_sum.sum(axis=0)
        if col_sums.sum() == 0:
            return None

        peak_col = int(col_sums.argmax())
        anchor_x = (peak_col + 0.5) / thumb_width
        return float(anchor_x), 0.5


# ── Public entry point ────────────────────────────────────────────────────────


def generate_plan(
    clip: ClipDefinition,
    face_result: FaceDetectionResult,
    ingestion_result: IngestionResult,
    config: dict,
) -> SportsFramePlan:
    """Generate a SportsFramePlan for the sports_action_crop layout.

    Runs the hybrid tracking cascade: face centroid → pose → motion energy →
    center. The first method that returns a valid anchor is used.

    Args:
        clip:             Clip to generate a plan for.
        face_result:      Pre-computed face detection output.
        ingestion_result: Source video metadata (path, resolution, fps).
        config:           Full pipeline config (sports_* overlays already applied).

    Returns:
        SportsFramePlan with crop coordinates in source pixel space.
    """
    strategy_config = config.get("sports_strategy", {})
    min_face_visibility = float(
        strategy_config.get("min_face_visibility", _DEFAULT_MIN_FACE_VISIBILITY)
    )
    pose_sample_fps = int(
        strategy_config.get("pose_sample_fps", _DEFAULT_POSE_SAMPLE_FPS)
    )
    motion_sample_fps = int(
        strategy_config.get("motion_sample_fps", _DEFAULT_MOTION_SAMPLE_FPS)
    )
    motion_thumb_w = int(
        strategy_config.get("motion_thumb_width", _DEFAULT_MOTION_THUMB_WIDTH)
    )
    motion_thumb_h = int(
        strategy_config.get("motion_thumb_height", _DEFAULT_MOTION_THUMB_HEIGHT)
    )
    ffmpeg_timeout = int(
        config.get("pipeline", {}).get("ffmpeg_timeout", 60)
    )
    sport = config.get("video_type", "sports_unknown").replace("sports_", "", 1)

    src_width, src_height = ingestion_result.resolution

    # ── Method 1: Face centroid ──────────────────────────────────────────────
    anchor = _try_face_centroid(
        clip, face_result, src_width, src_height, min_face_visibility
    )
    if anchor is not None:
        tracking_method = "face_centroid"
        logger.info(
            "Sports strategy: face_centroid anchor=(%.3f, %.3f)",
            anchor[0], anchor[1],
            extra={"clip_id": clip.clip_id, "stage": "compositor", "sport": sport},
        )
    else:
        # ── Method 2: MediaPipe Pose ─────────────────────────────────────────
        anchor = _try_pose_centroid(
            clip, ingestion_result, src_width, src_height,
            pose_sample_fps, ffmpeg_timeout,
        )
        if anchor is not None:
            tracking_method = "pose"
            logger.info(
                "Sports strategy: pose anchor=(%.3f, %.3f)",
                anchor[0], anchor[1],
                extra={"clip_id": clip.clip_id, "stage": "compositor", "sport": sport},
            )
        else:
            # ── Method 3: Motion energy ──────────────────────────────────────
            anchor = _try_motion_energy(
                clip, ingestion_result,
                motion_thumb_w, motion_thumb_h, motion_sample_fps, ffmpeg_timeout,
            )
            if anchor is not None:
                tracking_method = "motion_energy"
                logger.info(
                    "Sports strategy: motion_energy anchor=(%.3f, %.3f)",
                    anchor[0], anchor[1],
                    extra={"clip_id": clip.clip_id, "stage": "compositor", "sport": sport},
                )
            else:
                # ── Method 4: Center fallback ────────────────────────────────
                anchor = (0.5, 0.5)
                tracking_method = "center"
                logger.info(
                    "Sports strategy: center fallback",
                    extra={"clip_id": clip.clip_id, "stage": "compositor", "sport": sport},
                )

    crop_x, crop_y, crop_w, crop_h = _anchor_to_crop_rect(
        anchor[0], anchor[1], src_width, src_height
    )

    return SportsFramePlan(
        layout="sports_action_crop",
        sport=sport,
        tracking_method=tracking_method,
        crop_x=crop_x,
        crop_y=crop_y,
        crop_width=crop_w,
        crop_height=crop_h,
    )
