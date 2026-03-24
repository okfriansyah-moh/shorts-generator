---
name: mediapipe
description: "MediaPipe face detection patterns for Shorts Factory. Use when implementing the face_detection module. Covers detector setup, 2fps sampling strategy, EMA bounding box smoothing, visibility ratio computation, fallback layout decisions, and normalized coordinate handling."
---

# MediaPipe Face Detection Skill

## When to Use

- Implementing the face_detection module
- Configuring detection confidence thresholds
- Implementing bounding box smoothing (EMA)
- Computing face visibility ratios for scoring
- Handling fallback when no face is detected

## Library

- **Package:** `mediapipe` (PyPI)
- **Solution:** Face Detection (short-range model, optimized for 1–5m distance)
- **Backend:** TensorFlow Lite
- **Device:** CPU-only (GPU optional but not required)
- **Memory:** ~200 MB peak

## Detector Setup

```python
import mediapipe as mp

mp_face = mp.solutions.face_detection

def create_detector(config: dict):
    return mp_face.FaceDetection(
        model_selection=0,  # 0 = short-range (< 2m), 1 = full-range (< 5m)
        min_detection_confidence=config["face_detection"]["min_confidence"],  # 0.7
    )
```

| Parameter                  | Value           | Source                                 |
| -------------------------- | --------------- | -------------------------------------- |
| `model_selection`          | 0 (short-range) | Architecture spec                      |
| `min_detection_confidence` | 0.7             | `config.face_detection.min_confidence` |

## 2fps Sampling Strategy

Face detection runs on sampled frames, NOT every frame:

```python
import cv2

def sample_faces_for_scene(
    video_path: str,
    start_ms: int,
    end_ms: int,
    detector,
    sample_fps: int = 2,  # config.face_detection.sample_fps
) -> list[dict]:
    """Sample frames at 2fps and detect faces."""
    cap = cv2.VideoCapture(video_path)
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    frame_interval = int(video_fps / sample_fps)  # e.g., 30fps / 2 = every 15 frames

    start_frame = int(start_ms * video_fps / 1000)
    end_frame = int(end_ms * video_fps / 1000)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    bboxes = []
    frame_num = start_frame

    while frame_num < end_frame:
        ret, frame = cap.read()
        if not ret:
            break

        # Convert BGR → RGB for MediaPipe
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = detector.process(rgb_frame)

        h, w, _ = frame.shape
        timestamp_ms = int(frame_num * 1000 / video_fps)

        if results.detections:
            # Pick largest face (primary subject heuristic)
            best = max(results.detections, key=lambda d:
                d.location_data.relative_bounding_box.width *
                d.location_data.relative_bounding_box.height
            )
            rbb = best.location_data.relative_bounding_box
            bboxes.append({
                "timestamp_ms": timestamp_ms,
                "x": rbb.xmin,        # Already normalized [0, 1]
                "y": rbb.ymin,
                "width": rbb.width,
                "height": rbb.height,
                "confidence": best.score[0],
            })
        else:
            bboxes.append({
                "timestamp_ms": timestamp_ms,
                "x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0,
                "confidence": 0.0,
            })

        # Skip to next sample frame
        frame_num += frame_interval
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)

    cap.release()
    return bboxes
```

**Why 2fps?**

- Face position changes slowly (~0.5s is sufficient granularity)
- Processing 30fps would be 15× slower with minimal benefit
- ~50ms per frame inference → ~6 seconds per 60-second clip at 2fps

## Normalized Coordinates

All bounding box coordinates are in **[0.0, 1.0]** range (resolution-independent):

```
(x=0.0, y=0.0) ────────── (x=1.0, y=0.0)
       │                         │
       │     (face bbox)         │
       │   x,y ──── x+w         │
       │    │         │          │
       │   y+h ──────            │
       │                         │
(x=0.0, y=1.0) ────────── (x=1.0, y=1.0)
```

- `x`, `y` = top-left corner of bbox
- `width`, `height` = bbox dimensions
- All values relative to frame dimensions

## EMA Bounding Box Smoothing

