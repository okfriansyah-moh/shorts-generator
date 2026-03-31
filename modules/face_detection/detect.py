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
_DEFAULT_MODEL_PATH = "models/blaze_face_short_range.tflite"
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
    # Prefer region voting (filters out game character faces) — fall back
    # to averaging only when voting has insufficient data.
    estimated_pip = _vote_pip_region(scene_data_tuple)
    if estimated_pip is None:
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
# in normalized coordinates. Regions are intentionally larger than the
# actual PiP window to define search areas for face voting.
_PIP_CANDIDATES: list[tuple[str, float, float, float, float]] = [
    ("bottom_left",        0.0,  0.55, 0.25, 0.45),
    ("bottom_center",      0.20, 0.55, 0.25, 0.45),
    ("bottom_middle",      0.35, 0.55, 0.30, 0.45),
    ("bottom_right",       0.70, 0.55, 0.30, 0.45),
    ("middle_left",        0.0,  0.25, 0.25, 0.45),
    ("middle_right",       0.70, 0.25, 0.30, 0.45),
    ("upper_middle_left",  0.0,  0.0,  0.25, 0.45),
    ("top_left",           0.0,  0.0,  0.25, 0.40),
    ("top_right",          0.70, 0.0,  0.30, 0.40),
]

# Minimum number of face detections inside a single candidate region
# before we trust it as the real PiP location.
_MIN_REGION_VOTES = 3


def _face_inside_candidate(
    face: FaceBBox,
    candidate: tuple[str, float, float, float, float],
) -> bool:
    """Check if a detected face center falls inside a candidate PiP region."""
    _name, cx, cy, cw, ch = candidate
    face_center_x = face.x + face.width / 2
    face_center_y = face.y + face.height / 2
    return (
        cx <= face_center_x <= cx + cw
        and cy <= face_center_y <= cy + ch
    )


