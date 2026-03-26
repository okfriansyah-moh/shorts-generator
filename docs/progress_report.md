# Shorts Factory — Progress Report

**Last Updated:** 2026-03-26
**Active Phase:** Phase 4 — Clip Builder
**Phase Status:** ✅ COMPLETE (Verified & Audited — Post-Merge Review)

---

## Current Status

| Phase    | Name                   | Status      |
| -------- | ---------------------- | ----------- |
| Phase 0  | Core Infrastructure    | ✅ COMPLETE |
| Phase 1  | Core Pipeline Skeleton | ✅ COMPLETE |
| Phase 2  | Signal Extraction      | ✅ COMPLETE |
| Phase 3  | Scoring Engine         | ✅ COMPLETE |
| Phase 4  | Clip Builder           | ✅ COMPLETE |
| Phase 5  | Hook Generator         | ⏳ Pending  |
| Phase 6  | TTS & Subtitles        | ⏳ Pending  |
| Phase 7  | Compositor & Renderer  | ⏳ Pending  |
| Phase 8  | Thumbnail & Metadata   | ⏳ Pending  |
| Phase 9  | Storage & Scheduler    | ⏳ Pending  |
| Phase 10 | Observability          | ⏳ Pending  |

---

## Phase 0 — Core Infrastructure

**Status:** ✅ COMPLETE

### Completed Tasks

- [x] Create `core/config.py` with YAML loader, validation, and environment override logic
- [x] Create `core/logging.py` with structured JSON formatter and dual-output (stdout + file)
- [x] Create `core/dependencies.py` with FFmpeg/FFprobe/Python version checks
- [x] Write all four migration SQL scripts
- [x] Create `database/connection.py` with SQLite WAL mode and migration runner
- [x] Create `database/adapter.py` — single DB entry point for orchestrator
- [x] Create `config/config.yaml` with all default values documented
- [x] Create `run_pipeline.py` skeleton (arg parsing, config load, dependency check, exit)
- [x] Create `core/orchestrator.py` skeleton (stage list, no implementation)
- [x] Initialize `contracts/` package with `__init__.py`
- [x] Write unit tests for config validation (valid config, missing fields, invalid types)
- [x] Write unit tests for migration idempotency
- [x] Write integration test: startup → config load → DB init → dependency check → clean exit

### Files Created

| File Path                                                           | Purpose                                               |
| ------------------------------------------------------------------- | ----------------------------------------------------- |
| `core/config.py`                                                    | YAML config loader with validation + env overrides    |
| `core/logging.py`                                                   | Structured JSON formatter, stdout + file dual output  |
| `core/dependencies.py`                                              | FFmpeg/FFprobe/Python version checks at startup       |
| `core/orchestrator.py`                                              | 16-stage pipeline constants, stage index helpers      |
| `core/__init__.py`                                                  | Package init                                          |
| `database/adapter.py`                                               | DatabaseAdapter: single entry point for all DB access |
| `database/connection.py`                                            | SQLite connection setup, WAL mode, migration runner   |
| `database/__init__.py`                                              | Package init                                          |
| `database/migrations/20260324000001_create_videos_table.sql`        | Creates `videos` table with indexes                   |
| `database/migrations/20260324000002_create_scenes_table.sql`        | Creates `scenes` table with indexes                   |
| `database/migrations/20260324000003_create_clips_table.sql`         | Creates `clips` table with indexes                    |
| `database/migrations/20260324000004_create_pipeline_runs_table.sql` | Creates `pipeline_runs` table with indexes            |
| `config/config.yaml`                                                | All default configuration values documented           |
| `run_pipeline.py`                                                   | CLI entry point: arg parse, config, deps, DB init     |
| `contracts/__init__.py`                                             | Shared DTO package (empty, prepared for Phase 1+)     |
| `tests/unit/test_config.py`                                         | Config loader validation tests                        |
| `tests/unit/test_database.py`                                       | Migration idempotency and connection tests            |
| `tests/unit/test_adapter.py`                                        | DatabaseAdapter CRUD operation tests                  |
| `tests/unit/test_logging.py`                                        | Structured JSON formatter tests                       |
| `tests/unit/test_dependencies.py`                                   | FFmpeg/FFprobe/Python check tests                     |
| `tests/unit/test_orchestrator.py`                                   | Pipeline stage constant + index tests                 |
| `tests/integration/test_startup.py`                                 | Full startup integration test                         |
| `tests/conftest.py`                                                 | Shared fixtures: sample_config, test_db, sample_video |

