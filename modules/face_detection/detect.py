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
_DEFAULT_MODEL_PATH = "models/blaze_face_short_range.task"
_DEFAULT_SKIP = False


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
    """
    face_cfg = config.get("face_detection", {})
    sample_fps: int = int(face_cfg.get("sample_fps", _DEFAULT_SAMPLE_FPS))
    min_confidence: float = float(face_cfg.get("min_confidence", _DEFAULT_MIN_CONFIDENCE))
    ema_alpha: float = float(face_cfg.get("ema_alpha", _DEFAULT_EMA_ALPHA))
    min_face_size: float = float(face_cfg.get("min_face_size", _DEFAULT_MIN_FACE_SIZE))
    model_path: str = str(face_cfg.get("model_path", _DEFAULT_MODEL_PATH))

    video_id = ingestion_result.video_id
    video_path = ingestion_result.path

    # Early return when face detection is disabled via config or CLI
    if bool(face_cfg.get("skip", _DEFAULT_SKIP)):
        logger.info(
            "Face detection disabled via config; using gameplay-only layout",
            extra={"stage": "face_detection", "video_id": video_id},
        )
        return _empty_result(video_id, scene_list.scenes)

    logger.info(
        "Face detection stage started",
        extra={
            "stage": "face_detection",
            "video_id": video_id,
            "scene_count": len(scene_list.scenes),
            "sample_fps": sample_fps,
        },
    )

    detector = _load_mediapipe_detector(min_confidence, model_path)
    if detector is None:
        logger.warning(
            "MediaPipe unavailable — attempting PiP region scan fallback",
            extra={"stage": "face_detection", "video_id": video_id},
        )
        pip_bbox = _scan_pip_region(video_path, config)
        if pip_bbox is not None:
            logger.info(
                "PiP region detected via skin-tone scan at (%.2f, %.2f)",
                pip_bbox.x, pip_bbox.y,
                extra={"stage": "face_detection", "video_id": video_id},
            )
        return _empty_result(video_id, scene_list.scenes, estimated_pip=pip_bbox)

    scene_results: list[SceneFaceData] = []
    try:
        for scene in scene_list.scenes:
            scene_data = _process_scene(
                scene, video_path, video_id, detector, sample_fps,
                min_confidence, ema_alpha, min_face_size, config
            )
            scene_results.append(scene_data)
    finally:
        if hasattr(detector, "close"):
            detector.close()

    scene_data_tuple = tuple(scene_results)
    average_visibility = (
        sum(s.face_visible_ratio for s in scene_data_tuple) / len(scene_data_tuple)
        if scene_data_tuple
        else 0.0
    )
    faceless_count = sum(
        1 for s in scene_data_tuple if s.face_visible_ratio == 0.0
    )

    # Aggregate a video-level PiP bbox from ALL per-scene bounding boxes.
    # Since the face cam PiP barely moves between scenes, averaging all
    # detected positions gives a stable video-level estimate.
    estimated_pip = _compute_video_level_bbox(scene_data_tuple)

    result = FaceDetectionResult(
        video_id=video_id,
        scene_data=scene_data_tuple,
        average_visibility=average_visibility,
        faceless_scene_count=faceless_count,
        estimated_pip_bbox=estimated_pip,
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


def _empty_result(
    video_id: str,
    scenes: tuple["SceneSegment", ...],
    estimated_pip: "FaceBBox | None" = None,
) -> FaceDetectionResult:
    """Build a valid empty FaceDetectionResult (no faces in any scene)."""
    empty_scenes = tuple(
        SceneFaceData(
            scene_id=s.scene_id,
            face_visible_ratio=0.0,
            bounding_boxes=(),
            average_bbox=None,
            sample_count=0,
        )
        for s in scenes
    )
    return FaceDetectionResult(
        video_id=video_id,
        scene_data=empty_scenes,
        average_visibility=0.0,
        faceless_scene_count=len(empty_scenes),
        estimated_pip_bbox=estimated_pip,
    )


def _compute_video_level_bbox(
    scene_data: tuple[SceneFaceData, ...],
) -> "FaceBBox | None":
    """Aggregate all per-scene bounding boxes into a single video-level PiP estimate.

    Since the face cam PiP overlay is in a fixed position across the video,
    averaging ALL detected bounding boxes (even from low-visibility scenes)
    gives a stable estimate of where the PiP is located.

    Returns None when no bounding boxes were detected in any scene.
    """
    all_bboxes: list[FaceBBox] = []
    for sd in scene_data:
        all_bboxes.extend(sd.bounding_boxes)
    if not all_bboxes:
        return None
    n = len(all_bboxes)
    avg_x = sum(b.x for b in all_bboxes) / n
    avg_y = sum(b.y for b in all_bboxes) / n
    avg_w = sum(b.width for b in all_bboxes) / n
    avg_h = sum(b.height for b in all_bboxes) / n
    avg_conf = sum(b.confidence for b in all_bboxes) / n
    return FaceBBox(
        x=avg_x,
        y=avg_y,
        width=min(avg_w, 1.0 - avg_x),
        height=min(avg_h, 1.0 - avg_y),
        confidence=avg_conf,
        timestamp_ms=0,
    )


# PiP scan candidate regions — common face cam overlay positions in
# gaming stream recordings, expressed as (name, x, y, width, height)
# in normalized coordinates.
_PIP_CANDIDATES: list[tuple[str, float, float, float, float]] = [
    ("bottom_left", 0.0, 0.65, 0.30, 0.35),
    ("bottom_center", 0.35, 0.65, 0.30, 0.35),
    ("bottom_middle", 0.35, 0.65, 0.30, 0.35),
    ("bottom_right", 0.70, 0.65, 0.30, 0.35),
    ("middle_left", 0.0, 0.325, 0.30, 0.35),
    ("middle_right", 0.70, 0.325, 0.30, 0.35),
    ("upper_middle_left", 0.0, 0.15, 0.30, 0.35),
    ("top_left", 0.0, 0.0, 0.30, 0.35),
    ("top_right", 0.70, 0.0, 0.30, 0.35),
]

# Minimum skin-tone density to consider a region as containing a face cam PiP.
_MIN_SKIN_DENSITY = 0.03


def _scan_pip_region(
    video_path: str,
    config: dict[str, Any],
) -> "FaceBBox | None":
    """Detect PiP face cam region via skin-tone analysis when MediaPipe is unavailable.

    Extracts a few frames from the first 30 seconds, checks candidate PiP
    regions for skin-tone pixel density, and returns the region with the
    highest consistent density across frames.

    Uses Pillow (PIL) for image analysis — optional, returns None if unavailable.
    """
    try:
        from PIL import Image  # type: ignore[import]
    except ImportError:
        logger.debug(
            "Pillow not available for PiP scan fallback",
            extra={"stage": "face_detection", "video_id": ""},
        )
        return None

    frame_paths = _extract_scan_frames(video_path, config)
    if not frame_paths:
        return None

    scores: dict[str, float] = {name: 0.0 for name, *_ in _PIP_CANDIDATES}

    try:
        for fp in frame_paths:
            try:
                img = Image.open(fp).resize((320, 240)).convert("HSV")
            except Exception:
                continue
            w, h = img.size
            for name, cx, cy, cw, ch in _PIP_CANDIDATES:
                px, py = int(cx * w), int(cy * h)
                pw, ph = int(cw * w), int(ch * h)
                region = img.crop((px, py, px + pw, py + ph))
                pixels = list(region.getdata())
                if not pixels:
                    continue
                # Skin-tone in PIL HSV: H in [0,36] or [240,255], S>=30, V>=50
                skin = sum(
                    1 for hue, sat, val in pixels
                    if (hue <= 36 or hue >= 240) and sat >= 30 and val >= 50
                )
                scores[name] += skin / len(pixels)
    finally:
        for fp in frame_paths:
            try:
                if os.path.exists(fp):
                    os.unlink(fp)
            except OSError:
                pass

    if not scores:
        return None
    best_name = max(scores, key=lambda k: scores[k])
    if scores[best_name] < _MIN_SKIN_DENSITY:
        return None

    for name, x, y, w, h in _PIP_CANDIDATES:
        if name == best_name:
            logger.debug(
                "PiP scan selected region %s (score=%.3f)",
                best_name, scores[best_name],
                extra={"stage": "face_detection", "video_id": ""},
            )
            return FaceBBox(
                x=x, y=y, width=w, height=h,
                confidence=min(scores[best_name], 1.0),
                timestamp_ms=0,
            )
    return None


def _extract_scan_frames(
    video_path: str,
    config: dict[str, Any],
    timestamps_s: tuple[int, ...] = (5, 15, 25),
) -> list[str]:
    """Extract a few frames at fixed timestamps for PiP region scanning."""
    temp_dir = config.get("paths", {}).get("temp_dir", "output/temp")
    os.makedirs(temp_dir, exist_ok=True)
    timeout = config.get("pipeline", {}).get("ffmpeg_timeout", 300)
    paths: list[str] = []
    for ts in timestamps_s:
        fd, path = tempfile.mkstemp(
            suffix=".jpg", prefix=f"pip_scan_{ts}_", dir=temp_dir,
        )
        os.close(fd)
        cmd = [
            "ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
            "-vframes", "1", "-q:v", "2", "-loglevel", "error", path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode == 0 and os.path.getsize(path) > 0:
                paths.append(path)
            else:
                if os.path.exists(path):
                    os.unlink(path)
        except (subprocess.TimeoutExpired, OSError):
            if os.path.exists(path):
                os.unlink(path)
    return paths


def _load_mediapipe_detector(min_confidence: float, model_path: str) -> Any | None:
    """Load MediaPipe face detector using the Tasks API.

    Returns None (instead of raising) when mediapipe is missing or the
    model file does not exist — face detection is optional per architecture.

    Args:
        min_confidence: Minimum detection confidence threshold.
        model_path: Path to the .task model file.

    Returns:
        MediaPipe FaceDetector instance, or None on failure.
    """
    try:
        import mediapipe as mp  # type: ignore[import]
    except ImportError:
        logger.warning(
            "mediapipe is not installed — face detection will be skipped",
            extra={"stage": "face_detection", "video_id": ""},
        )
        return None

    if not os.path.isfile(model_path):
        logger.warning(
            "MediaPipe model file not found at %s — face detection will be skipped",
            model_path,
            extra={"stage": "face_detection", "video_id": ""},
        )
        return None

    try:
        options = mp.tasks.vision.FaceDetectorOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            min_detection_confidence=min_confidence,
        )
        return mp.tasks.vision.FaceDetector.create_from_options(options)
    except Exception as exc:
        logger.warning(
            "Failed to create MediaPipe FaceDetector: %s",
            exc,
            extra={"stage": "face_detection", "video_id": ""},
        )
        return None


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

    Uses the MediaPipe Tasks API. Bounding boxes are returned in pixel
    coordinates and normalized to [0, 1] for resolution independence.

    Args:
        frame_path: Path to the JPEG frame image.
        detector: MediaPipe FaceDetector (Tasks API) instance.
        timestamp_ms: Frame timestamp in milliseconds.
        min_confidence: Minimum confidence threshold for filtering.
        min_face_size: Minimum normalized face size (0–1) for width and height.

    Returns:
        FaceBBox for the highest-confidence detected face, or None if no face found.
    """
    try:
        import mediapipe as mp  # type: ignore[import]
    except ImportError:
        return None

    try:
        mp_image = mp.Image.create_from_file(frame_path)
    except Exception:
        return None

    result = detector.detect(mp_image)

    if not result.detections:
        return None

    # Select the highest-confidence detection
    best = max(
        result.detections,
        key=lambda d: d.categories[0].score if d.categories else 0.0,
    )

    confidence = float(best.categories[0].score) if best.categories else 0.0
    if confidence < min_confidence:
        return None

    # Tasks API returns pixel coordinates — normalize to [0, 1]
    bbox = best.bounding_box
    img_w = mp_image.width
    img_h = mp_image.height
    if img_w <= 0 or img_h <= 0:
        return None

    x = max(0.0, float(bbox.origin_x) / img_w)
    y = max(0.0, float(bbox.origin_y) / img_h)
    width = min(float(bbox.width) / img_w, 1.0 - x)
    height = min(float(bbox.height) / img_h, 1.0 - y)

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
