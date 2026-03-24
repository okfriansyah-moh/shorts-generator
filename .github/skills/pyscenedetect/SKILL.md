---
name: pyscenedetect
description: "PySceneDetect patterns for Shorts Factory. Use when implementing the scene_splitter module. Covers scene detection configuration, threshold tuning, post-processing rules for duration enforcement, timing format conversion, and caching/idempotency."
---

# PySceneDetect Skill

## When to Use

- Implementing the scene_splitter module
- Configuring scene detection thresholds
- Enforcing scene duration constraints (3–20s)
- Converting between PySceneDetect timing formats and DTO milliseconds

## Library

- **Package:** `scenedetect` (PyPI: `pip install scenedetect[opencv]`)
- **Engine:** FFmpeg-based frame reading
- **Determinism:** Fixed threshold = identical results on identical input

## Scene Detection

```python
from scenedetect import detect, ContentDetector, AdaptiveDetector

def detect_scenes(video_path: str, threshold: float = 27.0) -> list:
    """Detect scene boundaries using content-aware detection."""
    # ContentDetector: compares frame color histograms
    scene_list = detect(video_path, ContentDetector(threshold=threshold))
    return scene_list
```

**Detector options:**

| Detector                                   | Use Case                   | Deterministic |
| ------------------------------------------ | -------------------------- | ------------- |
| `ContentDetector(threshold=27.0)`          | General gameplay (default) | Yes           |
| `AdaptiveDetector(adaptive_threshold=3.0)` | Variable-lighting content  | Yes           |

- **Threshold:** 27.0 (from `config.scene_splitter.threshold`)
- Higher threshold → fewer scenes (more conservative)
- Lower threshold → more scenes (more sensitive)

## Post-Processing: Duration Enforcement

PySceneDetect may produce scenes outside the [3–20]s range. Post-processing is mandatory:

```python
def enforce_duration_constraints(
    scenes: list,
    video_id: str,
    min_duration: float = 3.0,   # config.scene_splitter.min_scene_duration
    max_duration: float = 20.0,  # config.scene_splitter.max_scene_duration
) -> list[dict]:
    """Merge micro-scenes and split macro-scenes."""
    processed = []

    for scene in scenes:
        start_ms = int(scene[0].get_seconds() * 1000)
        end_ms = int(scene[1].get_seconds() * 1000)
        duration = (end_ms - start_ms) / 1000.0

        if duration < min_duration:
            # Merge with previous scene
            if processed:
                processed[-1]["end_ms"] = end_ms
                processed[-1]["duration"] = (end_ms - processed[-1]["start_ms"]) / 1000.0
            else:
                # First scene too short — keep it, will merge with next
                processed.append({
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "duration": duration,
                })
            continue

        if duration > max_duration:
            # Force-split at midpoint
            mid_ms = (start_ms + end_ms) // 2
            processed.append({
                "start_ms": start_ms,
                "end_ms": mid_ms,
                "duration": (mid_ms - start_ms) / 1000.0,
            })
            processed.append({
                "start_ms": mid_ms,
                "end_ms": end_ms,
                "duration": (end_ms - mid_ms) / 1000.0,
            })
        else:
            processed.append({
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration": duration,
            })

    return processed
```

**Rules:**

1. Scene < 3s → merge with previous scene
2. Scene > 20s → split at midpoint
3. Edge case: no scenes detected → treat entire video as one scene

## Timing Format Conversion

| Source                        | Format             | Target                     |
| ----------------------------- | ------------------ | -------------------------- |
| PySceneDetect `FrameTimecode` | seconds (float)    | `int(seconds * 1000)` → ms |
| DTO `start_time` / `end_time` | milliseconds (int) | Used everywhere downstream |
| FFmpeg `-ss` / `-to`          | seconds (float)    | `ms / 1000.0`              |

```python
# PySceneDetect → DTO
start_ms = int(scene[0].get_seconds() * 1000)
end_ms = int(scene[1].get_seconds() * 1000)

# DTO → FFmpeg
ffmpeg_start = start_ms / 1000.0
ffmpeg_end = end_ms / 1000.0
```

## Scene ID Generation

```python
def make_scene_id(video_id: str, start_ms: int, end_ms: int) -> str:
    """Content-addressable scene ID."""
    return f"{video_id}_{start_ms}_{end_ms}"
```

- Deterministic: same video + same boundaries = same ID
- Format: `{video_id}_{start_ms}_{end_ms}`

## Converting to DTOs

```python
from contracts.scenes import SceneSegment, SceneList

def build_scene_list(video_id: str, processed_scenes: list[dict]) -> SceneList:
    segments = tuple(
        SceneSegment(
            scene_id=make_scene_id(video_id, s["start_ms"], s["end_ms"]),
            video_id=video_id,
            start_time=s["start_ms"],
            end_time=s["end_ms"],
            duration=s["duration"],
        )
        for s in processed_scenes
    )
    return SceneList(video_id=video_id, scenes=segments)
```

## Caching / Idempotency

```python
# Check if scenes already exist in DB (resume behavior)
existing = cursor.execute(
    "SELECT COUNT(*) FROM scenes WHERE video_id = ?", (video_id,)
).fetchone()[0]

if existing > 0:
    # Load from DB, skip detection
    rows = cursor.execute(
        "SELECT * FROM scenes WHERE video_id = ? ORDER BY start_time", (video_id,)
    ).fetchall()
    return build_scene_list_from_rows(video_id, rows)
```

## Performance

- **1-hour video:** 2–3 minutes processing (single-pass FFmpeg read)
- **Output:** Typically 50–200 scenes for gameplay content
- **Memory:** Minimal (frame-by-frame processing, no full video in memory)

## Anti-Patterns

```python
# ❌ Using fixed-interval splitting instead of content detection
scenes = split_every_n_seconds(video, 10)  # Ignores content

# ❌ Not enforcing duration constraints
scenes = detect(video, ContentDetector())  # May produce 0.5s scenes

# ❌ Randomized threshold
threshold = random.uniform(20, 35)  # Non-deterministic

# ✅ Deterministic detection + duration enforcement
scenes = detect(video, ContentDetector(threshold=config["scene_splitter"]["threshold"]))
processed = enforce_duration_constraints(scenes, video_id, config["scene_splitter"]["min_scene_duration"], config["scene_splitter"]["max_scene_duration"])
```
