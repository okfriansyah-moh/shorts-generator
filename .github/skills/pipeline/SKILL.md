---
name: pipeline
description: "Pipeline reasoning for Shorts Factory. Use when validating stage ordering, understanding dependencies between modules, checking checkpoint behavior, or planning parallel development. Provides the 16-stage sequence, DTO flow map, and parallelism matrix."
---

# Pipeline Reasoning Skill

## When to Use

- Validating that pipeline stages execute in correct order
- Understanding which DTOs flow between which stages
- Planning parallel development tracks
- Implementing or reviewing orchestrator logic
- Writing integration tests that span multiple stages

## 16-Stage Sequence (Immutable)

```
Stage  0: ingestion
Stage  1: scene_splitter
Stage  2: transcription
Stage  3: face_detection
Stage  4: scoring
Stage  5: clip_builder
Stage  6: hook_generator      ← per-clip from here
Stage  7: tts
Stage  8: subtitle
Stage  9: compositor
Stage 10: renderer
Stage 11: thumbnail
Stage 12: metadata
Stage 13: storage
Stage 14: scheduler
Stage 15: publisher
```

**Rules:**

- Never reorder stages
- Never skip stages
- Never parallelize stages at runtime
- Stages 0–5 are video-level (run once per video)
- Stages 6–13 are per-clip (run once per clip in the ClipList)
- Stages 14–15 are batch-level (run once after all clips processed)

## DTO Flow Map

```
                    file_path
                        │
                  ┌─────▼──────┐
                  │  ingestion  │
                  └─────┬──────┘
                  IngestionResult
                   │    │    │
         ┌─────────┘    │    └──────────┐
         ▼              ▼               ▼
  ┌──────────┐   ┌──────────┐   ┌──────────────┐
  │scene_split│   │transcript│   │face_detection │
  └────┬─────┘   └────┬─────┘   └──────┬───────┘
   SceneList      Transcript     FaceDetectionResult
       │              │               │
       └──────┬───────┘───────────────┘
              ▼
        ┌──────────┐
        │  scoring  │
        └────┬─────┘
       ScoredSceneList
              │
        ┌─────▼──────┐
        │ clip_builder │
        └─────┬──────┘
           ClipList
              │
     ┌────────┼─────── FOR EACH CLIP ──────────┐
     ▼        ▼                                  ▼
  hook_gen → tts → subtitle → compositor → renderer
     │                                        │
     ├──────────► thumbnail                   │
     ├──────────► metadata                    │
     │                                        │
     └──────────► storage ◄───────────────────┘
                    │
               scheduler
                    │
               publisher
```

## Checkpoint Behavior Per Stage

All checkpoint writes go through `database/adapter.py` — see `docs/db_adapter_spec.md`.

| Stage           | Checkpoint Target                                  | Resume Strategy                   |
| --------------- | -------------------------------------------------- | --------------------------------- |
| ingestion       | `pipeline_runs.last_completed_stage = 'ingestion'` | Re-read from `videos` table       |
| scene_splitter  | `last_completed_stage = 'scene_splitter'`          | Re-read from `scenes` table       |
| transcription   | `last_completed_stage = 'transcription'`           | Re-read from DB or cached DTO     |
| face_detection  | `last_completed_stage = 'face_detection'`          | Re-read from DB or cached DTO     |
| scoring         | `last_completed_stage = 'scoring'`                 | Re-read scored scenes from DB     |
| clip_builder    | `last_completed_stage = 'clip_builder'`            | Re-read clips from `clips` table  |
| per-clip stages | `clips.status` per clip                            | Skip clips with status ≥ 'queued' |
| scheduler       | `last_completed_stage = 'scheduler'`               | Skip clips already scheduled      |
| publisher       | `last_completed_stage = 'publisher'`               | Skip clips already published      |

## Development Parallelism Matrix

```
Phase 0  ──→ [core infrastructure]             ← must complete first
Phase 1  ──→ [ingestion] [scene_splitter]      ← sequential
Phase 2  ──→ [transcription] [face_detection]  ← PARALLEL (independent inputs)
Phase 3  ──→ [scoring]                         ← depends on Phase 2
Phase 4  ──→ [clip_builder]                    ← depends on Phase 3
Phase 5  ──→ [compositor]                      ← depends on Phase 4
Phase 6  ──→ [hook_gen] [tts] [subtitle] [renderer] ← hook/tts/subtitle PARALLEL
Phase 7  ──→ [thumbnail] [metadata]            ← PARALLEL (independent)
Phase 8  ──→ [storage] [scheduler]             ← minimal coupling
Phase 9  ──→ [publisher]                       ← depends on Phase 8
Phase 10 ──→ [analytics]                       ← can start after Phase 8
```

## Stage Dependencies (Input Requirements)

| Stage          | Requires                                      | Cannot Run Without |
| -------------- | --------------------------------------------- | ------------------ |
| scene_splitter | IngestionResult                               | Phase 0+1          |
| transcription  | IngestionResult                               | Phase 1            |
| face_detection | IngestionResult, SceneList                    | Phase 1            |
| scoring        | SceneList, Transcript, FaceDetectionResult    | Phase 2            |
| clip_builder   | ScoredSceneList                               | Phase 3            |
| hook_generator | ClipDefinition, Transcript                    | Phase 4, Phase 2   |
| compositor     | ClipDefinition, FaceDetectionResult           | Phase 4, Phase 2   |
| renderer       | CompositeStream, TTSResult, SubtitleResult    | Phase 5, Phase 6   |
| storage        | RenderedClip, ThumbnailResult, MetadataResult | Phase 6, Phase 7   |

## Phase-to-Directory Ownership (STRICT)

Each phase owns specific directories. **NEVER modify files outside your phase's ownership.**

| Phase   | Owned Directories                                                     | May Modify `database/` | May Modify `docs/` |
| ------- | --------------------------------------------------------------------- | ---------------------- | ------------------- |
| Phase 0 | `core/`, `database/`, `config/`, `run_pipeline.py`                    | **Yes**                | No                  |
| Phase 1 | `modules/ingestion/`, `modules/scene_splitter/`, corresponding tests  | **No**                 | No                  |
| Phase 2 | `modules/transcription/`, `modules/face_detection/`, `modules/audio_analysis/`, corresponding tests | **No** | No |
| Phase 3 | `modules/scoring/`, `tests/unit/test_scoring.py`                      | **No**                 | No                  |
| Phase 4 | `modules/clip_builder/`, `tests/unit/test_clip_builder.py`            | **No**                 | No                  |
| Phase 5 | `modules/compositor/`, `tests/unit/test_compositor.py`                | **No**                 | No                  |
| Phase 6 | `modules/hook_generator/`, `modules/tts/`, `modules/subtitle/`, `modules/renderer/`, corresponding tests | **No** | No |
| Phase 7 | `modules/thumbnail/`, `modules/metadata/`, corresponding tests        | **No**                 | No                  |
| Phase 8 | `modules/storage/`, `modules/scheduler/`, corresponding tests         | **No**                 | No                  |
| Phase 9 | `modules/publisher/`, `tests/unit/test_publisher.py`                  | **No**                 | No                  |

**Rules:**
- `contracts/` — Any phase may ADD new DTO files. No phase may modify existing DTO fields.
- Module `__init__.py` MUST use relative imports: `from .X import Y`, NOT `from modules.X.Y import Y`.
- Violation of these rules triggers automatic pipeline rollback.