### Exit Criteria

- [x] Configuration loads from `config.yaml` with all fields validated
- [x] Environment variable overrides work for all configuration keys
- [x] Structured JSON logging writes to stdout
- [x] Per-run log file writes to `output/{video_id}/pipeline.log`
- [x] SQLite database created with all four tables and indexes
- [x] FFmpeg and FFprobe availability verified at startup
- [x] Python version check passes (≥ 3.10)
- [x] Repeated startup produces identical state (idempotent)
- [x] `run_pipeline.py` accepts a video file path argument and validates it exists

### Test Results

- **66 tests passing** for Phase 0 modules
- **0 lint errors** (ruff clean)

---

## Phase 1 — Core Pipeline Skeleton

**Status:** ✅ COMPLETE

### Completed Tasks

- [x] Define `IngestionResult` DTO in `contracts/ingestion.py`
- [x] Define `SceneList` and `SceneSegment` DTOs in `contracts/scene.py`
- [x] Implement `modules/ingestion/ingest.py` with FFprobe validation and SHA-256 fingerprinting
- [x] Implement `modules/scene_splitter/split.py` with PySceneDetect integration
- [x] Implement scene post-processing (merge micro-scenes, split long scenes)
- [x] Update `core/orchestrator.py` to wire ingestion → scene_splitter
- [x] Write unit tests for ingestion (valid file, missing file, unsupported format, no audio, out of range)
- [x] Write unit tests for scene splitter (normal video, static video, flickering video)
- [x] Write unit test for `video_id` determinism
- [x] Write integration test: orchestrator wires ingestion → scene_splitter → valid SceneList output
- [x] Harden `database/adapter.py` to enforce `SceneSegment` DTO boundaries and perform internal ms↔sec conversion for scene persistence
- [x] Harden `core/orchestrator.py` with bounded per-stage retry, structured failure classification, and safer checkpoint/status handling
- [x] Upgrade structured logging with retry/error observability fields and per-run file reconfiguration after `video_id` is known
- [x] Remove hardcoded scene splitter fallback constants by moving fallback threshold/target duration into `config/config.yaml`
- [x] Add a dedicated hardening test suite covering DTO enforcement, type conversion, retry behavior, failure classification, terminal-state handling, and observability fields

### Files Created

| File Path                            | Purpose                                                                  |
| ------------------------------------ | ------------------------------------------------------------------------ |
| `contracts/ingestion.py`             | `IngestionResult` frozen dataclass DTO                                   |
| `contracts/scene.py`                 | `SceneSegment` and `SceneList` frozen dataclass DTOs                     |
| `modules/ingestion/__init__.py`      | Package init, exports `ingest` and `IngestionError`                      |
| `modules/ingestion/ingest.py`        | FFprobe validation, SHA-256 fingerprinting, `IngestionResult`            |
| `modules/scene_splitter/__init__.py` | Package init, exports `split_scenes` and `SceneSplitterError`            |
| `modules/scene_splitter/split.py`    | PySceneDetect integration, post-processing, `SceneList`                  |
| `contracts/errors.py`                | Structured pipeline error types and deterministic classification         |
| `tests/unit/test_ingestion.py`       | Ingestion unit tests (format, duration, audio, determinism)              |
| `tests/unit/test_scene_splitter.py`  | Scene splitter unit tests (merge, split, determinism)                    |
| `tests/unit/test_hardening.py`       | Hardening tests for DTO boundaries, retries, state handling, and logging |
| `tests/integration/test_phase1.py`   | Phase 1 integration tests (orchestrator wiring, idempotency)             |

### Exit Criteria

- [x] `IngestionResult` DTO defined with all fields from architecture spec
- [x] `SceneList` and `SceneSegment` DTOs defined with all fields
- [x] Ingestion validates MP4/MKV/AVI formats, rejects unsupported
- [x] Ingestion rejects videos without audio stream
- [x] Ingestion rejects videos outside 30–120 minute range
- [x] `video_id` is deterministic (same file → same ID on every run)
- [x] Scene splitter produces identical boundaries on repeated runs
- [x] No scene shorter than 3 seconds in output
- [x] No scene longer than 20 seconds in output
- [x] Scenes inserted into SQLite with deterministic `scene_id`
- [x] Rerun skips already-processed video and scenes
- [x] Scene persistence uses DTO-only boundaries with adapter-managed ms↔sec conversion
- [x] Ingestion and scene splitting execute with bounded deterministic retries
- [x] Pipeline failures are classified into structured error types for logging and state updates
- [x] Non-terminal run lookup excludes `partial`, `failed`, and `completed` states correctly
- [x] Structured logs include retry/error observability fields for stage attempts and durations
- [x] Structured logs include explicit `status` field (success/failed/skipped) per roadmap spec
- [x] Orchestrator-level idempotency verified (skip INSERT when video exists, return cached scenes)
- [x] Fail-fast behavior verified (stage failure → pipeline returns None, status marked "failed")
- [x] Scene and video INSERT idempotency verified (ON CONFLICT DO NOTHING produces no duplicates)

