# Shorts Factory — Progress Report

**Last Updated:** 2026-03-25
**Active Phase:** Phase 1 — Core Pipeline Skeleton
**Phase Status:** ✅ COMPLETE (Verified & Audited)

---

## Current Status

| Phase    | Name                   | Status      |
| -------- | ---------------------- | ----------- |
| Phase 0  | Core Infrastructure    | ✅ COMPLETE |
| Phase 1  | Core Pipeline Skeleton | ✅ COMPLETE |
| Phase 2  | Signal Extraction      | ⏳ Pending  |
| Phase 3  | Scoring Engine         | ⏳ Pending  |
| Phase 4  | Clip Builder           | ⏳ Pending  |
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