def _vote_pip_region(
    scene_data: tuple[SceneFaceData, ...],
) -> FaceBBox | None:
    """Vote on which candidate PiP region consistently contains a real human face.

    For each detected face across all scenes, check which candidate region
    its center falls inside. The candidate with the most votes wins, but
    only if it has enough votes to be reliable (>= _MIN_REGION_VOTES).

    Returns the full candidate region bbox (not the raw face bbox) so the
    compositor crops the entire OBS window.
    """
    votes: dict[str, int] = {name: 0 for name, *_ in _PIP_CANDIDATES}

    for sd in scene_data:
        for face in sd.bounding_boxes:
            for candidate in _PIP_CANDIDATES:
                if _face_inside_candidate(face, candidate):
                    votes[candidate[0]] += 1
                    break  # one face = one vote for one region only

    if not votes:
        return None

    best_name = max(votes, key=lambda k: votes[k])
    if votes[best_name] < _MIN_REGION_VOTES:
        return None

    for name, x, y, w, h in _PIP_CANDIDATES:
        if name == best_name:
            logger.info(
                "PiP region voting selected %s (%d votes)",
                best_name,
                votes[best_name],
                extra={"stage": "face_detection", "video_id": ""},
            )
            return FaceBBox(
                x=x, y=y, width=w, height=h,
                confidence=1.0, timestamp_ms=0,
            )
    return None


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

    per_frame: dict[str, list[float]] = {name: [] for name, *_ in _PIP_CANDIDATES}

    try:
        for fp in frame_paths:
            try:
                img = Image.open(fp).resize((640, 480)).convert("HSV")
            except Exception:
                continue
            w, h = img.size
            for name, cx, cy, cw, ch in _PIP_CANDIDATES:
                px, py = int(cx * w), int(cy * h)
                pw, ph = int(cw * w), int(ch * h)
                region = img.crop((px, py, px + pw, py + ph))
                pixels = list(region.get_flattened_data())
                if not pixels:
                    continue
                # Skin-tone in PIL HSV: H in [0,28] or [245,255], S>=50, V>=70
                # Tighter than before to avoid orange/yellow game UI elements
                skin = sum(
                    1 for hue, sat, val in pixels
                    if (hue <= 28 or hue >= 245) and sat >= 50 and val >= 70
                )
                per_frame[name].append(skin / len(pixels))
    finally:
        for fp in frame_paths:
            try:
                if os.path.exists(fp):
                    os.unlink(fp)
            except OSError:
                pass

    if not per_frame:
        return None
    # Use minimum density across frames — PiP face cam must be consistently present
    scores = {
        name: min(densities) if densities else 0.0
        for name, densities in per_frame.items()
    }
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
    """Load MediaPipe face detector.

    Resolution order:
      1. If a valid .task/.tflite model file exists, use the Tasks API.
      2. Otherwise, try the bundled legacy ``mp.solutions.face_detection``
         API which ships inside older ``mediapipe`` pip packages.

    Returns None when mediapipe is not installed.
    """
    try:
        import mediapipe as mp  # type: ignore[import]
    except ImportError:
        logger.warning(
            "mediapipe is not installed — face detection will be skipped",
            extra={"stage": "face_detection", "video_id": ""},
        )
        return None

    # --- Resolve model file: try configured path, then common extensions ---
    candidate_paths = [model_path]
    base, ext = os.path.splitext(model_path)
    if ext == ".task":
        candidate_paths.append(base + ".tflite")
    elif ext == ".tflite":
        candidate_paths.append(base + ".task")

    resolved_path: str | None = None
    for p in candidate_paths:
        if os.path.isfile(p):
            # Validate the file is a real binary model, not an HTML/XML error page
            try:
                with open(p, "rb") as f:
                    header = f.read(1)
            except OSError:
                continue
            if header[:1] not in (b"<", b"{"):
                resolved_path = p
                break
            else:
                logger.warning(
                    "Model file %s appears to be an HTML/XML error page "
                    "(bad download). Skipping.",
                    p,
                    extra={"stage": "face_detection", "video_id": ""},
                )

    # --- Try Tasks API first (requires valid model file) ---
    if resolved_path is not None:
        try:
            options = mp.tasks.vision.FaceDetectorOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=resolved_path),
                running_mode=mp.tasks.vision.RunningMode.IMAGE,
                min_detection_confidence=min_confidence,
            )
            detector = mp.tasks.vision.FaceDetector.create_from_options(options)
            logger.info(
                "MediaPipe Tasks API face detector loaded from %s",
                resolved_path,
                extra={"stage": "face_detection", "video_id": ""},
            )
            return detector
        except Exception as exc:
            logger.warning(
                "Tasks API detector failed (%s) — "
                "trying legacy detector.",
                exc,
                extra={"stage": "face_detection", "video_id": ""},
            )

    # --- Fallback: legacy solutions API (bundled in older pip packages) ---
    if hasattr(mp, "solutions") and hasattr(mp.solutions, "face_detection"):
        try:
            legacy = mp.solutions.face_detection.FaceDetection(  # type: ignore[attr-defined]
                model_selection=0,
                min_detection_confidence=min_confidence,
            )
            logger.info(
                "MediaPipe legacy face detector loaded (no model file required)",
                extra={"stage": "face_detection", "video_id": ""},
            )
            return legacy
        except Exception as exc:
            logger.warning(
                "Legacy detector also failed: %s",
                exc,
                extra={"stage": "face_detection", "video_id": ""},
            )
    else:
        if resolved_path is None:
            logger.warning(
                "No model file found at %s and mp.solutions.face_detection "
                "is not available in this mediapipe version. "
                "Download the model: curl -fLo models/blaze_face_short_range.tflite "
                "'https://storage.googleapis.com/mediapipe-models/face_detector/"
                "blaze_face_short_range/float16/1/blaze_face_short_range.tflite'",
                model_path,
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

    Supports both the Tasks API (FaceDetector.detect) and the legacy
    solutions API (FaceDetection.process). Bounding boxes are returned
    as normalized [0, 1] coordinates for resolution independence.
    """
    try:
        import mediapipe as mp  # type: ignore[import]
    except ImportError:
        return None

    try:
        # Legacy solutions API: FaceDetection has .process() method
        if hasattr(detector, "process"):
            import cv2  # type: ignore[import]
            img = cv2.imread(frame_path)
            if img is None:
                return None
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            result = detector.process(rgb)
            if not result.detections:
                return None
            best = max(
                result.detections,
                key=lambda d: d.score[0] if d.score else 0.0,
            )
            confidence = float(best.score[0]) if best.score else 0.0
            if confidence < min_confidence:
                return None
            rbb = best.location_data.relative_bounding_box
            x = max(0.0, float(rbb.xmin))
            y = max(0.0, float(rbb.ymin))
            width = min(float(rbb.width), 1.0 - x)
            height = min(float(rbb.height), 1.0 - y)
        else:
            # Tasks API: FaceDetector has .detect() method
            try:
                mp_image = mp.Image.create_from_file(frame_path)
            except Exception:
                return None
            result = detector.detect(mp_image)
            if not result.detections:
                return None
            best = max(
                result.detections,
                key=lambda d: d.categories[0].score if d.categories else 0.0,
            )
            confidence = float(best.categories[0].score) if best.categories else 0.0
            if confidence < min_confidence:
                return None
            bbox = best.bounding_box
            img_w = mp_image.width
            img_h = mp_image.height
            if img_w <= 0 or img_h <= 0:
                return None
            x = max(0.0, float(bbox.origin_x) / img_w)
            y = max(0.0, float(bbox.origin_y) / img_h)
            width = min(float(bbox.width) / img_w, 1.0 - x)
            height = min(float(bbox.height) / img_h, 1.0 - y)
    except Exception:
        return None

    if width <= 0.0 or height <= 0.0:
        return None
    if width < min_face_size or height < min_face_size:
        return None

    return FaceBBox(
        x=x, y=y, width=width, height=height,
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