### Test Results

- **161 tests passing** across Phase 0 + Phase 1 modules (including 41 hardening tests)
- **0 lint errors** (ruff clean)

### Architecture Compliance

- ✅ No cross-module imports between `modules/*` packages
- ✅ DTOs are frozen dataclasses (`frozen=True`)
- ✅ No `sqlite3`/`psycopg2` imports in `modules/`
- ✅ All logs use `logging` module — no `print()`
- ✅ Deterministic: `video_id = SHA256(first_10MB + str(file_size))[:16]`
- ✅ Content-addressable `scene_id = {video_id}_{start_ms}_{end_ms}`
- ✅ Config values read from `config.yaml` — no hardcoded thresholds
- ✅ Adapter is the scene time conversion boundary: DTOs stay in ms, SQLite storage remains in seconds
- ✅ Pipeline run state handling treats `partial` as terminal for resume/active-run queries
- ✅ Failure handling and retry behavior are deterministic and bounded
- ✅ All public function signatures have type annotations
- ✅ Tests pass without GPU, without network, without real video files

---

## Phase 2 — Transcription & Signal Extraction

**Status:** ✅ COMPLETE

### Completed Tasks

- [x] Define `Transcript`, `TranscriptSegment`, `Word` DTOs in `contracts/transcript.py`
- [x] Define `FaceDetectionResult`, `SceneFaceData`, `FaceBBox` DTOs in `contracts/face.py`
- [x] Define `SceneAudioEnergy`, `AudioEnergyData` DTOs in `contracts/audio.py`
- [x] Implement `modules/transcription/transcribe.py` with faster-whisper, word-level timestamps
- [x] Implement `modules/face_detection/detect.py` with MediaPipe, 2fps sampling, EMA smoothing
- [x] Implement `modules/audio_analysis/analyze.py` with FFmpeg RMS extraction
- [x] Update `core/orchestrator.py` to wire scene_splitter → [transcription, face_detection, audio_analysis]
- [x] Write unit tests for transcription (speech present, no speech, confidence scores, frozen DTOs)
- [x] Write unit tests for face detection (face visible, no face, multiple faces, EMA smoothing correctness)
- [x] Write unit tests for audio energy (varying energy, flat energy, normalization range, FFmpeg failure)
- [x] Write integration tests: full signal extraction chain, empty signal graceful paths

### Files Created

| File Path                             | Purpose                                                                      |
| ------------------------------------- | ---------------------------------------------------------------------------- |
| `contracts/transcript.py`             | `Word`, `TranscriptSegment`, `Transcript` frozen DTOs                        |
| `contracts/face.py`                   | `FaceBBox`, `SceneFaceData`, `FaceDetectionResult` frozen DTOs               |
| `contracts/audio.py`                  | `SceneAudioEnergy`, `AudioEnergyData` frozen DTOs                            |
| `modules/transcription/__init__.py`   | Package init, exports `transcribe`                                           |
| `modules/transcription/transcribe.py` | faster-whisper integration, FFmpeg audio extraction, word-level timestamps   |
| `modules/face_detection/__init__.py`  | Package init, exports `detect_faces`                                         |
| `modules/face_detection/detect.py`    | MediaPipe face detection, 2fps sampling via FFmpeg, EMA smoothing            |
| `modules/audio_analysis/__init__.py`  | Package init, exports `analyze_audio`                                        |
| `modules/audio_analysis/analyze.py`   | FFmpeg astats RMS extraction, per-scene normalization to [0, 1]              |
| `tests/unit/test_transcription.py`    | Unit tests: word timestamps, empty speech, FFmpeg failure, frozen DTOs       |
| `tests/unit/test_face_detection.py`   | Unit tests: EMA smoothing, no-face, multiple scenes, normalized coordinates  |
| `tests/unit/test_audio_analysis.py`   | Unit tests: normalization, flat audio, RMS parsing, FFmpeg failure           |
| `tests/integration/test_phase2.py`    | Integration tests: full signal extraction chain, empty signal graceful paths |

