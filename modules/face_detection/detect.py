"""Face detection module — MediaPipe face detection with 2fps sampling and EMA smoothing.

Implements deterministic face detection using MediaPipe. Samples frames at
exactly 2fps using FFmpeg, applies EMA smoothing to bounding boxes, and
returns normalized coordinates for resolution independence.

Entry point: detect_faces(ingestion_result, scene_list, config) -> FaceDetectionResult
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING, Any

from contracts.face import FaceBBox, FaceDetectionResult, SceneFaceData

if TYPE_CHECKING:
    from contracts.ingestion import IngestionResult
    from contracts.scene import SceneList, SceneSegment

logger = logging.getLogger(__name__)

# Default config values — overridden by config.yaml at runtime
_DEFAULT_SAMPLE_FPS = 2
_DEFAULT_MIN_CONFIDENCE = 0.7
_DEFAULT_EMA_ALPHA = 0.3
_DEFAULT_MIN_FACE_SIZE = 0.05


def detect_faces(
    ingestion_result: "IngestionResult",
    scene_list: "SceneList",
    config: dict[str, Any],
) -> FaceDetectionResult:
    """Detect faces across all scenes using MediaPipe at 2fps sampling rate.

    For each scene: extracts frames at 2fps via FFmpeg, runs MediaPipe
    face detection, filters low-confidence detections, applies EMA smoothing
    to bounding boxes, and computes face visibility ratio.

    No faces detected is a valid result — the compositor falls back to
    gameplay-only layout.

    Args:
        ingestion_result: DTO from the ingestion stage.
        scene_list: DTO from the scene_splitter stage.
        config: Pipeline configuration dict (from config.yaml).

    Returns:
        FaceDetectionResult DTO with per-scene visibility ratios.

    Raises:
        RuntimeError: If MediaPipe cannot be loaded (dependency error).
    """
    face_cfg = config.get("face_detection", {})
    sample_fps: int = int(face_cfg.get("sample_fps", _DEFAULT_SAMPLE_FPS))
    min_confidence: float = float(face_cfg.get("min_confidence", _DEFAULT_MIN_CONFIDENCE))
    ema_alpha: float = float(face_cfg.get("ema_alpha", _DEFAULT_EMA_ALPHA))
    min_face_size: float = float(face_cfg.get("min_face_size", _DEFAULT_MIN_FACE_SIZE))

    video_id = ingestion_result.video_id
    video_path = ingestion_result.path

    logger.info(
        "Face detection stage started",
        extra={
            "stage": "face_detection",
            "video_id": video_id,
            "scene_count": len(scene_list.scenes),
            "sample_fps": sample_fps,
        },
    )

    detector = _load_mediapipe_detector(min_confidence)

    scene_results: list[SceneFaceData] = []
    for scene in scene_list.scenes:
        scene_data = _process_scene(
            scene, video_path, video_id, detector, sample_fps,
            min_confidence, ema_alpha, min_face_size, config
        )
        scene_results.append(scene_data)

    scene_data_tuple = tuple(scene_results)
    average_visibility = (
        sum(s.face_visible_ratio for s in scene_data_tuple) / len(scene_data_tuple)
        if scene_data_tuple
        else 0.0
    )
    faceless_count = sum(
        1 for s in scene_data_tuple if s.face_visible_ratio == 0.0
    )

    result = FaceDetectionResult(
        video_id=video_id,
        scene_data=scene_data_tuple,
        average_visibility=average_visibility,
        faceless_scene_count=faceless_count,
    )

    logger.info(
        "Face detection stage completed",
        extra={
            "stage": "face_detection",
            "video_id": video_id,
            "average_visibility": average_visibility,
            "faceless_scene_count": faceless_count,
        },
    )
    return result


def _load_mediapipe_detector(min_confidence: float) -> Any:
    """Load MediaPipe face detection model.

    Args:
        min_confidence: Minimum detection confidence threshold.

    Returns:
        MediaPipe FaceDetection object.

    Raises:
        RuntimeError: If MediaPipe is not installed or model load fails.
    """
    try:
        import mediapipe as mp  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "mediapipe is not installed. Run: pip install mediapipe"
        )

    try:
        face_detection = mp.solutions.face_detection.FaceDetection(
            model_selection=1,
            min_detection_confidence=min_confidence,
        )
        return face_detection
    except Exception as exc:
        raise RuntimeError(f"Failed to load MediaPipe face detection: {exc}") from exc


def _process_scene(
    scene: "SceneSegment",
    video_path: str,
    video_id: str,
    detector: Any,
    sample_fps: int,
    min_confidence: float,
    ema_alpha: float,
    min_face_size: float,
    config: dict[str, Any],
) -> SceneFaceData:
    """Process a single scene: extract frames, detect faces, smooth bboxes.

    Args:
        scene: The SceneSegment to process.
        video_path: Path to the source video.
        video_id: Video identifier for logging.
        detector: MediaPipe FaceDetection instance.
        sample_fps: Frames per second to sample.
        min_confidence: Minimum detection confidence threshold.
        ema_alpha: EMA smoothing alpha coefficient.
        min_face_size: Minimum normalized face size (width and height) to accept.
        config: Pipeline configuration dict.

    Returns:
        SceneFaceData DTO for this scene.
    """
    duration_s = scene.duration
    sample_count = max(1, math.ceil(duration_s * sample_fps))
    temp_dir = config.get("paths", {}).get("temp_dir", "output/temp")

    frame_paths = _extract_frames(
        video_path, scene.start_time, scene.end_time,
        sample_fps, temp_dir, scene.scene_id, config
    )

    bboxes: list[FaceBBox] = []
    try:
        for i, frame_path in enumerate(frame_paths):
            frame_ts_ms = scene.start_time + int(i * (1000 / sample_fps))
            bbox = _detect_face_in_frame(
                frame_path, detector, frame_ts_ms, min_confidence, min_face_size
            )
            if bbox is not None:
                bboxes.append(bbox)
    finally:
        for fp in frame_paths:
            try:
                if os.path.exists(fp):
                    os.unlink(fp)
            except OSError:
                pass

    bboxes_tuple = tuple(sorted(bboxes, key=lambda b: b.timestamp_ms))
    face_visible_ratio = len(bboxes_tuple) / sample_count if sample_count > 0 else 0.0
    average_bbox = _compute_ema_bbox(bboxes_tuple, ema_alpha)

    return SceneFaceData(
        scene_id=scene.scene_id,
        face_visible_ratio=face_visible_ratio,
        bounding_boxes=bboxes_tuple,
        average_bbox=average_bbox,
        sample_count=sample_count,
    )


def _extract_frames(
    video_path: str,
    start_ms: int,
    end_ms: int,
    sample_fps: int,
    temp_dir: str,
    scene_id: str,
    config: dict[str, Any],
) -> list[str]:
    """Extract frames from a video segment at the given sample rate using FFmpeg.

    Args:
        video_path: Source video path.
        start_ms: Segment start in milliseconds.
        end_ms: Segment end in milliseconds.
        sample_fps: Target frames per second.
        temp_dir: Directory for temp frame files.
        scene_id: Scene identifier for naming temp files.
        config: Pipeline configuration dict.

    Returns:
        List of paths to extracted frame images. May be empty on FFmpeg failure.
    """
    os.makedirs(temp_dir, exist_ok=True)
    start_s = start_ms / 1000.0
    duration_s = (end_ms - start_ms) / 1000.0

    with tempfile.TemporaryDirectory(
        prefix=f"faces_{scene_id}_", dir=temp_dir
    ) as frame_dir:
        pattern = os.path.join(frame_dir, "frame_%04d.jpg")
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", f"{start_s:.3f}",
            "-i", video_path,
            "-t", f"{duration_s:.3f}",
            "-vf", f"fps={sample_fps}",
            "-q:v", "2",
            "-loglevel", "error",
            pattern,
        ]
        timeout = config.get("pipeline", {}).get("ffmpeg_timeout", 300)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "FFmpeg frame extraction timed out",
                extra={"stage": "face_detection", "scene_id": scene_id},
            )
            return []

        if result.returncode != 0:
            logger.warning(
                "FFmpeg frame extraction failed",
                extra={
                    "stage": "face_detection",
                    "scene_id": scene_id,
                    "stderr": result.stderr[:200],
                },
            )
            return []

        frame_paths_in_tmp = sorted(
            os.path.join(frame_dir, f)
            for f in os.listdir(frame_dir)
            if f.endswith(".jpg")
        )

        # Copy frames out before temp dir is cleaned up
        persistent_paths: list[str] = []
        for i, src_path in enumerate(frame_paths_in_tmp):
            fd, dst_path = tempfile.mkstemp(
                suffix=".jpg",
                prefix=f"face_frame_{scene_id}_{i:04d}_",
                dir=temp_dir,
            )
            os.close(fd)
            shutil.copy2(src_path, dst_path)
            persistent_paths.append(dst_path)

        return persistent_paths


def _detect_face_in_frame(
    frame_path: str,
    detector: Any,
    timestamp_ms: int,
    min_confidence: float,
    min_face_size: float = _DEFAULT_MIN_FACE_SIZE,
) -> FaceBBox | None:
    """Detect the highest-confidence face in a single frame.

    Filters out detections below min_confidence and bounding boxes smaller
    than min_face_size (normalized width/height).

    Args:
        frame_path: Path to the JPEG frame image.
        detector: MediaPipe FaceDetection instance.
        timestamp_ms: Frame timestamp in milliseconds.
        min_confidence: Minimum confidence threshold for filtering.
        min_face_size: Minimum normalized face size (0–1) for width and height.

    Returns:
        FaceBBox for the highest-confidence detected face, or None if no face found.
    """
    try:
        import cv2  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "opencv-python is not installed. Run: pip install opencv-python-headless"
        )

    image = cv2.imread(frame_path)
    if image is None:
        return None

    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    results = detector.process(image_rgb)

    if not results.detections:
        return None

    # Select the highest-confidence detection
    best = max(
        results.detections,
        key=lambda d: d.score[0] if d.score else 0.0,
    )

    confidence = float(best.score[0]) if best.score else 0.0
    if confidence < min_confidence:
        return None

    bbox = best.location_data.relative_bounding_box
    x = max(0.0, float(bbox.xmin))
    y = max(0.0, float(bbox.ymin))
    width = min(float(bbox.width), 1.0 - x)
    height = min(float(bbox.height), 1.0 - y)

    if width <= 0.0 or height <= 0.0:
        return None

    if width < min_face_size or height < min_face_size:
        return None

    return FaceBBox(
        x=x,
        y=y,
        width=width,
        height=height,
        confidence=confidence,
        timestamp_ms=timestamp_ms,
    )


def _compute_ema_bbox(
    bboxes: tuple[FaceBBox, ...],
    alpha: float,
) -> FaceBBox | None:
    """Compute the EMA-smoothed average bounding box across a sequence of detections.

    EMA formula: ema_t = alpha * value_t + (1 - alpha) * ema_{t-1}
    First value is used as the initial EMA state (no warm-up required).

    Args:
        bboxes: Sequence of FaceBBox instances sorted by timestamp_ms.
        alpha: EMA smoothing coefficient (0 < alpha <= 1).

    Returns:
        EMA-smoothed FaceBBox using the last detection's timestamp, or None if empty.
    """
    if not bboxes:
        return None

    ema_x = bboxes[0].x
    ema_y = bboxes[0].y
    ema_w = bboxes[0].width
    ema_h = bboxes[0].height
    avg_conf = bboxes[0].confidence

    for bbox in bboxes[1:]:
        ema_x = alpha * bbox.x + (1 - alpha) * ema_x
        ema_y = alpha * bbox.y + (1 - alpha) * ema_y
        ema_w = alpha * bbox.width + (1 - alpha) * ema_w
        ema_h = alpha * bbox.height + (1 - alpha) * ema_h
        avg_conf = alpha * bbox.confidence + (1 - alpha) * avg_conf

    return FaceBBox(
        x=ema_x,
        y=ema_y,
        width=min(ema_w, 1.0 - ema_x),
        height=min(ema_h, 1.0 - ema_y),
        confidence=avg_conf,
        timestamp_ms=bboxes[-1].timestamp_ms,
    )
