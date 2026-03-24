# Shorts Factory — DTO Contract Specification

> **Version:** 1.0
> **Date:** 2026-03-24
> **Status:** Design Phase
> **Type:** Data Contract Specification (Single Source of Truth)
> **Author:** System Architect

This document defines the **exact data contracts** between all modules in the Shorts Factory pipeline. It specifies every field, every type, every constraint, and every invariant for all DTOs (Data Transfer Objects) passed between modules.

This specification is **authoritative**. Where ambiguity exists in other documents, this document takes precedence for all questions of inter-module data format, field definitions, and validation rules.

---

## Table of Contents

- [1. Contract Philosophy](#1-contract-philosophy)
- [2. Global Rules](#2-global-rules)
- [3. Core DTO Definitions](#3-core-dto-definitions)
  - [3.1 IngestionResult](#31-ingestionresult)
  - [3.2 SceneSegment](#32-scenesegment)
  - [3.3 SceneList](#33-scenelist)
  - [3.4 Word](#34-word)
  - [3.5 TranscriptSegment](#35-transcriptsegment)
  - [3.6 Transcript](#36-transcript)
  - [3.7 FaceBBox](#37-facebbox)
  - [3.8 SceneFaceData](#38-scenefacedata)
  - [3.9 FaceDetectionResult](#39-facedetectionresult)
  - [3.10 AudioEnergyData](#310-audioenergydata)
  - [3.11 ScoredScene](#311-scoredscene)
  - [3.12 ScoredSceneList](#312-scoredscenelist)
  - [3.13 ClipDefinition](#313-clipdefinition)
  - [3.14 ClipList](#314-cliplist)
  - [3.15 HookResult](#315-hookresult)
  - [3.16 TTSResult](#316-ttsresult)
  - [3.17 SubtitleResult](#317-subtitleresult)
  - [3.18 CompositeStream](#318-compositestream)
  - [3.19 RenderedClip](#319-renderedclip)
  - [3.20 ThumbnailResult](#320-thumbnailresult)
  - [3.21 MetadataResult](#321-metadataresult)
  - [3.22 StorageRecord](#322-storagerecord)
- [4. Cross-Module Dependencies](#4-cross-module-dependencies)
- [5. Validation Rules](#5-validation-rules)
- [6. Versioning Strategy](#6-versioning-strategy)
- [7. Anti-Patterns](#7-anti-patterns)

---

## 1. Contract Philosophy

### 1.1 DTO-Only Communication

All inter-module data flows through explicitly defined DTO objects. A module receives data only through its declared input DTOs and produces data only through its declared output DTO. There are no side channels.

| Principle                     | Description                                                                                                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Explicit data**             | Every piece of data a module needs is declared in its input DTO. If a field is not in the DTO, the module does not have access to it and must not attempt to obtain it.        |
| **No hidden coupling**        | Modules do not share global state, configuration objects, database connections, or file handles. The orchestrator passes DTOs; modules process them and return DTOs.           |
| **No implicit data**          | A module must not infer information from the absence of a field, from filesystem state, from database queries, or from environment variables. All data is explicit in the DTO. |
| **Contract as documentation** | The DTO definition IS the module interface. Reading a DTO definition tells you everything a module can see and everything it produces.                                         |

### 1.2 DTO Location

All DTOs are defined in the `contracts/` Python package. This package is the only shared package between modules.

```
contracts/
├── __init__.py
├── ingestion.py         # IngestionResult
├── scenes.py            # SceneSegment, SceneList
├── transcript.py        # Word, TranscriptSegment, Transcript
├── face_detection.py    # FaceBBox, SceneFaceData, FaceDetectionResult
├── audio.py             # AudioEnergyData
├── scoring.py           # ScoredScene, ScoredSceneList
├── clips.py             # ClipDefinition, ClipList
├── hooks.py             # HookResult
├── tts.py               # TTSResult
├── subtitles.py         # SubtitleResult
├── compositor.py        # CompositeStream
├── renderer.py          # RenderedClip
├── thumbnail.py         # ThumbnailResult
├── metadata.py          # MetadataResult
└── storage.py           # StorageRecord
```

### 1.3 DTO Implementation Pattern

Every DTO is a Python `dataclass` with `frozen=True`. No exceptions.

```
Pattern:
  @dataclass(frozen=True)
  class DtoName:
      field_a: type
      field_b: type
```

No logic. No methods. No `@property`. No `__post_init__` side effects. DTOs are inert data containers.

---

## 2. Global Rules

### 2.1 Immutability

All DTOs are **frozen dataclasses**. Once created, no field may be modified. If a downstream module needs a modified version of an upstream DTO, it creates a new DTO instance — it never mutates the original.

**Rationale:** Immutability eliminates an entire class of bugs where one module inadvertently modifies data that another module is still using. It also makes the pipeline trivially thread-safe if parallelism is introduced later.

### 2.2 Additive-Only Changes

DTO contracts evolve under strict additive rules:

| Allowed                                       | Forbidden                                                   |
| --------------------------------------------- | ----------------------------------------------------------- |
| Add a new optional field with a default value | Remove an existing field                                    |
| Add a new DTO class                           | Rename an existing field                                    |
| Add a new enum member to a constrained field  | Change a field's type                                       |
| —                                             | Change a field's semantic meaning                           |
| —                                             | Reorder fields (positional construction must remain stable) |

### 2.3 JSON-Serializable

Every field in every DTO must be JSON-serializable. This enables:

- Caching intermediate results to disk for debugging
- Logging DTO snapshots for audit trails
- Reconstructing DTOs from database on pipeline resume

**Allowed types:**

| Python Type     | JSON Representation | Notes                                           |
| --------------- | ------------------- | ----------------------------------------------- |
| `str`           | string              | —                                               |
| `int`           | number              | —                                               |
| `float`         | number              | NaN and Inf are forbidden                       |
| `bool`          | boolean             | —                                               |
| `None`          | null                | Only for optional fields                        |
| `list[T]`       | array               | T must itself be JSON-serializable              |
| `tuple[T, ...]` | array               | Serialized as array; deserialized back to tuple |
| `dict[str, T]`  | object              | Keys MUST be strings                            |
| Nested DTO      | object              | Serialized recursively                          |

**Forbidden types:** `datetime` (use ISO 8601 string), `Path` (use string), `bytes`, `set`, `complex`, any class instance that is not a DTO.

### 2.4 No Methods

DTOs contain zero methods. They do not compute, validate, or transform data. They hold data. Period.

Validation logic lives in:

- **Module input validation** — Each module validates its input DTOs before processing
- **Orchestrator postcondition checks** — The orchestrator validates output DTOs after module execution
- **Factory functions** (optional) — Standalone functions in `contracts/` that construct DTOs with validation, separate from the DTO class itself

### 2.5 No Default Values for Required Fields

Fields that are always populated have no default value. This forces the constructor to receive every required field explicitly, catching missing-data bugs at DTO creation time rather than downstream.

Optional fields (fields that may legitimately be absent) use `None` as their default:

```
required_field: str          # No default → constructor MUST supply it
optional_field: str | None = None  # Default None → constructor MAY omit it
```

### 2.6 String Encoding

All string fields use UTF-8. Paths are stored as POSIX-style strings (forward slashes) relative to the project output root, except for `IngestionResult.path` which is an absolute path to the source video (external to the project).

### 2.7 Timestamp Convention

All timestamps in DTOs are represented as:

- **Millisecond integers** for media-relative timestamps (scene start, word timing, clip boundary)
- **ISO 8601 strings** for wall-clock timestamps (created_at, scheduled_at)

There is no ambiguity: media timestamps are `int` (ms), wall-clock timestamps are `str` (ISO 8601).

---

## 3. Core DTO Definitions

### 3.1 IngestionResult

**Produced by:** Ingestion module
**Consumed by:** Scene Splitter, Transcription, Face Detection, Audio Analysis

| Field              | Type              | Description                           | Constraint                                                                        |
| ------------------ | ----------------- | ------------------------------------- | --------------------------------------------------------------------------------- |
| `video_id`         | `str`             | Deterministic content fingerprint     | `SHA256(first_10MB + str(file_size))[:16]`. Exactly 16 hex characters. Lowercase. |
| `path`             | `str`             | Absolute path to source video file    | Must be an existing, readable file. Not relative.                                 |
| `duration_seconds` | `float`           | Total video duration                  | `1800.0 ≤ duration_seconds ≤ 7200.0` (30–120 minutes)                             |
| `resolution`       | `tuple[int, int]` | Video resolution as `(width, height)` | Both values > 0. Width and height are pixel counts.                               |
| `codec`            | `str`             | Video codec name                      | Non-empty string (e.g., `"h264"`, `"hevc"`, `"vp9"`)                              |
| `audio_codec`      | `str`             | Audio codec name                      | Non-empty string (e.g., `"aac"`, `"opus"`, `"mp3"`)                               |
| `has_audio`        | `bool`            | Whether audio stream is present       | Must be `True` for pipeline to proceed (audio is required)                        |
| `file_size_bytes`  | `int`             | Source file size in bytes             | > 0                                                                               |
| `fps`              | `float`           | Source video frame rate               | > 0                                                                               |

**Identity rule:** `video_id` is computed as `SHA256(first_10MB_bytes + str(file_size_bytes))[:16]`. This is deterministic — the same file always produces the same `video_id`, regardless of filename, path, or timestamp.

**Invariants:**

- `has_audio` is always `True` (pipeline aborts on ingestion if no audio)
- `duration_seconds` is always within [1800.0, 7200.0]
- `video_id` is always exactly 16 lowercase hex characters
- `path` always points to an existing file at the time of DTO creation

---

### 3.2 SceneSegment

**Produced by:** Scene Splitter module (as part of `SceneList`)
**Consumed by:** Scoring, Face Detection, Audio Analysis, Clip Builder (indirectly via ScoredScene)

| Field        | Type    | Description                           | Constraint                                                               |
| ------------ | ------- | ------------------------------------- | ------------------------------------------------------------------------ |
| `scene_id`   | `str`   | Deterministic scene identifier        | Format: `{video_id}_{start_ms}_{end_ms}`. Globally unique per video.     |
| `video_id`   | `str`   | Parent video reference                | Must match `IngestionResult.video_id`. 16 hex chars.                     |
| `start_time` | `int`   | Scene start timestamp in milliseconds | `≥ 0`. Must be < `end_time`.                                             |
| `end_time`   | `int`   | Scene end timestamp in milliseconds   | `> start_time`. Must be ≤ video duration in ms.                          |
| `duration`   | `float` | Scene duration in seconds             | `3.0 ≤ duration ≤ 20.0`. Computed as `(end_time - start_time) / 1000.0`. |

**Identity rule:** `scene_id = f"{video_id}_{start_time}_{end_time}"` — deterministic from content boundaries.

**Invariants:**

- `duration` always equals `(end_time - start_time) / 1000.0`
- `duration` is always in [3.0, 20.0] after boundary enforcement
- No two `SceneSegment` objects within the same `SceneList` have overlapping time ranges
- Scenes are temporally contiguous — there are no gaps between consecutive scenes (the end of scene N equals the start of scene N+1)

---

### 3.3 SceneList

**Produced by:** Scene Splitter module
**Consumed by:** Face Detection, Audio Analysis, Scoring (indirectly)

| Field            | Type                 | Description                          | Constraint                                            |
| ---------------- | -------------------- | ------------------------------------ | ----------------------------------------------------- |
| `video_id`       | `str`                | Parent video reference               | 16 hex chars. Matches `IngestionResult.video_id`.     |
| `scenes`         | `list[SceneSegment]` | Ordered list of scene segments       | Non-empty (≥ 1 scene). Sorted by `start_time ASC`.    |
| `total_duration` | `float`              | Sum of all scene durations (seconds) | Must equal sum of all `SceneSegment.duration` values. |

**Invariants:**

- `scenes` is always sorted by `start_time ASC`
- `len(scenes) ≥ 1` (if no scenes detected, the full video is treated as one scene)
- All scenes reference the same `video_id`
- `total_duration` equals `sum(s.duration for s in scenes)`

---

### 3.4 Word

**Produced by:** Transcription module (as part of `TranscriptSegment`)
**Consumed by:** Subtitle Generator, Metadata Generator (indirectly via Transcript)

| Field        | Type    | Description                            | Constraint                                         |
| ------------ | ------- | -------------------------------------- | -------------------------------------------------- |
| `text`       | `str`   | Single word                            | Non-empty, stripped of leading/trailing whitespace |
| `start_time` | `int`   | Word start timestamp in milliseconds   | `≥ 0`                                              |
| `end_time`   | `int`   | Word end timestamp in milliseconds     | `> start_time`                                     |
| `confidence` | `float` | Transcription confidence for this word | `0.0 ≤ confidence ≤ 1.0`                           |

**Invariants:**

- `start_time < end_time`
- `confidence` is in [0.0, 1.0]
- `text` is never empty

---

### 3.5 TranscriptSegment

**Produced by:** Transcription module (as part of `Transcript`)
**Consumed by:** Scoring, Hook Generator, Subtitle Generator, Metadata Generator (indirectly via Transcript)

| Field        | Type         | Description                                       | Constraint                                                   |
| ------------ | ------------ | ------------------------------------------------- | ------------------------------------------------------------ |
| `text`       | `str`        | Full segment text                                 | May be empty if no speech in this time range                 |
| `start_time` | `int`        | Segment start timestamp in milliseconds           | `≥ 0`                                                        |
| `end_time`   | `int`        | Segment end timestamp in milliseconds             | `> start_time`                                               |
| `words`      | `list[Word]` | Word-level breakdown with timestamps              | May be empty if `text` is empty. Sorted by `start_time ASC`. |
| `confidence` | `float`      | Average transcription confidence for this segment | `0.0 ≤ confidence ≤ 1.0`                                     |

**Invariants:**

- If `text` is non-empty, `words` must be non-empty
- `words` is sorted by `start_time ASC`
- All words fall within `[start_time, end_time]` of the segment
- `start_time < end_time`

---

### 3.6 Transcript

**Produced by:** Transcription module
**Consumed by:** Scoring, Hook Generator, Subtitle Generator, Metadata Generator

| Field         | Type                      | Description                          | Constraint                                                     |
| ------------- | ------------------------- | ------------------------------------ | -------------------------------------------------------------- |
| `video_id`    | `str`                     | Parent video reference               | 16 hex chars                                                   |
| `segments`    | `list[TranscriptSegment]` | Ordered transcript segments          | May be empty (no speech detected). Sorted by `start_time ASC`. |
| `total_words` | `int`                     | Total word count across all segments | `≥ 0`. Must equal `sum(len(s.words) for s in segments)`.       |
| `language`    | `str`                     | Detected language code               | ISO 639-1 code (e.g., `"en"`).                                 |

**Invariants:**

- `segments` is sorted by `start_time ASC`
- `total_words` equals `sum(len(s.words) for s in segments)`
- An empty transcript (no speech detected) is a valid state: `segments = []`, `total_words = 0`
- The transcript covers the full video duration (segments may have gaps where no speech occurs)

---

### 3.7 FaceBBox

**Produced by:** Face Detection module (as part of `SceneFaceData`)
**Consumed by:** Compositor, Thumbnail Generator (indirectly via `FaceDetectionResult`)

| Field          | Type    | Description                        | Constraint                               |
| -------------- | ------- | ---------------------------------- | ---------------------------------------- |
| `x`            | `float` | Bounding box left edge, normalized | `0.0 ≤ x ≤ 1.0`                          |
| `y`            | `float` | Bounding box top edge, normalized  | `0.0 ≤ y ≤ 1.0`                          |
| `width`        | `float` | Bounding box width, normalized     | `0.0 < width ≤ 1.0`. `x + width ≤ 1.0`   |
| `height`       | `float` | Bounding box height, normalized    | `0.0 < height ≤ 1.0`. `y + height ≤ 1.0` |
| `confidence`   | `float` | Detection confidence               | `0.0 ≤ confidence ≤ 1.0`                 |
| `timestamp_ms` | `int`   | Frame timestamp in milliseconds    | `≥ 0`                                    |

**Invariants:**

- All spatial values are in [0.0, 1.0] — normalized to frame dimensions for resolution independence
- `x + width ≤ 1.0` and `y + height ≤ 1.0` (box does not exceed frame boundary)
- `confidence ≥ 0.7` (detections below threshold are filtered out before DTO creation)

---

### 3.8 SceneFaceData

**Produced by:** Face Detection module (as part of `FaceDetectionResult`)
**Consumed by:** Scoring, Compositor, Thumbnail Generator (indirectly via `FaceDetectionResult`)

| Field                | Type             | Description                                   | Constraint                                                      |
| -------------------- | ---------------- | --------------------------------------------- | --------------------------------------------------------------- | ------------------------------------- |
| `scene_id`           | `str`            | Reference to parent scene                     | Must match a valid `SceneSegment.scene_id`                      |
| `face_visible_ratio` | `float`          | Fraction of sampled frames with face detected | `0.0 ≤ face_visible_ratio ≤ 1.0`                                |
| `bounding_boxes`     | `list[FaceBBox]` | Per-frame bounding boxes (2fps sampling)      | Sorted by `timestamp_ms ASC`. May be empty if no face detected. |
| `average_bbox`       | `FaceBBox        | None`                                         | EMA-smoothed average bounding box for the scene                 | `None` if no faces detected in scene. |
| `sample_count`       | `int`            | Number of frames sampled in this scene        | `> 0`. Equals `ceil(scene_duration_seconds * 2)` (2fps).        |

**Invariants:**

- `face_visible_ratio` equals `len(bounding_boxes) / sample_count`
- `bounding_boxes` is sorted by `timestamp_ms ASC`
- If `bounding_boxes` is empty, `average_bbox` is `None` and `face_visible_ratio` is `0.0`
- `average_bbox` uses EMA smoothing with alpha = 0.3

---

### 3.9 FaceDetectionResult

**Produced by:** Face Detection module
**Consumed by:** Scoring, Compositor, Thumbnail Generator

| Field                  | Type                  | Description                                      | Constraint                                             |
| ---------------------- | --------------------- | ------------------------------------------------ | ------------------------------------------------------ |
| `video_id`             | `str`                 | Parent video reference                           | 16 hex chars                                           |
| `scene_data`           | `list[SceneFaceData]` | Per-scene face detection results                 | One entry per scene. Same order as `SceneList.scenes`. |
| `average_visibility`   | `float`               | Mean `face_visible_ratio` across all scenes      | `0.0 ≤ average_visibility ≤ 1.0`                       |
| `faceless_scene_count` | `int`                 | Number of scenes with `face_visible_ratio = 0.0` | `≥ 0`                                                  |

**Invariants:**

- `len(scene_data)` equals `len(SceneList.scenes)` — one entry per scene, no exceptions
- `scene_data` is ordered to match `SceneList.scenes` (by `start_time ASC`)
- `average_visibility` equals `mean(s.face_visible_ratio for s in scene_data)`
- `faceless_scene_count` equals `sum(1 for s in scene_data if s.face_visible_ratio == 0.0)`

---

### 3.10 AudioEnergyData

**Produced by:** Audio Analysis module
**Consumed by:** Scoring

| Field            | Type                     | Description                          | Constraint                                             |
| ---------------- | ------------------------ | ------------------------------------ | ------------------------------------------------------ |
| `video_id`       | `str`                    | Parent video reference               | 16 hex chars                                           |
| `scene_energies` | `list[SceneAudioEnergy]` | Per-scene audio energy measurements  | One entry per scene. Same order as `SceneList.scenes`. |
| `video_min_rms`  | `float`                  | Minimum RMS energy across all scenes | `≥ 0.0`                                                |
| `video_max_rms`  | `float`                  | Maximum RMS energy across all scenes | `≥ video_min_rms`                                      |
| `video_mean_rms` | `float`                  | Mean RMS energy across all scenes    | `video_min_rms ≤ video_mean_rms ≤ video_max_rms`       |

**Nested type — `SceneAudioEnergy`:**

| Field               | Type    | Description                              | Constraint                                                                                                     |
| ------------------- | ------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `scene_id`          | `str`   | Reference to parent scene                | Must match a valid `SceneSegment.scene_id`                                                                     |
| `rms_energy`        | `float` | RMS audio energy for this scene          | `≥ 0.0`                                                                                                        |
| `normalized_energy` | `float` | Energy normalized to [0, 1] within video | `0.0 ≤ normalized_energy ≤ 1.0`. Computed as `(rms_energy - video_min_rms) / (video_max_rms - video_min_rms)`. |

**Invariants:**

- `len(scene_energies)` equals `len(SceneList.scenes)`
- `scene_energies` is ordered to match `SceneList.scenes`
- If `video_max_rms == video_min_rms` (flat audio), all `normalized_energy` values are `0.0`
- `normalized_energy` is precomputed by the Audio Analysis module — the Scoring module does not recalculate it

---

### 3.11 ScoredScene

**Produced by:** Scoring module (as part of `ScoredSceneList`)
**Consumed by:** Clip Builder

| Field                    | Type    | Description                                           | Constraint                                 |
| ------------------------ | ------- | ----------------------------------------------------- | ------------------------------------------ |
| `scene_id`               | `str`   | Reference to parent scene                             | Must match a valid `SceneSegment.scene_id` |
| `video_id`               | `str`   | Parent video reference                                | 16 hex chars                               |
| `start_time`             | `int`   | Scene start (ms) — copied from `SceneSegment`         | `≥ 0`                                      |
| `end_time`               | `int`   | Scene end (ms) — copied from `SceneSegment`           | `> start_time`                             |
| `duration`               | `float` | Scene duration (seconds) — copied from `SceneSegment` | `3.0 ≤ duration ≤ 20.0`                    |
| `keyword_score`          | `float` | Keyword engagement factor                             | `0.0 ≤ keyword_score ≤ 1.0`                |
| `audio_energy_score`     | `float` | Audio energy factor                                   | `0.0 ≤ audio_energy_score ≤ 1.0`           |
| `scene_activity_score`   | `float` | Visual motion factor                                  | `0.0 ≤ scene_activity_score ≤ 1.0`         |
| `face_presence_score`    | `float` | Face visibility factor                                | `0.0 ≤ face_presence_score ≤ 1.0`          |
| `sentence_density_score` | `float` | Speech density factor                                 | `0.0 ≤ sentence_density_score ≤ 1.0`       |
| `composite_score`        | `float` | Weighted composite score                              | `0.0 ≤ composite_score ≤ 1.0`              |
| `rank`                   | `int`   | Position in descending composite_score order          | `≥ 1`. Unique within a `ScoredSceneList`.  |

**Composite score formula:**

```
composite_score = (keyword_score × 3 + audio_energy_score × 2 + face_presence_score × 2
                   + scene_activity_score × 1 + sentence_density_score × 1) / 9
```

**Invariants:**

- All individual scores are in [0.0, 1.0]
- `composite_score` is in [0.0, 1.0] (result of dividing by sum of weights = 9)
- `composite_score` is deterministic — same input signals produce the same composite
- `rank = 1` is the highest-scoring scene
- If two scenes have identical `composite_score`, the scene with earlier `start_time` gets the lower rank number (higher priority)

---

### 3.12 ScoredSceneList

**Produced by:** Scoring module
**Consumed by:** Clip Builder

| Field       | Type                | Description                         | Constraint                                                   |
| ----------- | ------------------- | ----------------------------------- | ------------------------------------------------------------ |
| `video_id`  | `str`               | Parent video reference              | 16 hex chars                                                 |
| `scenes`    | `list[ScoredScene]` | All scenes with scores              | Non-empty. Sorted by `composite_score DESC, start_time ASC`. |
| `min_score` | `float`             | Lowest composite_score in the list  | `0.0 ≤ min_score ≤ 1.0`                                      |
| `max_score` | `float`             | Highest composite_score in the list | `min_score ≤ max_score ≤ 1.0`                                |
| `avg_score` | `float`             | Mean composite_score                | `min_score ≤ avg_score ≤ max_score`                          |

**Invariants:**

- `scenes` is sorted by `composite_score DESC`, with `start_time ASC` as tiebreaker
- `len(scenes)` equals the total number of scenes in the source `SceneList`
- `rank` values are `1` through `len(scenes)`, assigned by this sort order
- `min_score`, `max_score`, `avg_score` are derived from `scenes` — not independently set

---

### 3.13 ClipDefinition

**Produced by:** Clip Builder module (as part of `ClipList`)
**Consumed by:** Hook Generator, Subtitle Generator, Compositor, Thumbnail Generator, Metadata Generator, Storage

| Field           | Type                | Description                                         | Constraint                                                               |
| --------------- | ------------------- | --------------------------------------------------- | ------------------------------------------------------------------------ |
| `clip_id`       | `str`               | Deterministic clip identifier                       | `SHA256(video_id + str(start_time) + str(end_time))[:16]`. 16 hex chars. |
| `video_id`      | `str`               | Parent video reference                              | 16 hex chars                                                             |
| `scenes`        | `list[ScoredScene]` | Constituent scenes in temporal order                | Non-empty. Sorted by `start_time ASC`. All scenes temporally contiguous. |
| `start_time`    | `int`               | Clip start (ms) — equals first scene's `start_time` | `≥ 0`                                                                    |
| `end_time`      | `int`               | Clip end (ms) — equals last scene's `end_time`      | `> start_time`                                                           |
| `duration`      | `float`             | Clip duration in seconds                            | `30.0 ≤ duration ≤ 60.0`                                                 |
| `average_score` | `float`             | Mean `composite_score` of constituent scenes        | `0.0 ≤ average_score ≤ 1.0`                                              |
| `clip_index`    | `int`               | Position in the batch (0-based)                     | `≥ 0`. Unique within a `ClipList`.                                       |

**Identity rule:** `clip_id = SHA256(video_id + str(start_time) + str(end_time))[:16]`

**Invariants:**

- `duration` is always in [30.0, 60.0] — hard floor and hard ceiling
- `duration` equals `(end_time - start_time) / 1000.0`
- `start_time` equals `scenes[0].start_time`
- `end_time` equals `scenes[-1].end_time`
- `scenes` are temporally contiguous: for all i, `scenes[i].end_time == scenes[i+1].start_time`
- `average_score` equals `mean(s.composite_score for s in scenes)`
- `clip_id` is deterministic — same video with same config produces the same `clip_id`

---

### 3.14 ClipList

**Produced by:** Clip Builder module
**Consumed by:** Orchestrator (drives per-clip processing loop)

| Field            | Type                   | Description                        | Constraint                                                              |
| ---------------- | ---------------------- | ---------------------------------- | ----------------------------------------------------------------------- |
| `video_id`       | `str`                  | Parent video reference             | 16 hex chars                                                            |
| `clips`          | `list[ClipDefinition]` | All selected clips                 | Non-empty (≥ 1 clip, target 10–15, max 20). Sorted by `start_time ASC`. |
| `total_clips`    | `int`                  | Number of clips selected           | `1 ≤ total_clips ≤ 20`. Equals `len(clips)`.                            |
| `clips_rejected` | `int`                  | Number of candidate clips rejected | `≥ 0`                                                                   |

**Invariants:**

- `clips` is sorted by `start_time ASC` (chronological processing order)
- `total_clips` equals `len(clips)`
- `1 ≤ total_clips ≤ 20`
- No two clips overlap temporally by more than 50%
- `clip_index` values are `0` through `total_clips - 1`, in `start_time ASC` order

---

### 3.15 HookResult

**Produced by:** Hook Generator module
**Consumed by:** TTS, Thumbnail Generator, Metadata Generator

| Field           | Type        | Description                                            | Constraint                                                 |
| --------------- | ----------- | ------------------------------------------------------ | ---------------------------------------------------------- |
| `clip_id`       | `str`       | Reference to parent clip                               | 16 hex chars. Must match a valid `ClipDefinition.clip_id`. |
| `hook_text`     | `str`       | Attention-grabbing opener                              | Non-empty. Maximum 15 words. 1–2 sentences.                |
| `story_text`    | `str`       | Narrative continuation                                 | Non-empty. 1–2 sentences. Maximum 40 words.                |
| `template_id`   | `str`       | Template identifier used for generation                | Non-empty. Used for debugging and ensuring batch rotation. |
| `keywords_used` | `list[str]` | Keywords extracted from transcript for template params | May be empty if no keywords found (generic template used). |

**Invariants:**

- `len(hook_text.split()) ≤ 15` (word count, not character count)
- `len(story_text.split()) ≤ 40`
- `hook_text` and `story_text` are never empty
- `template_id` is never empty
- Within a single pipeline batch, no two clips share the same `template_id` unless the template pool is exhausted (pool size ≥ 30)

---

### 3.16 TTSResult

**Produced by:** TTS module
**Consumed by:** Subtitle Generator, Renderer

| Field              | Type                 | Description                    | Constraint                                         |
| ------------------ | -------------------- | ------------------------------ | -------------------------------------------------- | ---------------------------------------------------- |
| `clip_id`          | `str`                | Reference to parent clip       | 16 hex chars                                       |
| `audio_path`       | `str`                | Path to synthesized audio file | Relative path. Must point to a valid WAV file.     |
| `duration_seconds` | `float`              | Audio duration in seconds      | `> 0.0`. Must not exceed parent clip's `duration`. |
| `sample_rate`      | `int`                | Audio sample rate in Hz        | `44100` (enforced)                                 |
| `word_timestamps`  | `list[TTSWordTiming] | None`                          | Per-word timing data for subtitle sync             | `None` if engine does not provide word-level timing. |
| `engine`           | `str`                | TTS engine used                | One of: `"edge_tts"`, `"pyttsx3"`                  |

**Nested type — `TTSWordTiming`:**

| Field        | Type  | Description                              | Constraint     |
| ------------ | ----- | ---------------------------------------- | -------------- |
| `word`       | `str` | The spoken word                          | Non-empty      |
| `start_time` | `int` | Word start (ms), relative to audio start | `≥ 0`          |
| `end_time`   | `int` | Word end (ms), relative to audio start   | `> start_time` |

**Invariants:**

- `audio_path` points to an existing WAV file at DTO creation time
- `duration_seconds > 0.0`
- If `word_timestamps` is provided, words are sorted by `start_time ASC`
- `engine` is always one of the two allowed values

---

### 3.17 SubtitleResult

**Produced by:** Subtitle Generator module
**Consumed by:** Renderer

| Field                      | Type   | Description                          | Constraint                                     |
| -------------------------- | ------ | ------------------------------------ | ---------------------------------------------- |
| `clip_id`                  | `str`  | Reference to parent clip             | 16 hex chars                                   |
| `subtitle_path`            | `str`  | Path to ASS subtitle file            | Relative path. Must point to a valid ASS file. |
| `word_count`               | `int`  | Total words in subtitle track        | `≥ 0`                                          |
| `style_preset`             | `str`  | Visual style applied                 | Non-empty (e.g., `"default"`, `"karaoke"`)     |
| `has_narration_subtitles`  | `bool` | Whether TTS narration is subtitled   | —                                              |
| `has_transcript_subtitles` | `bool` | Whether original speech is subtitled | —                                              |

**Invariants:**

- `subtitle_path` points to an existing ASS file at DTO creation time
- `word_count` is consistent with the subtitle file content
- At least one of `has_narration_subtitles` or `has_transcript_subtitles` is `True` (otherwise there is nothing to subtitle)

---

### 3.18 CompositeStream

**Produced by:** Compositor module
**Consumed by:** Renderer

| Field             | Type               | Description                                     | Constraint                                              |
| ----------------- | ------------------ | ----------------------------------------------- | ------------------------------------------------------- |
| `clip_id`         | `str`              | Reference to parent clip                        | 16 hex chars                                            |
| `video_path`      | `str`              | Path to intermediate composite video (no audio) | Relative path. Must point to a valid video file.        |
| `resolution`      | `tuple[int, int]`  | Output resolution                               | Must be `(1080, 1920)`                                  |
| `fps`             | `float`            | Frame rate                                      | Must match source video fps                             |
| `layout_type`     | `str`              | Layout mode used                                | One of: `"face_gameplay_split"`, `"gameplay_only_zoom"` |
| `layout_metadata` | `dict[str, float]` | Layout details                                  | See below                                               |

**`layout_metadata` fields by `layout_type`:**

For `"face_gameplay_split"`:

| Key                     | Type    | Description                         |
| ----------------------- | ------- | ----------------------------------- |
| `gameplay_height_ratio` | `float` | Gameplay region height ratio (0.65) |
| `face_height_ratio`     | `float` | Face region height ratio (0.35)     |
| `face_crop_x`           | `float` | Normalized face crop X position     |
| `face_crop_y`           | `float` | Normalized face crop Y position     |
| `face_crop_width`       | `float` | Normalized face crop width          |
| `face_crop_height`      | `float` | Normalized face crop height         |
| `face_zoom`             | `float` | Zoom factor applied to face (1.2)   |

For `"gameplay_only_zoom"`:

| Key             | Type    | Description                                                                |
| --------------- | ------- | -------------------------------------------------------------------------- |
| `zoom_factor`   | `float` | Zoom level applied (1.1–1.3)                                               |
| `pan_direction` | `str`   | Ken Burns pan direction (`"left_to_right"`, `"right_to_left"`, `"center"`) |

**Invariants:**

- `resolution` is always `(1080, 1920)`
- `video_path` points to an existing video file at DTO creation time
- `layout_type` is always one of the two allowed values
- If `FaceDetectionResult` shows face visibility ≥ 0.3 for this clip's scenes → `layout_type = "face_gameplay_split"`
- If face visibility < 0.3 → `layout_type = "gameplay_only_zoom"`

---

### 3.19 RenderedClip

**Produced by:** Renderer module
**Consumed by:** Storage

| Field              | Type               | Description                | Constraint                                     |
| ------------------ | ------------------ | -------------------------- | ---------------------------------------------- |
| `clip_id`          | `str`              | Reference to parent clip   | 16 hex chars                                   |
| `video_path`       | `str`              | Path to final rendered MP4 | Relative path. Must point to a valid MP4 file. |
| `duration_seconds` | `float`            | Actual rendered duration   | `30.0 ≤ duration_seconds ≤ 60.0`               |
| `file_size_bytes`  | `int`              | Output file size           | `> 0`, `≤ 104857600` (100MB)                   |
| `resolution`       | `tuple[int, int]`  | Output resolution          | Must be `(1080, 1920)`                         |
| `codec`            | `str`              | Video codec used           | `"h264"` (H.264 High Profile)                  |
| `audio_codec`      | `str`              | Audio codec used           | `"aac"`                                        |
| `fps`              | `float`            | Output frame rate          | `30.0`                                         |
| `audio_mix`        | `dict[str, float]` | Audio channel volumes      | `{"gameplay": 0.7, "narration": 0.3}`          |

**Invariants:**

- `resolution` is always `(1080, 1920)`
- `duration_seconds` is always in [30.0, 60.0]
- `file_size_bytes` never exceeds 100MB (re-encoded if necessary)
- `codec` is always `"h264"`
- `fps` is always `30.0`
- `video_path` points to an existing, complete MP4 file (atomic write via `.tmp` rename)

---

### 3.20 ThumbnailResult

**Produced by:** Thumbnail Generator module
**Consumed by:** Storage

| Field                | Type              | Description                                   | Constraint                                      |
| -------------------- | ----------------- | --------------------------------------------- | ----------------------------------------------- |
| `clip_id`            | `str`             | Reference to parent clip                      | 16 hex chars                                    |
| `image_path`         | `str`             | Path to thumbnail JPEG                        | Relative path. Must point to a valid JPEG file. |
| `resolution`         | `tuple[int, int]` | Thumbnail resolution                          | Must be `(1280, 720)`                           |
| `text_overlay`       | `str`             | Text rendered on the thumbnail                | 2–3 words maximum. Non-empty.                   |
| `face_visible`       | `bool`            | Whether a face is present in the thumbnail    | —                                               |
| `frame_timestamp_ms` | `int`             | Source frame timestamp used for the thumbnail | `≥ clip start_time`, `≤ clip end_time`          |
| `frame_score`        | `float`           | Frame selection score                         | `≥ 0.0`                                         |

**Invariants:**

- `resolution` is always `(1280, 720)`
- `len(text_overlay.split()) ≤ 3`
- `text_overlay` is never empty
- `image_path` points to an existing JPEG file at DTO creation time
- If any face-containing frame exists in the clip → `face_visible = True` (face is prioritized)

---

### 3.21 MetadataResult

**Produced by:** Metadata Generator module
**Consumed by:** Storage

| Field         | Type        | Description              | Constraint                             |
| ------------- | ----------- | ------------------------ | -------------------------------------- |
| `clip_id`     | `str`       | Reference to parent clip | 16 hex chars                           |
| `title`       | `str`       | Video title              | 40–60 characters. Contains 1–2 emojis. |
| `description` | `str`       | Video description        | 150–300 characters. Contains hashtags. |
| `tags`        | `list[str]` | Video tags               | 10–15 tags. Total characters ≤ 500.    |
| `category`    | `str`       | YouTube category         | One of: `"Gaming"`, `"Entertainment"`  |

**Invariants:**

- `40 ≤ len(title) ≤ 60` (character count)
- `150 ≤ len(description) ≤ 300` (character count)
- `10 ≤ len(tags) ≤ 15`
- `sum(len(t) for t in tags) + len(tags) - 1 ≤ 500` (total tag characters with comma separators)
- `title` is unique within a pipeline batch — no two clips share the same title
- `category` is one of the allowed values
- All tags are non-empty strings

---

### 3.22 StorageRecord

**Produced by:** Storage module
**Consumed by:** Scheduler, Publisher

| Field             | Type             | Description                                  | Constraint                                                                  |
| ----------------- | ---------------- | -------------------------------------------- | --------------------------------------------------------------------------- | --------------------------------------- |
| `clip_id`         | `str`            | Primary identifier                           | 16 hex chars                                                                |
| `video_id`        | `str`            | Parent video reference                       | 16 hex chars                                                                |
| `status`          | `str`            | Lifecycle state                              | One of: `"generated"`, `"queued"`, `"scheduled"`, `"published"`, `"failed"` |
| `composite_score` | `float`          | Clip average score (for scheduling priority) | `0.0 ≤ composite_score ≤ 1.0`                                               |
| `file_paths`      | `dict[str, str]` | Paths to all stored artifacts                | See required keys below                                                     |
| `title`           | `str`            | Stored title (from MetadataResult)           | 40–60 characters                                                            |
| `description`     | `str`            | Stored description                           | 150–300 characters                                                          |
| `tags`            | `list[str]`      | Stored tags                                  | 10–15 tags                                                                  |
| `category`        | `str`            | YouTube category                             | `"Gaming"` or `"Entertainment"`                                             |
| `created_at`      | `str`            | Record creation timestamp                    | ISO 8601 format                                                             |
| `scheduled_at`    | `str             | None`                                        | Assigned publish timestamp                                                  | ISO 8601 or `None` if not yet scheduled |
| `published_at`    | `str             | None`                                        | Actual publish timestamp                                                    | ISO 8601 or `None` if not yet published |
| `youtube_id`      | `str             | None`                                        | YouTube video ID after upload                                               | `None` until published                  |
| `error_message`   | `str             | None`                                        | Failure description                                                         | `None` unless status is `"failed"`      |
| `retry_count`     | `int`            | Number of publish retries attempted          | `0 ≤ retry_count ≤ 3`                                                       |

**Required `file_paths` keys:**

| Key           | Description                | Constraint                    |
| ------------- | -------------------------- | ----------------------------- |
| `"video"`     | Path to final rendered MP4 | Must be a valid relative path |
| `"thumbnail"` | Path to thumbnail JPEG     | Must be a valid relative path |
| `"metadata"`  | Path to metadata JSON      | Must be a valid relative path |
| `"subtitles"` | Path to ASS subtitle file  | Must be a valid relative path |
| `"narration"` | Path to TTS audio WAV      | Must be a valid relative path |

**Invariants:**

- `status` transitions follow the lifecycle: `generated → queued → scheduled → published | failed`
- No backward transitions except: `failed → scheduled` (manual retry, max 3×)
- `file_paths` always contains all five required keys
- All file paths point to existing files when `status` is `"queued"` or later
- `scheduled_at` is non-null when `status` is `"scheduled"` or later
- `published_at` is non-null only when `status` is `"published"`
- `youtube_id` is non-null only when `status` is `"published"`
- `error_message` is non-null only when `status` is `"failed"`
- `retry_count ≤ 3`

---

## 4. Cross-Module Dependencies

### 4.1 Module-to-DTO Dependency Matrix

This table defines exactly which DTOs each module receives as input and which DTO it produces. There are no undeclared dependencies.

| Module                  | Receives (Input DTOs)                                               | Produces (Output DTO)           |
| ----------------------- | ------------------------------------------------------------------- | ------------------------------- |
| **Ingestion**           | File path (`str`)                                                   | `IngestionResult`               |
| **Scene Splitter**      | `IngestionResult`                                                   | `SceneList`                     |
| **Transcription**       | `IngestionResult`                                                   | `Transcript`                    |
| **Face Detection**      | `IngestionResult`, `SceneList`                                      | `FaceDetectionResult`           |
| **Audio Analysis**      | `IngestionResult`, `SceneList`                                      | `AudioEnergyData`               |
| **Scoring**             | `SceneList`, `Transcript`, `FaceDetectionResult`, `AudioEnergyData` | `ScoredSceneList`               |
| **Clip Builder**        | `ScoredSceneList`                                                   | `ClipList`                      |
| **Hook Generator**      | `ClipDefinition`, `Transcript`                                      | `HookResult`                    |
| **TTS**                 | `HookResult`                                                        | `TTSResult`                     |
| **Subtitle Generator**  | `Transcript`, `TTSResult`, `ClipDefinition`                         | `SubtitleResult`                |
| **Compositor**          | `ClipDefinition`, `FaceDetectionResult`                             | `CompositeStream`               |
| **Renderer**            | `CompositeStream`, `TTSResult`, `SubtitleResult`                    | `RenderedClip`                  |
| **Thumbnail Generator** | `ClipDefinition`, `FaceDetectionResult`, `HookResult`               | `ThumbnailResult`               |
| **Metadata Generator**  | `HookResult`, `Transcript`, `ClipDefinition`                        | `MetadataResult`                |
| **Storage**             | `RenderedClip`, `ThumbnailResult`, `MetadataResult`                 | `StorageRecord`                 |
| **Scheduler**           | `list[StorageRecord]`                                               | `list[StorageRecord]` (updated) |
| **Publisher**           | `StorageRecord`                                                     | `StorageRecord` (updated)       |

### 4.2 Data Flow Graph

```
File path (str)
  │
  ▼
IngestionResult ──┬──→ Scene Splitter ──→ SceneList ──┬──→ Face Detection ──→ FaceDetectionResult ──┐
                  │                                    │                                              │
                  │                                    ├──→ Audio Analysis ──→ AudioEnergyData ───────┤
                  │                                    │                                              │
                  ├──→ Transcription ──→ Transcript ───┤                                              │
                  │                                    │                                              │
                  │                                    └──→ Scoring ◄────────────────────────────────┘
                  │                                              │
                  │                                              ▼
                  │                                        ScoredSceneList
                  │                                              │
                  │                                              ▼
                  │                                        Clip Builder ──→ ClipList
                  │                                                            │
                  │                                        ┌───────────────────┤
                  │                                        │    FOR EACH CLIP  │
                  │                                        │                   │
                  │     Transcript ◄────────────────────── │ ──→ Hook Gen ──→ HookResult ──┬──→ TTS ──→ TTSResult
                  │                                        │                               │              │
                  │     FaceDetectionResult ◄───────────── │ ──→ Compositor ──→ CompositeStream          │
                  │                                        │                       │                      │
                  │     Transcript + TTSResult ◄────────── │ ──→ Subtitle Gen ──→ SubtitleResult         │
                  │                                        │                       │                      │
                  │                                        │     Renderer ◄────────┴──────────────────────┘
                  │                                        │        │
                  │                                        │        ▼
                  │                                        │   RenderedClip
                  │                                        │
                  │     HookResult + FaceDetectionResult ◄ │ ──→ Thumbnail Gen ──→ ThumbnailResult
                  │                                        │
                  │     HookResult + Transcript ◄───────── │ ──→ Metadata Gen ──→ MetadataResult
                  │                                        │
                  │                                        │     Storage ◄── RenderedClip + ThumbnailResult + MetadataResult
                  │                                        │        │
                  │                                        │        ▼
                  │                                        │   StorageRecord
                  │                                        └───────────────────────┘
                  │
                  │   Scheduler ◄── list[StorageRecord] ──→ updated list[StorageRecord]
                  │
                  │   Publisher ◄── StorageRecord ──→ updated StorageRecord
```

### 4.3 Strict Boundary Rules

| Rule                                  | Description                                                                                                                                                                                    |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **No transitive access**              | Hook Generator receives `ClipDefinition` and `Transcript`. It does NOT receive `FaceDetectionResult`, even though the orchestrator has it. A module sees only what is declared in Section 4.1. |
| **No DTO mutation in transit**        | The orchestrator passes DTOs by reference. No module may modify a DTO it receives. Immutability (frozen dataclass) enforces this at runtime.                                                   |
| **No DTO creation outside contracts** | Modules must not define ad-hoc data structures (dicts, tuples, named tuples) for inter-module data. All inter-module data uses DTOs from `contracts/`.                                         |
| **No file path sharing outside DTOs** | One module cannot tell another module "read from this path" except through a DTO field. The path is a declared, validated field — not a side-channel convention.                               |

---

## 5. Validation Rules

### 5.1 Field-Level Validation

Every DTO field has an explicit type and constraint (defined in Section 3). Validation is enforced at two points:

**Point 1 — Construction time (module output):**

Each module validates its own output DTO before returning it. If a field violates its constraint (e.g., `duration < 30.0` for a `ClipDefinition`), the module raises an exception. The orchestrator catches this and routes to the failure handler.

**Point 2 — Postcondition check (orchestrator):**

After each stage, the orchestrator performs postcondition validation on the output DTO:

| Stage          | Postcondition Check                                                                     |
| -------------- | --------------------------------------------------------------------------------------- |
| Ingestion      | `video_id` is 16 hex chars, `duration_seconds` in [1800, 7200], `has_audio` is True     |
| Scene Splitter | `len(scenes) ≥ 1`, all durations in [3.0, 20.0], sorted by `start_time`                 |
| Transcription  | `Transcript` object returned (empty is valid)                                           |
| Face Detection | `len(scene_data)` equals scene count                                                    |
| Audio Analysis | `len(scene_energies)` equals scene count                                                |
| Scoring        | All `composite_score` values in [0.0, 1.0], ranks are sequential                        |
| Clip Builder   | All clip durations in [30.0, 60.0], `1 ≤ total_clips ≤ 20`                              |
| Hook Generator | `hook_text` ≤ 15 words, `story_text` ≤ 40 words, both non-empty                         |
| TTS            | `audio_path` exists, `duration_seconds > 0`                                             |
| Subtitle       | `subtitle_path` exists                                                                  |
| Compositor     | `resolution` is (1080, 1920), `video_path` exists                                       |
| Renderer       | `resolution` is (1080, 1920), `duration_seconds` in [30, 60], `file_size_bytes ≤ 100MB` |
| Thumbnail      | `resolution` is (1280, 720), `image_path` exists, `text_overlay` ≤ 3 words              |
| Metadata       | `title` 40–60 chars, `tags` 10–15 items                                                 |
| Storage        | All `file_paths` keys present, all files exist                                          |
| Scheduler      | All updated records have `scheduled_at` set, status is `"scheduled"`                    |

### 5.2 Invalid State Handling

When validation fails, the system follows a strict protocol:

| Scope                   | Invalid State                         | Response                                                          |
| ----------------------- | ------------------------------------- | ----------------------------------------------------------------- |
| **Video-level stage**   | Output DTO fails postcondition        | Stage failure → pipeline aborts (status = `failed`)               |
| **Clip-level stage**    | Output DTO fails postcondition        | Clip failure → skip clip, continue to next clip                   |
| **Threshold breach**    | > 50% of clips fail                   | Pipeline aborts (status = `failed`)                               |
| **Construction error**  | Module cannot construct a valid DTO   | Exception propagated to orchestrator → same as stage/clip failure |
| **Type mismatch**       | Module returns wrong DTO type         | Exception at postcondition check → stage failure                  |
| **Null required field** | A required field (no default) is None | Construction-time TypeError → module failure                      |

### 5.3 File Path Validation

For any DTO field containing a file path:

```
1. The path must be a valid string (non-empty, no null bytes)
2. The file must exist at the time the DTO is created
3. The file must be readable by the current process
4. For output files: verified via os.path.exists() after writing
5. For relative paths: resolved relative to output/{video_id}/
6. For absolute paths (IngestionResult.path only): verified as absolute
```

### 5.4 Cross-Reference Validation

DTOs that reference identities from upstream DTOs must be validated for consistency:

| Referencing DTO                     | Referenced Field           | Must Match                     |
| ----------------------------------- | -------------------------- | ------------------------------ |
| `SceneSegment.video_id`             | `IngestionResult.video_id` | Exact match                    |
| `SceneFaceData.scene_id`            | `SceneSegment.scene_id`    | Exact match                    |
| `SceneAudioEnergy.scene_id`         | `SceneSegment.scene_id`    | Exact match                    |
| `ScoredScene.scene_id`              | `SceneSegment.scene_id`    | Exact match                    |
| `ClipDefinition.video_id`           | `IngestionResult.video_id` | Exact match                    |
| `ClipDefinition.scenes[*].scene_id` | `SceneSegment.scene_id`    | Each must exist in `SceneList` |
| `HookResult.clip_id`                | `ClipDefinition.clip_id`   | Exact match                    |
| `TTSResult.clip_id`                 | `ClipDefinition.clip_id`   | Exact match                    |
| `SubtitleResult.clip_id`            | `ClipDefinition.clip_id`   | Exact match                    |
| `CompositeStream.clip_id`           | `ClipDefinition.clip_id`   | Exact match                    |
| `RenderedClip.clip_id`              | `ClipDefinition.clip_id`   | Exact match                    |
| `ThumbnailResult.clip_id`           | `ClipDefinition.clip_id`   | Exact match                    |
| `MetadataResult.clip_id`            | `ClipDefinition.clip_id`   | Exact match                    |
| `StorageRecord.clip_id`             | `ClipDefinition.clip_id`   | Exact match                    |
| `StorageRecord.video_id`            | `IngestionResult.video_id` | Exact match                    |

---

## 6. Versioning Strategy

### 6.1 DTO Version Numbering

Each DTO carries an implicit version tied to this specification document. The specification version (currently `1.0`) applies to all DTOs. When any DTO definition changes, the specification version is bumped.

| Change Type                              | Version Bump          | Example                                                     |
| ---------------------------------------- | --------------------- | ----------------------------------------------------------- |
| New optional field added to existing DTO | Patch (`1.0` → `1.1`) | Add `encoding_preset` to `RenderedClip` with default `None` |
| New DTO class added                      | Minor (`1.0` → `1.1`) | Add `QualityMetrics` DTO                                    |
| Field type changed                       | Major (`1.0` → `2.0`) | Change `start_time` from `int` to `float`                   |
| Field removed                            | Major (`1.0` → `2.0`) | Remove `confidence` from `Word`                             |
| Field renamed                            | Major (`1.0` → `2.0`) | Rename `path` to `file_path`                                |

### 6.2 Backward Compatibility

**Adding a field is safe** if and only if:

- The field has a default value (`None`, `""`, `0`, `[]`)
- No existing module depends on the absence of the field
- The field's default value is a valid state (e.g., `None` for an optional feature)

**Removing a field is always a breaking change.** Even if no module currently reads the field, removal violates the additive-only contract and may break serialization/deserialization of cached DTOs.

### 6.3 Cached DTO Compatibility

On pipeline resume, DTOs are reconstructed from the database. The reconstruction logic must handle:

- Missing fields (added after the original run) → use default value
- Unknown fields (from a newer spec) → ignore silently

This ensures a pipeline run started with spec v1.0 can resume under spec v1.1 (which only adds optional fields), but NOT under spec v2.0 (which changes or removes fields).

### 6.4 Spec Version Tracking

The `pipeline_runs` table stores `config_snapshot`, which includes the DTO spec version. On resume:

```
IF stored_spec_version.major != current_spec_version.major:
    → ABORT: incompatible DTO version, cannot resume safely
IF stored_spec_version.minor < current_spec_version.minor:
    → WARN: DTO spec updated, new optional fields may not be populated for resumed clips
    → CONTINUE: backward-compatible
IF stored_spec_version == current_spec_version:
    → CONTINUE: exact match
```

---

## 7. Anti-Patterns

The following patterns are **explicitly forbidden** in the Shorts Factory codebase. Any code review must reject these patterns on sight.

### 7.1 Passing Raw Dicts

**Forbidden:**

```
# WRONG — raw dict with no type safety
result = {"video_id": "abc123", "duration": 3600}
next_module.process(result)
```

**Required:**

```
# CORRECT — typed DTO
result = IngestionResult(video_id="abc123...", ...)
next_module.process(result)
```

**Why:** Raw dicts have no type checking, no IDE support, no validation, and no documentation. A misspelled key (`"duraton"` instead of `"duration"`) silently produces `None` instead of raising an error.

### 7.2 Accessing Other Module's Internal State

**Forbidden:**

```
# WRONG — Hook Generator directly reading scene splitter's internal file
scenes = json.load(open("output/video_id/scenes/scene_001.json"))
```

**Required:**

```
# CORRECT — Hook Generator receives ClipDefinition DTO from orchestrator
def generate_hook(clip: ClipDefinition, transcript: Transcript) -> HookResult:
    ...
```

**Why:** Modules do not know about each other's file layouts, database schemas, or internal state. If the scene splitter changes its file format, no other module should break.

### 7.3 Hidden Dependencies

**Forbidden:**

```
# WRONG — Module reads config directly to get info about another module
config = load_config()
face_detection_threshold = config["face_detection"]["threshold"]
```

**Required:**

```
# CORRECT — Module receives only its declared input DTOs
# Face detection threshold is internal to the face detection module
# Other modules see only FaceDetectionResult, which contains the output
```

**Why:** If module A reads module B's configuration, A is implicitly coupled to B. Changing B's config may break A in ways that are invisible until runtime.

### 7.4 DTO Mutation

**Forbidden:**

```
# WRONG — Modifying a received DTO
def process(clip: ClipDefinition):
    clip.duration = clip.duration + 1  # Raises FrozenInstanceError, but don't try
```

**Required:**

```
# CORRECT — Create new instance if modification is needed (rare)
# Modules should almost never need to create modified copies of input DTOs
```

**Why:** DTOs are shared references. Mutation creates invisible side effects for all consumers.

### 7.5 Non-DTO Return Types

**Forbidden:**

```
# WRONG — Module returns a tuple
def run_scoring(scenes, transcript):
    return (scored_list, min_score, max_score)
```

**Required:**

```
# CORRECT — Module returns a DTO
def run_scoring(scenes: SceneList, ...) -> ScoredSceneList:
    return ScoredSceneList(scenes=..., min_score=..., max_score=..., ...)
```

**Why:** Tuples are positional, unnamed, and unversioned. Adding a fourth return value breaks all callers. DTOs are named, typed, and extensible.

### 7.6 Importing Module Internals

**Forbidden:**

```
# WRONG — Hook generator importing scene splitter internals
from modules.scene_splitter.detector import PySceneDetectWrapper
```

**Required:**

```
# CORRECT — Only import from contracts/
from contracts.scenes import SceneSegment, SceneList
from contracts.clips import ClipDefinition
```

**Why:** Module internals are private. Only the `contracts/` package is the public API surface.

### 7.7 Using Global State as Data Channel

**Forbidden:**

```
# WRONG — Setting a global variable for another module to read
LAST_FACE_DETECTION = None

def detect_faces(...):
    global LAST_FACE_DETECTION
    LAST_FACE_DETECTION = result
    return result
```

**Required:**

```
# CORRECT — Return DTO; orchestrator passes it forward
def detect_faces(...) -> FaceDetectionResult:
    return FaceDetectionResult(...)
```

**Why:** Global state creates invisible coupling and makes the pipeline non-deterministic (execution order could change the value).

### 7.8 Database Queries as Module Communication

**Forbidden:**

```
# WRONG — Compositor querying the database to get face detection data
import sqlite3
conn = sqlite3.connect("shorts.db")
face_data = conn.execute("SELECT * FROM scenes WHERE ...").fetchall()
```

**Required:**

```
# CORRECT — Compositor receives FaceDetectionResult DTO from orchestrator
def compose(clip: ClipDefinition, face_data: FaceDetectionResult) -> CompositeStream:
    ...
```

**Why:** The database is for persistence, not communication. Module A does not know (or care) how module B stores its data. The orchestrator is the sole agent that mediates data flow between modules.

---

_End of DTO contract specification._
