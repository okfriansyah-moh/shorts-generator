---
name: dto
description: "DTO interpretation and validation for Shorts Factory. Use when creating, modifying, reviewing, or consuming frozen dataclass DTOs from contracts/. Provides the full 22-DTO registry, field types, constraints, producer/consumer mapping, and anti-patterns."
---

# DTO Interpretation Skill

## When to Use

- Creating a new module that consumes or produces DTOs
- Reviewing code for DTO contract compliance
- Validating field types, ranges, and constraints
- Checking producer/consumer relationships

## DTO Registry (22 DTOs)

### Video-Level DTOs

| DTO                   | File                      | Producer                 | Consumers                                     | Key Constraints                                                |
| --------------------- | ------------------------- | ------------------------ | --------------------------------------------- | -------------------------------------------------------------- |
| `IngestionResult`     | `contracts/ingestion.py`  | ingestion                | scene_splitter, transcription, face_detection | `video_id`: 16 hex, duration: [30–120]min, `has_audio`=True    |
| `SceneSegment`        | `contracts/scenes.py`     | (in SceneList)           | all downstream                                | duration: [3–20]s, `scene_id`=`{video_id}_{start_ms}_{end_ms}` |
| `SceneList`           | `contracts/scenes.py`     | scene_splitter           | face_detection, scoring                       | non-empty, sorted by `start_time`, no gaps                     |
| `Word`                | `contracts/transcript.py` | (in Transcript)          | —                                             | non-empty text, confidence: [0.0–1.0]                          |
| `TranscriptSegment`   | `contracts/transcript.py` | (in Transcript)          | —                                             | sorted by `start_time`, words non-empty if text non-empty      |
| `Transcript`          | `contracts/transcript.py` | transcription            | scoring, hook_generator, subtitle, metadata   | may be empty (no speech), sorted by `start_time`               |
| `FaceBBox`            | `contracts/face.py`       | (in FaceDetectionResult) | —                                             | all coords [0.0–1.0], confidence ≥ 0.7                         |
| `SceneFaceData`       | `contracts/face.py`       | (in FaceDetectionResult) | —                                             | `visible_ratio` = len(boxes) / sample_count                    |
| `FaceDetectionResult` | `contracts/face.py`       | face_detection           | scoring, compositor, thumbnail                | one entry per scene, sorted by `start_time`                    |
| `SceneAudioEnergy`    | `contracts/audio.py`      | (in AudioEnergyData)     | —                                             | `normalized_energy`: [0.0–1.0]                                 |
| `AudioEnergyData`     | `contracts/audio.py`      | audio_analysis           | scoring                                       | one entry per scene                                            |
| `ScoredScene`         | `contracts/scoring.py`    | (in ScoredSceneList)     | —                                             | all scores [0.0–1.0], composite = weighted avg / 9             |
| `ScoredSceneList`     | `contracts/scoring.py`    | scoring                  | clip_builder                                  | sorted by composite DESC then start_time ASC                   |

### Clip-Level DTOs

| DTO               | File                      | Producer       | Consumers                                           | Key Constraints                                                                  |
| ----------------- | ------------------------- | -------------- | --------------------------------------------------- | -------------------------------------------------------------------------------- |
| `ClipDefinition`  | `contracts/clips.py`      | (in ClipList)  | hook_gen, subtitle, compositor, thumbnail, metadata | duration: [30–60]s, `clip_id`=SHA256(video_id+start+end)[:16], contiguous scenes |
| `ClipList`        | `contracts/clips.py`      | clip_builder   | orchestrator                                        | 1–20 clips, sorted by `start_time`, no >50% overlap                              |
| `HookResult`      | `contracts/hooks.py`      | hook_generator | tts, thumbnail, metadata                            | hook ≤ 15 words, story ≤ 40 words                                                |
| `TTSWordTiming`   | `contracts/tts.py`        | (in TTSResult) | —                                                   | non-empty word, start < end                                                      |
| `TTSResult`       | `contracts/tts.py`        | tts            | subtitle, renderer                                  | 44100 Hz, duration > 0, valid audio file                                         |
| `SubtitleResult`  | `contracts/subtitles.py`  | subtitle       | renderer                                            | valid ASS file, at least one subtitle type                                       |
| `CompositeStream` | `contracts/compositor.py` | compositor     | renderer                                            | resolution (1080, 1920), layout ∈ {face_gameplay_split, gameplay_only_zoom}      |
| `RenderedClip`    | `contracts/renderer.py`   | renderer       | storage                                             | [30–60]s, (1080,1920), h264, 30fps, ≤ 100MB                                      |
| `ThumbnailResult` | `contracts/thumbnail.py`  | thumbnail      | storage                                             | (1280,720) JPEG, text ≤ 3 words                                                  |
| `MetadataResult`  | `contracts/metadata.py`   | metadata       | storage                                             | title: 40–60 chars, desc: 150–300 chars, 10–15 tags                              |
| `StorageRecord`   | `contracts/storage.py`    | storage        | scheduler, publisher                                | status ∈ {generated, queued, scheduled, published, failed}                       |

## Validation Rules

### All DTOs

- Must be `@dataclass(frozen=True)`
- No methods, no properties, no `__post_init__` logic
- All fields typed (PEP 484)
- JSON-serializable only: `str`, `int`, `float`, `bool`, `None`, `list`, `tuple`, nested DTOs
- Forbidden types: `datetime`, `Path`, `bytes`, `set`, `complex`, class instances

### Versioning

- **Additive only**: new fields may be added (with defaults)
- **Never remove or rename** existing fields
- DTO changes merge to `main` BEFORE module changes that depend on them

## Anti-Patterns

```python
# ❌ Raw dict instead of DTO
result = {"video_id": "abc123", "duration": 3600}

# ❌ Mutable dataclass
@dataclass  # Missing frozen=True
class MyDTO: ...

# ❌ Logic in DTO
@dataclass(frozen=True)
class MyDTO:
    def validate(self): ...  # No methods allowed

# ❌ Cross-module type
from modules.scoring.internal import ScoreResult  # Forbidden

# ✅ Correct usage
from contracts.scoring import ScoredSceneList
result: ScoredSceneList = scoring.process(input_dto, config)
```