Eliminates frame-to-frame jitter:

```python
def smooth_bboxes_ema(bboxes: list[dict], alpha: float = 0.3) -> list[dict]:
    """Exponential Moving Average smoothing."""
    smoothed = []
    prev = None

    for bbox in bboxes:
        if bbox["confidence"] > 0.0:
            if prev is not None:
                smooth = {
                    "timestamp_ms": bbox["timestamp_ms"],
                    "x": alpha * bbox["x"] + (1 - alpha) * prev["x"],
                    "y": alpha * bbox["y"] + (1 - alpha) * prev["y"],
                    "width": alpha * bbox["width"] + (1 - alpha) * prev["width"],
                    "height": alpha * bbox["height"] + (1 - alpha) * prev["height"],
                    "confidence": bbox["confidence"],
                }
            else:
                smooth = bbox.copy()
            smoothed.append(smooth)
            prev = smooth
        else:
            # No face — hold last known position
            if prev:
                smoothed.append({**prev, "timestamp_ms": bbox["timestamp_ms"], "confidence": 0.0})
            else:
                smoothed.append(bbox)

    return smoothed
```

- **Alpha = 0.3:** balance between responsiveness and smoothness
- Higher alpha (→ 1.0) = more responsive, more jitter
- Lower alpha (→ 0.0) = smoother, slower to adapt

## Visibility Ratio

```python
def compute_visibility_ratio(bboxes: list[dict]) -> float:
    """Fraction of sampled frames with face detected."""
    if not bboxes:
        return 0.0
    detected = sum(1 for b in bboxes if b["confidence"] > 0.0)
    return detected / len(bboxes)
```

- Used in scoring as `face_presence` signal (0.0–1.0)
- Used by compositor to decide layout (face+gameplay vs gameplay-only)

## Fallback Layout Decision

```python
def decide_layout(visibility_ratio: float, min_face_size: float = 0.05) -> str:
    """Decide compositor layout based on face detection results."""
    if visibility_ratio < 0.1:
        return "gameplay_only_zoom"  # No usable face data
    else:
        return "face_gameplay_split"  # Normal 65/35 layout
```

**Gap handling:**
| Gap Duration | Strategy |
|-------------|----------|
| < 1 second | Interpolate bbox positions |
| 1–5 seconds | Hold last known position |
| > 5 seconds | Switch to gameplay-only layout |

## Converting to DTOs

```python
from contracts.face import FaceBBox, SceneFaceData, FaceDetectionResult

def build_face_result(video_id: str, scenes_data: list) -> FaceDetectionResult:
    scene_faces = []
    for scene in scenes_data:
        boxes = tuple(
            FaceBBox(
                timestamp_ms=b["timestamp_ms"],
                x=b["x"], y=b["y"],
                width=b["width"], height=b["height"],
                confidence=b["confidence"],
            )
            for b in scene["bboxes"]
        )
        scene_faces.append(SceneFaceData(
            scene_id=scene["scene_id"],
            boxes=boxes,
            visible_ratio=compute_visibility_ratio(scene["bboxes"]),
        ))

    return FaceDetectionResult(
        video_id=video_id,
        scenes=tuple(scene_faces),
    )
```

## Face Detection is OPTIONAL

The pipeline MUST work without face data:

```python
# In orchestrator — face detection failure is non-fatal
try:
    face_result = face_detection.process(ingestion_result, scene_list, config)
except Exception as e:
    logger.warning("Face detection failed, continuing without", extra={"error": str(e)})
    face_result = None  # Compositor will use gameplay-only layout
```

## Anti-Patterns

```python
# ❌ Processing every frame (15× slower, no benefit)
for frame in all_frames:
    detect(frame)

# ❌ Using pixel coordinates (resolution-dependent)
bbox = {"x": 450, "y": 200, "width": 150, "height": 200}

# ❌ Crashing pipeline when no face detected
assert face_result is not None  # Face is optional!

# ❌ Multiple faces without selection logic
all_faces = results.detections  # Which one is the subject?

# ✅ 2fps sampling, normalized coords, optional module, largest face
```