### Exit Criteria

- [x] `Transcript`, `TranscriptSegment`, `Word` DTOs defined with all fields
- [x] `FaceDetectionResult`, `SceneFaceData`, `FaceBBox` DTOs defined with all fields
- [x] Transcription produces word-level timestamps (not just segment-level)
- [x] Transcription returns empty result for videos with no speech (not an error)
- [x] Face detection samples at 2fps, not every frame
- [x] Face detection applies EMA smoothing with configurable alpha
- [x] Face detection returns normalized bounding boxes (0–1 range)
- [x] Audio energy extraction returns per-scene normalized RMS values
- [x] All three modules are independently testable with mock `IngestionResult` and `SceneList`
- [x] Integration test: signal extraction with mocked dependencies → correct DTO shapes

### Test Results

- **212 tests passing** across Phase 0 + Phase 1 + Phase 2 modules
- **0 lint errors** (ruff clean)

### Architecture Compliance

- ✅ No cross-module imports between `modules/*` packages
- ✅ All DTOs are frozen dataclasses (`frozen=True`)
- ✅ No `sqlite3`/`psycopg2` imports in `modules/`
- ✅ All logs use `logging` module — no `print()`
- ✅ Config values read from `config.yaml` — no hardcoded thresholds
- ✅ All public function signatures have type annotations
- ✅ Tests pass without GPU, without network, without real video files
- ✅ FFmpeg used for all audio/frame extraction (no Python video libraries)
- ✅ Deterministic: same input + same config = identical Transcript, FaceDetectionResult, AudioEnergyData
- ✅ Normalized coordinates: all face bounding boxes in [0, 1] range
- ✅ Word-level timestamps: transcription produces per-word timing, not just segments
- ✅ Graceful empty signals: no speech/no face/flat audio → valid empty DTOs, not errors

---

## Phase 3 — Scoring Engine

**Status:** ✅ COMPLETE

### Completed Tasks

- [x] Define `ScoredScene`, `ScoredSceneList` DTOs in `contracts/scoring.py`
- [x] Implement `modules/scoring/score.py` — five-factor weighted composite scoring
- [x] Implement `modules/scoring/keywords.py` — keyword engagement scoring with configurable keyword list
- [x] Implement `modules/scoring/activity.py` — scene activity via FFmpeg inter-frame pixel difference
- [x] Implement min-max normalization of composite scores across all scenes
- [x] Implement temporal fallback for degenerate case (all identical scores)
- [x] Implement sentence density scoring (optimal 2–4 wps range)
- [x] Wire scoring module into orchestrator pipeline
- [x] Write unit tests: keyword scoring, sentence density, audio/face passthrough, activity fallback, composite weighting, normalization, degenerate case, determinism, full `process()` integration
- [x] Write integration tests: signal chain compatibility, deterministic ordering, graceful missing signals

### Files Created

| File Path                          | Purpose                                                                 |
| ---------------------------------- | ----------------------------------------------------------------------- |
| `contracts/scoring.py`             | `ScoredScene`, `ScoredSceneList` frozen DTOs with `rank` and aggregates |
| `modules/scoring/__init__.py`      | Package init, exports `process`                                         |
| `modules/scoring/score.py`         | Five-factor scoring engine, normalization, temporal fallback            |
| `modules/scoring/keywords.py`      | Keyword extraction and density scoring                                  |
| `modules/scoring/activity.py`      | FFmpeg-based inter-frame pixel difference for scene activity            |
| `tests/unit/test_scoring.py`       | Unit tests: all five factors, weighting, normalization, determinism     |
| `tests/integration/test_phase3.py` | Integration tests: signal chain, DTO compatibility, ordering            |

### Exit Criteria

- [x] `ScoredScene` DTO has all 12 fields: scene_id, video_id, start_time, end_time, duration, keyword_score, audio_energy_score, face_presence_score, scene_activity_score, sentence_density_score, composite_score, rank
- [x] `ScoredSceneList` DTO has aggregate fields: min_score, max_score, avg_score
- [x] Composite score formula: `(keyword×3 + audio_energy×2 + face_presence×2 + scene_activity×1 + sentence_density×1) / 9`
- [x] All individual scores normalized to [0.0, 1.0]
- [x] Composite scores min-max normalized across all scenes
- [x] Missing signals (no transcript, no face, no audio) default to 0.0 — not errors
- [x] Deterministic: same input + same config = identical ScoredSceneList
- [x] Temporal fallback when all scores identical — produces spread across video
- [x] Weights configurable from `config.yaml` — no hardcoded values
- [x] Scenes ranked by composite_score DESC, start_time ASC as tiebreaker

### Test Results

- **262 tests passing** across Phase 0 + Phase 1 + Phase 2 + Phase 3 modules
- **0 lint errors** (ruff clean)

### Architecture Compliance

- ✅ No cross-module imports between `modules/*` packages
- ✅ All DTOs are frozen dataclasses (`frozen=True`)
- ✅ No `sqlite3`/`psycopg2` imports in `modules/`
- ✅ All logs use `logging` module — no `print()`
- ✅ Config values read from `config.yaml` — no hardcoded thresholds
- ✅ All public function signatures have type annotations
- ✅ Tests pass without GPU, without network, without real video files
- ✅ Deterministic: same input + same config = identical ScoredSceneList
- ✅ FFmpeg used for scene activity computation (no Python video libraries)
- ✅ Graceful degradation: missing signals default to 0.0, not errors
- ✅ DTO field names match `docs/dto_contracts.md` spec and database column names

---

## Phase 4 — Clip Builder

**Status:** ✅ COMPLETE

### Completed Tasks

- [x] Define `ClipDefinition`, `ClipList` DTOs in `contracts/clip.py`
- [x] Implement `modules/clip_builder/build.py` — greedy nucleus expansion algorithm
- [x] Implement duration enforcement (30–60 second hard floor/ceiling)
- [x] Implement contiguity requirement (no gaps between merged scenes)
- [x] Implement rejection criteria (low score, excessive overlap > 50%)
- [x] Implement threshold-lowering fallback (up to 3 retries, −0.05 each)
- [x] Implement deterministic clip_id: `SHA256(video_id + str(start_time) + str(end_time))[:16]`
- [x] Implement max_clips_per_run cap (default 20)
- [x] Wire clip_builder module into orchestrator pipeline
- [x] Write unit tests: basic building, duration enforcement, contiguity, rejection, threshold lowering, deterministic IDs, determinism, edge cases
- [x] Write integration tests (part of test_phase3.py signal chain verification)

### Files Created

| File Path                          | Purpose                                                               |
| ---------------------------------- | --------------------------------------------------------------------- |
| `contracts/clip.py`                | `ClipDefinition`, `ClipList` frozen DTOs                              |
| `modules/clip_builder/__init__.py` | Package init, exports `process`                                       |
| `modules/clip_builder/build.py`    | Greedy nucleus expansion clip building, rejection, threshold fallback |
| `tests/unit/test_clip_builder.py`  | Unit tests: building, duration, contiguity, rejection, determinism    |

### Exit Criteria

- [x] `ClipDefinition` DTO has all 8 fields: clip_id, video_id, scenes, start_time, end_time, duration, average_score, clip_index
- [x] `ClipList` DTO has: video_id, clips, total_clips, clips_rejected
- [x] All clips strictly within 30–60 second duration range
- [x] Clips contain only temporally contiguous scenes
- [x] No scene appears in more than one clip
- [x] No two clips overlap by more than 50%
- [x] `clip_id = SHA256(video_id + str(start_time) + str(end_time))[:16]`
- [x] `average_score = mean(composite_score for scenes in clip)`
- [x] Deterministic: same input + same config = identical ClipList
- [x] Threshold lowering produces clips when initial threshold too aggressive
- [x] Clips capped at `max_clips_per_run` from pipeline config
- [x] `ValueError` raised when no valid clips can be produced

### Test Results

- **289 tests passing** across Phase 0 + Phase 1 + Phase 2 + Phase 3 + Phase 4 modules
- **0 lint errors** (ruff clean)

### Architecture Compliance

- ✅ No cross-module imports between `modules/*` packages
- ✅ All DTOs are frozen dataclasses (`frozen=True`)
- ✅ No `sqlite3`/`psycopg2` imports in `modules/`
- ✅ All logs use `logging` module — no `print()`
- ✅ Config values read from `config.yaml` — no hardcoded thresholds
- ✅ All public function signatures have type annotations
- ✅ Tests pass without GPU, without network, without real video files
- ✅ Deterministic: same input + same config = identical ClipList
- ✅ Content-addressable clip IDs via SHA256
- ✅ Duration constraints enforced at build time, not validated post-hoc
