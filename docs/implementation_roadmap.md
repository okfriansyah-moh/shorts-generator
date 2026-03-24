# Shorts Factory — Implementation Roadmap

This document defines the **deterministic implementation roadmap** for the Shorts Factory autonomous content production pipeline.

The system is implemented as a **modular monolith executed as a single Python process**.

The architecture follows the **sequential batch pipeline model**:

```
Input Video
→ Ingestion (validate + fingerprint)
→ Scene Splitting (PySceneDetect + FFmpeg)
→ Transcription (faster-whisper, word-level)
→ Face Detection (MediaPipe, 2fps sampling)
→ Scoring Engine (rule-based, 5-factor composite)
→ Clip Builder (merge scenes → 30–60s segments)
→ Hook Generator (template-based narration)
→ TTS Synthesis (Edge TTS)
→ Subtitle Generation (word-level ASS)
→ Compositor (face + gameplay → 9:16 layout)
→ Final Renderer (merge all layers → MP4)
→ Thumbnail Generator (frame selection + text overlay)
→ Metadata Generator (title, description, tags)
→ Storage (SQLite + filesystem)
→ Scheduler (daily publish queue)
→ Publisher (YouTube Data API v3)
```

The system operates using a **SQLite state machine** and deterministic orchestrator pipeline. Every pipeline run is idempotent, resumable, and produces identical outputs for identical inputs.

---

## Table of Contents

- [System Priority Layer](#system-priority-layer)
- [Pipeline Lifecycle Specification](#pipeline-lifecycle-specification)
- [Module Interface Contract Specification](#module-interface-contract-specification)
- [Execution Safety Rules](#execution-safety-rules)
- [Logging Requirements](#logging-requirements)
- [Phase 0 — Core Infrastructure](#phase-0--core-infrastructure)
- [Phase 1 — Core Pipeline Skeleton](#phase-1--core-pipeline-skeleton)
- [Phase 2 — Transcription & Signal Extraction](#phase-2--transcription--signal-extraction)
- [Phase 3 — Scoring Engine](#phase-3--scoring-engine)
- [Phase 4 — Clip Builder](#phase-4--clip-builder)
- [Phase 5 — Composition Engine](#phase-5--composition-engine)
- [Phase 6 — Rendering Pipeline](#phase-6--rendering-pipeline)
- [Phase 7 — Metadata & Thumbnail Generation](#phase-7--metadata--thumbnail-generation)
- [Phase 8 — Storage & Scheduling](#phase-8--storage--scheduling)
- [Phase 9 — Publisher](#phase-9--publisher)
- [Phase 10 — Observability & Analytics](#phase-10--observability--analytics)
- [Parallel Development Strategy](#parallel-development-strategy)
- [Deterministic System Invariants](#deterministic-system-invariants)
- [Not In This Roadmap](#not-in-this-roadmap)
- [Architecture Protection Rules](#architecture-protection-rules)

---

## System Priority Layer

> **IMPORTANT:** This section does NOT replace phases. It does NOT change phase ordering. It only defines **which existing phases are production-critical** and annotates them with hard enforcement requirements. All phases remain in their current sequence.

### Purpose

The implementation roadmap defines _what_ to build and _in what order_. This layer defines _what matters most for producing usable Shorts_ — ensuring that development effort is prioritized toward output-generating capabilities.

### P0 — Execution Blockers (Must Complete Before Pipeline Produces Any Usable Output)

These phases are **hard prerequisites** for producing a single valid Short. Without them, the system cannot generate output.

| Phase       | Name                   | Why It's P0                                                                      |
| ----------- | ---------------------- | -------------------------------------------------------------------------------- |
| **Phase 0** | Core Infrastructure    | No config → no logging → no file structure → nothing runs                        |
| **Phase 1** | Core Pipeline Skeleton | No ingestion → no scene splitting → no raw material for clips                    |
| **Phase 4** | Clip Builder           | No clip builder → scenes cannot be merged into 30–60s segments → no valid Shorts |
| **Phase 6** | Rendering Pipeline     | No renderer → no final MP4 output → nothing to publish                           |

**Phase 0 hard requirements:**

- Configuration loader must validate all required paths and dependencies before any module executes
- SQLite database must be initialized with all four tables (`videos`, `clips`, `scenes`, `pipeline_runs`)
- Filesystem output structure must be created atomically
- Logging must produce structured JSON to both stdout and per-run log files

**Phase 1 hard requirements:**

- Ingestion must compute deterministic `video_id` from SHA-256 of first 10MB + file size
- Scene splitter must enforce min 3s / max 20s scene boundaries
- FFmpeg and FFprobe must be validated as available before any video processing begins

**Phase 4 hard requirements:**

- Clips must be exactly 30–60 seconds (hard floor, hard ceiling)
- Only temporally contiguous scenes may be merged
- Clip IDs must be deterministic: `SHA256(video_id + start_ms + end_ms)[:16]`

**Phase 6 hard requirements:**

- Output must be 1080x1920, H.264 High Profile, 30fps
- Duration must be validated post-render — reject any output outside 30–60s
- Audio mix must combine gameplay (70%) and narration (30%)
- Subtitle burn-in must be hardcoded into the video stream

**Gate:** The system MUST NOT attempt to store or schedule any clip until Phase 6 passes end-to-end integration testing with a real video input.

### P1 — Core Production Engine (Modules That Directly Create Quality Shorts)

These phases form the **quality generation core**. They already exist in the roadmap — this layer clarifies their critical importance.

| Phase       | Name                    | Why It's P1                                                                                                   |
| ----------- | ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Phase 2** | Transcription & Signals | Provides `Transcript` — the word-level speech data required for scoring, hooks, subtitles, and metadata       |
| **Phase 3** | Scoring Engine          | Provides `ScoredSceneList` — the ranked scene data required for intelligent clip selection                    |
| **Phase 5** | Composition Engine      | Provides face-aware 9:16 layout — the visual composition that differentiates a produced Short from a raw clip |

**Enforcement requirement (applies to existing Phase 2/3/5 logic):**

- Transcription MUST produce word-level timestamps (not just segment-level) — subtitle alignment depends on this
- Scoring MUST use all five factors (keyword, audio_energy, face_presence, scene_activity, sentence_density) with configurable weights
- If face detection returns zero visibility for a clip, the compositor MUST fall back to full-gameplay layout — never produce a broken or empty frame
- If transcription returns empty (no speech detected), scoring MUST still function using remaining signals (audio energy, scene activity, face presence)

### P1.5 — Quality & Optimization Layer (Improves Output Quality Without Blocking Production)

These phases improve the quality and discoverability of produced Shorts but are not required for producing a valid output.

| Phase       | Name                 | Why It's P1.5                                                                                                                      |
| ----------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **Phase 7** | Metadata & Thumbnail | Generates thumbnails, titles, descriptions, tags — critical for CTR and discovery but not for video production                     |
| **Phase 8** | Storage & Scheduling | Persists clips with lifecycle management and assigns publish dates — required for automated publishing but not for clip generation |

**Enforcement requirement (applies to existing Phase 7/8 logic):**

- Thumbnail MUST contain a visible face if any face-containing frame exists in the clip
- Title MUST be 40–60 characters with 1–2 emojis and an emotional trigger word
- No two clips in the same batch may share the same title
- Storage MUST use `INSERT ... ON CONFLICT DO NOTHING` semantics — idempotent by design
- Scheduler MUST order by composite score descending (best clips publish first)

### P2 — Enhancements (Optional / Non-Blocking)

These phases improve the system but are not required for producing or publishing Shorts.

| Phase        | Name                      | Impact                                                                         |
| ------------ | ------------------------- | ------------------------------------------------------------------------------ |
| **Phase 9**  | Publisher                 | Uploads to YouTube; manual upload remains a fallback                           |
| **Phase 10** | Observability & Analytics | Pipeline metrics, performance tracking, quality analytics                      |
| Future       | GPU Acceleration          | Faster transcription, face detection, and encoding via CUDA/NVENC              |
| Future       | Multi-Language Support    | Localized transcription, hooks, metadata, and subtitles                        |
| Future       | Scoring Weight Tuning     | Data-driven adjustment of scoring weights based on published Short performance |

No changes to dependency ordering. These proceed as currently specified in the phase sequence.

---

## Pipeline Lifecycle Specification

Every pipeline run and every clip follows a deterministic lifecycle. State transitions are forward-only and persisted in SQLite.

### Pipeline Run States

| State       | Description                                                              |
| ----------- | ------------------------------------------------------------------------ |
| `started`   | Pipeline run initiated, video ingested, `pipeline_runs` row created      |
| `analyzing` | Scene splitting, transcription, and face detection in progress           |
| `building`  | Scoring, clip building, and per-clip processing in progress              |
| `completed` | All clips processed, stored, and scheduled                               |
| `partial`   | Pipeline completed but some clips failed individual processing           |
| `failed`    | Pipeline-level failure (ingestion failed, FFmpeg unavailable, disk full) |

### Pipeline Run Transitions

```
started → analyzing
analyzing → building
building → completed
building → partial       (some clips failed, others succeeded)
started → failed         (ingestion or validation failure)
analyzing → failed       (scene splitter or transcription failure)
building → failed        (all clips failed)
```

### Clip States

| State       | Description                                      |
| ----------- | ------------------------------------------------ |
| `generated` | All assets produced (video, thumbnail, metadata) |
| `queued`    | Assets verified, ready for scheduling            |
| `scheduled` | Publish date assigned                            |
| `published` | Successfully uploaded to YouTube                 |
| `failed`    | Upload failed after max retries                  |

### Clip Transitions

```
generated → queued
queued → scheduled
scheduled → published
scheduled → failed
failed → scheduled       (manual retry, max 3×)
```

### Forbidden Transitions

```
published → any state
failed → any state       (except manual retry → scheduled)
queued → generated       (no backward movement)
scheduled → queued       (no backward movement)
```

Once a clip reaches `published`, it is immutable. Once a clip reaches `failed` after exhausting retries, it requires manual operator intervention.

### Retry Policy

**Per-clip rendering retry:**

```
max_retries: 2
retry_action: re-render with fallback FFmpeg settings (lower bitrate)
dead_action: skip clip, log failure, continue to next clip
```

**Publishing retry:**

```
max_retries: 3
retry_delay: exponential backoff (60s, 300s, 900s)
dead_action: mark as failed, alert operator, retain all assets for manual upload
```

### Idempotency Behavior

**Pipeline-level idempotency:**

- `video_id` is computed deterministically from file content (SHA-256 of first 10MB + file size)
- If `pipeline_runs` already contains a `completed` entry for this `video_id`, the pipeline skips all processing and reports "already processed"
- If `pipeline_runs` contains a `partial` or `failed` entry, the pipeline resumes from the last completed stage

**Clip-level idempotency:**

- `clip_id` is derived deterministically: `SHA256(video_id + start_ms + end_ms)[:16]`
- If a clip_id already exists in the `clips` table, the clip is skipped entirely
- File writes use atomic rename (write to `.tmp`, rename on success)
- Database writes use `INSERT ... ON CONFLICT DO NOTHING`

**Scene-level idempotency:**

- `scene_id` is derived deterministically: `{video_id}_{start_ms}_{end_ms}`
- If a scene_id already exists in the `scenes` table, analysis is skipped for that scene

**Force reprocessing:** The `--force` flag clears all existing data for the given `video_id` before starting. This is a destructive operation requiring explicit intent.

---

## Module Interface Contract Specification

All modules communicate exclusively through typed DTO (Data Transfer Object) contracts. No module may access another module's internal state, private functions, or intermediate files.

### Contract Structure

Every module defines exactly two boundaries:

1. **Input Contract** — The DTO(s) it receives from upstream modules
2. **Output Contract** — The DTO it produces for downstream modules

DTOs are plain data classes defined in the shared `contracts/` package. They contain:

- Typed fields (primitive types, lists, nested DTOs)
- No methods, no logic, no I/O
- No dependencies on any module's internals

### Contract Registry

| Module         | Input DTO(s)                                          | Output DTO                   |
| -------------- | ----------------------------------------------------- | ---------------------------- |
| Ingestion      | File path (string)                                    | `IngestionResult`            |
| Scene Splitter | `IngestionResult`                                     | `SceneList`                  |
| Transcription  | `IngestionResult`                                     | `Transcript`                 |
| Face Detection | `IngestionResult`, `SceneList`                        | `FaceDetectionResult`        |
| Scoring        | `SceneList`, `Transcript`, `FaceDetectionResult`      | `ScoredSceneList`            |
| Clip Builder   | `ScoredSceneList`                                     | `ClipList`                   |
| Hook Generator | `ClipDefinition`, `Transcript`                        | `HookResult`                 |
| TTS            | `HookResult`                                          | `TTSResult`                  |
| Subtitle       | `Transcript`, `TTSResult`, `ClipDefinition`           | `SubtitleResult`             |
| Compositor     | `ClipDefinition`, `FaceDetectionResult`               | `CompositeStream`            |
| Renderer       | `CompositeStream`, `TTSResult`, `SubtitleResult`      | `RenderedClip`               |
| Thumbnail      | `ClipDefinition`, `FaceDetectionResult`, `HookResult` | `ThumbnailResult`            |
| Metadata       | `HookResult`, `Transcript`, `ClipDefinition`          | `MetadataResult`             |
| Storage        | `RenderedClip`, `ThumbnailResult`, `MetadataResult`   | `StorageRecord`              |
| Scheduler      | `StorageRecord` list                                  | Updated `StorageRecord` list |
| Publisher      | `StorageRecord`                                       | Updated `StorageRecord`      |

### Contract Enforcement Rules

1. **Additive only** — New fields may be added to a DTO. Existing fields may never be removed or renamed.
2. **No cross-imports** — Module A cannot import anything from Module B's internal package. Only the `contracts/` package is shared.
3. **Testability** — Any module can be tested in isolation by constructing its input DTO directly. No upstream module needs to be running.
4. **Serializable** — All DTOs must be JSON-serializable for caching and debugging. Complex types (paths, timestamps) are stored as strings.

---

## Execution Safety Rules

The following constraints are enforced at the system level. The orchestrator and individual modules must respect these limits before producing any output.

### Output Constraints

| Rule                     | Value              | Enforcement                                                                    |
| ------------------------ | ------------------ | ------------------------------------------------------------------------------ |
| `min_clip_duration`      | 30 seconds         | Clip Builder rejects clips below this floor                                    |
| `max_clip_duration`      | 60 seconds         | Clip Builder splits clips exceeding this ceiling                               |
| `max_clips_per_run`      | 20                 | Clip Builder stops after this limit, even if more scenes available             |
| `min_clips_per_run`      | 5                  | Scoring threshold lowers incrementally if fewer than 5 clips would be produced |
| `output_resolution`      | 1080x1920          | Renderer rejects any output not matching this resolution                       |
| `output_codec`           | H.264 High Profile | Renderer enforces codec via FFmpeg flags                                       |
| `output_framerate`       | 30fps              | Renderer re-encodes if source differs                                          |
| `max_file_size_per_clip` | 100MB              | Renderer re-encodes with constrained quality if exceeded                       |
| `thumbnail_resolution`   | 1280x720           | Thumbnail module enforces this size                                            |
| `title_length`           | 40–60 characters   | Metadata module truncates or rejects outside range                             |

### Resource Constraints

| Rule                 | Value              | Enforcement                                            |
| -------------------- | ------------------ | ------------------------------------------------------ |
| `min_disk_space`     | 3× input file size | Pre-flight check before pipeline starts                |
| `max_memory_usage`   | 4GB peak           | Whisper model selection (small/base) constrains memory |
| `min_input_duration` | 30 minutes         | Ingestion rejects shorter videos                       |
| `max_input_duration` | 120 minutes        | Ingestion rejects longer videos                        |

### Failure Thresholds

| Rule                         | Threshold                | Action                                     |
| ---------------------------- | ------------------------ | ------------------------------------------ |
| Clip render failures         | > 50% of clips fail      | Abort pipeline, mark run as `failed`       |
| Face detection failure rate  | > 70% of scenes faceless | Log warning, continue with fallback layout |
| Disk space during processing | < 500MB remaining        | Abort pipeline, clean intermediates        |
| FFmpeg process timeout       | > 300 seconds per clip   | Kill process, retry once, then skip clip   |

### Enforcement

- All safety rules are checked **before** each stage begins processing
- If any hard limit is breached, the pipeline aborts with a structured error log
- Soft limits (face detection failure rate, scoring thresholds) trigger warnings but do not halt the pipeline
- The orchestrator checks all constraints at the start of each pipeline run (pre-flight check)

---

## Logging Requirements

All modules must produce structured logs with the following fields on every processing step.

### Required Log Fields

| Field          | Description                                                  |
| -------------- | ------------------------------------------------------------ |
| `run_id`       | UUID for this pipeline execution                             |
| `video_id`     | Deterministic fingerprint of the source video                |
| `clip_id`      | Clip being processed (empty for video-level stages)          |
| `stage`        | Module name (e.g. `scene_splitter`, `renderer`, `thumbnail`) |
| `status`       | Result: `success`, `failed`, `skipped`                       |
| `error_reason` | Error description (empty on success)                         |
| `duration_ms`  | Processing duration in milliseconds                          |
| `timestamp`    | ISO 8601 timestamp                                           |

### Log Format

```json
{
  "run_id": "run-a1b2c3d4",
  "video_id": "f8e2a9c1b3d7",
  "clip_id": "c4d5e6f7a8b9",
  "stage": "renderer",
  "status": "success",
  "error_reason": "",
  "duration_ms": 14320,
  "timestamp": "2026-03-24T10:15:32.000Z"
}
```

### Stage-Specific Log Extensions

| Stage          | Additional Fields                                       |
| -------------- | ------------------------------------------------------- |
| Ingestion      | `input_path`, `duration_seconds`, `resolution`, `codec` |
| Scene Splitter | `scene_count`, `avg_scene_duration`                     |
| Transcription  | `word_count`, `avg_confidence`                          |
| Face Detection | `avg_face_visibility`, `faceless_scene_count`           |
| Scoring        | `min_score`, `max_score`, `avg_score`                   |
| Clip Builder   | `clips_created`, `clips_rejected`, `avg_clip_duration`  |
| Renderer       | `output_file_size`, `output_duration`, `encoding_fps`   |
| Thumbnail      | `face_visible`, `text_overlay`, `frame_index`           |
| Publisher      | `youtube_id`, `upload_duration_ms`, `retry_count`       |

### Log Levels

| Level      | Usage                                                                                 |
| ---------- | ------------------------------------------------------------------------------------- |
| `INFO`     | Stage started, stage completed, clip stored                                           |
| `WARN`     | Face detection fallback triggered, scoring threshold lowered, template rotation reset |
| `ERROR`    | Stage failure, FFmpeg crash, file I/O error                                           |
| `CRITICAL` | Pipeline abort, disk space exhaustion, database corruption                            |

### Log Destinations

- **stdout** — All logs for real-time monitoring
- **`output/{video_id}/pipeline.log`** — Per-run persistent log file
- **`pipeline_runs` table** — Summary status and error log stored in SQLite

---

## Phase 0 — Core Infrastructure

### Objective

Initialize the base runtime environment required for the pipeline. This phase establishes the configuration layer, logging infrastructure, SQLite database, filesystem structure, and dependency verification.

No video processing logic exists in this phase.

### Phase Invariants

1. No video processing or domain modules exist in this phase
2. All configuration is loaded from a single `config.yaml` file with environment variable overrides
3. No secrets are stored in source code — YouTube OAuth credentials are loaded from a separate credentials file outside the repository
4. SQLite database is initialized with all four tables on first run
5. All filesystem paths are validated before any module executes
6. External dependencies (FFmpeg, FFprobe) are verified as available at startup

### Deliverables

```
shorts_factory/
├── core/
│   ├── __init__.py
│   ├── config.py              # YAML config loader + environment override + validation
│   ├── logging.py             # Structured JSON logging to stdout + file
│   ├── orchestrator.py        # Pipeline stage sequencer (skeleton only in Phase 0)
│   └── dependencies.py        # External dependency checker (FFmpeg, FFprobe, Python version)
├── db/
│   ├── __init__.py
│   ├── connection.py          # SQLite connection manager (WAL mode, single file)
│   └── migrations/
│       ├── 001_create_videos_table.sql
│       ├── 002_create_scenes_table.sql
│       ├── 003_create_clips_table.sql
│       └── 004_create_pipeline_runs_table.sql
├── contracts/
│   └── __init__.py            # Shared DTO package (empty, prepared for Phase 1+)
config.yaml                    # Default configuration file
run_pipeline.py                # Entry point (skeleton: parse args, load config, exit)
```

### Configuration Structure

```yaml
pipeline:
  min_clip_duration: 30
  max_clip_duration: 60
  max_clips_per_run: 20
  min_clips_per_run: 5
  output_resolution: [1080, 1920]
  output_framerate: 30
  output_codec: h264

scoring:
  keyword_weight: 3
  audio_energy_weight: 2
  face_presence_weight: 2
  scene_activity_weight: 1
  sentence_density_weight: 1
  min_composite_score: 0.2

scene_splitter:
  min_scene_duration: 3
  max_scene_duration: 20

transcription:
  model: small
  language: en

face_detection:
  sample_fps: 2
  confidence_threshold: 0.7
  ema_alpha: 0.3

tts:
  engine: edge-tts
  voice: en-US-AriaNeural
  volume_normalization_lufs: -14

renderer:
  crf: 20
  audio_mix_gameplay: 0.7
  audio_mix_narration: 0.3

thumbnail:
  saturation_boost: 1.15
  contrast_boost: 1.10
  max_text_words: 3
  font_size_min: 72

scheduler:
  posts_per_day: 1
  publish_time_utc: "10:00"

publisher:
  max_retries: 3
  retry_delays: [60, 300, 900]
  initial_visibility: unlisted
  public_delay_minutes: 30

paths:
  output_dir: ./output
  database: ./output/shorts.db

channel:
  name: ""
  hashtags: []
  static_tags: []
```

### Database Migrations

#### 001_create_videos_table.sql

```sql
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    resolution_width INTEGER NOT NULL,
    resolution_height INTEGER NOT NULL,
    codec_video TEXT,
    codec_audio TEXT,
    file_size_bytes INTEGER NOT NULL,
    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'ingested'
);
```

#### 002_create_scenes_table.sql

```sql
CREATE TABLE IF NOT EXISTS scenes (
    scene_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    duration REAL NOT NULL,
    composite_score REAL,
    keyword_score REAL,
    audio_energy_score REAL,
    scene_activity_score REAL,
    face_presence_score REAL,
    sentence_density_score REAL,
    face_visible_ratio REAL,
    transcript_text TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scenes_video ON scenes(video_id);
CREATE INDEX IF NOT EXISTS idx_scenes_score ON scenes(composite_score);
```

#### 003_create_clips_table.sql

```sql
CREATE TABLE IF NOT EXISTS clips (
    clip_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    duration REAL NOT NULL,
    composite_score REAL,
    video_path TEXT,
    thumbnail_path TEXT,
    title TEXT,
    description TEXT,
    tags TEXT,
    status TEXT NOT NULL DEFAULT 'generated',
    scheduled_at TIMESTAMP,
    published_at TIMESTAMP,
    youtube_id TEXT,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(video_id);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
CREATE INDEX IF NOT EXISTS idx_clips_scheduled ON clips(scheduled_at)
    WHERE status = 'scheduled';
```

#### 004_create_pipeline_runs_table.sql

```sql
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    last_completed_stage TEXT,
    clips_generated INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'started',
    error_log TEXT,
    config_snapshot TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_video ON pipeline_runs(video_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON pipeline_runs(status);
```

### Failure Handling

- If `config.yaml` is missing or invalid, the process exits with code 1 and a human-readable error listing all invalid/missing fields
- If SQLite database cannot be created (permissions, disk full), the process exits with code 1
- If FFmpeg or FFprobe are not found in PATH, the process exits with code 1 and prints installation instructions
- If Python version < 3.10, the process exits with code 1
- Migration failures are fatal — the process does not start with an inconsistent database

### Idempotency Rules

- Running the setup multiple times produces identical state — tables use `CREATE TABLE IF NOT EXISTS`
- Configuration reload does not reset database content
- Filesystem directory creation is idempotent (`os.makedirs(exist_ok=True)`)

### Exit Criteria

- [ ] Configuration loads from `config.yaml` with all fields validated
- [ ] Environment variable overrides work for all configuration keys
- [ ] Structured JSON logging writes to stdout
- [ ] Per-run log file writes to `output/{video_id}/pipeline.log`
- [ ] SQLite database created with all four tables and indexes
- [ ] FFmpeg and FFprobe availability verified at startup
- [ ] Python version check passes (≥ 3.10)
- [ ] Repeated startup produces identical state (idempotent)
- [ ] `run_pipeline.py` accepts a video file path argument and validates it exists

### Tasks

- [ ] Create `core/config.py` with YAML loader, validation, and environment override logic
- [ ] Create `core/logging.py` with structured JSON formatter and dual-output (stdout + file)
- [ ] Create `core/dependencies.py` with FFmpeg/FFprobe/Python version checks
- [ ] Write all four migration SQL scripts
- [ ] Create `db/connection.py` with SQLite WAL mode and migration runner
- [ ] Create `config.yaml` with all default values documented
- [ ] Create `run_pipeline.py` skeleton (arg parsing, config load, dependency check, exit)
- [ ] Create `core/orchestrator.py` skeleton (stage list, no implementation)
- [ ] Initialize `contracts/` package with `__init__.py`
- [ ] Write unit tests for config validation (valid config, missing fields, invalid types)
- [ ] Write unit tests for migration idempotency
- [ ] Write integration test: startup → config load → DB init → dependency check → clean exit

---

## Phase 1 — Core Pipeline Skeleton

### Objective

Implement the first two pipeline stages — **ingestion** and **scene splitting** — producing the raw scene material that all downstream modules consume. This phase also defines the core DTO contracts for `IngestionResult` and `SceneList`.

After this phase, the pipeline can accept a video file and output a validated list of scene segments with deterministic IDs.

### Phase Invariants

1. Ingestion must never modify the source video file
2. `video_id` is always computed as `SHA256(first_10MB + file_size)` — this is the system-wide unique identifier
3. Scene boundaries must be identical on repeated runs with the same input and configuration
4. No scene may be shorter than `min_scene_duration` (3s) or longer than `max_scene_duration` (20s)
5. The orchestrator must check for existing `video_id` in the database and skip ingestion if already present
6. Scene splitter must handle edge cases: static video (one scene), rapid flickering (merge micro-scenes)

### Deliverables

```
shorts_factory/
├── contracts/
│   ├── ingestion.py           # IngestionResult DTO
│   └── scene.py               # SceneList, SceneSegment DTOs
├── modules/
│   ├── ingestion/
│   │   ├── __init__.py
│   │   └── ingest.py          # Video validation, fingerprinting, metadata extraction
│   └── scene_splitter/
│       ├── __init__.py
│       └── split.py           # PySceneDetect integration, scene boundary detection
├── core/
│   └── orchestrator.py        # Updated: wires ingestion → scene_splitter
```

### Execution Flow

**Ingestion:**

1. Receive file path from CLI argument
2. Validate file exists and is readable
3. Probe with FFprobe: extract duration, resolution, codec, audio stream presence
4. Reject if: file not found, unsupported format, no audio stream, duration out of range (30–120 min)
5. Compute `video_id`: read first 10MB of file, concatenate with file size as string, SHA-256 hash, take first 16 hex characters
6. Check `videos` table — if `video_id` exists, return cached `IngestionResult` without reprocessing
7. Insert row into `videos` table
8. Return `IngestionResult` DTO

**Scene Splitting:**

1. Receive `IngestionResult` from ingestion
2. Check `scenes` table — if scenes exist for this `video_id`, return cached `SceneList`
3. Run PySceneDetect on the video with adaptive threshold (deterministic, fixed seed)
4. Post-process scene list:
   - Merge any scene < 3 seconds with its predecessor
   - Force-split any scene > 20 seconds at the midpoint
5. Assign `scene_id` to each scene: `{video_id}_{start_ms}_{end_ms}`
6. Insert all scenes into `scenes` table
7. Return `SceneList` DTO

### Failure Handling

- **FFprobe failure** (corrupt file) → abort pipeline with `CRITICAL` log, set run status to `failed`
- **PySceneDetect crash** → retry once with default threshold, if still fails → abort pipeline
- **No scenes detected** (static video) → create single scene spanning entire video, log `WARN`
- **Excessive micro-scenes** (> 500 scenes for < 60 min video) → log `WARN`, merge pass runs until under 300

### Idempotency Rules

- If `video_id` already exists in `videos` table → skip ingestion, return cached result
- If scenes already exist for `video_id` in `scenes` table → skip splitting, return cached result
- `INSERT ... ON CONFLICT DO NOTHING` on all database writes
- No file mutations — source video is read-only

### Exit Criteria

- [ ] `IngestionResult` DTO defined with all fields from architecture spec
- [ ] `SceneList` and `SceneSegment` DTOs defined with all fields
- [ ] Ingestion validates MP4/MKV/AVI formats, rejects unsupported
- [ ] Ingestion rejects videos without audio stream
- [ ] Ingestion rejects videos outside 30–120 minute range
- [ ] `video_id` is deterministic (same file → same ID on every run)
- [ ] Scene splitter produces identical boundaries on repeated runs
- [ ] No scene shorter than 3 seconds in output
- [ ] No scene longer than 20 seconds in output
- [ ] Scenes inserted into SQLite with deterministic `scene_id`
- [ ] Rerun skips already-processed video and scenes
- [ ] Integration test: real MP4 → ingestion → scene split → valid SceneList

### Tasks

- [ ] Define `IngestionResult` DTO in `contracts/ingestion.py`
- [ ] Define `SceneList` and `SceneSegment` DTOs in `contracts/scene.py`
- [ ] Implement `modules/ingestion/ingest.py` with FFprobe validation and SHA-256 fingerprinting
- [ ] Implement `modules/scene_splitter/split.py` with PySceneDetect integration
- [ ] Implement scene post-processing (merge micro-scenes, split long scenes)
- [ ] Update `core/orchestrator.py` to wire ingestion → scene_splitter
- [ ] Write unit tests for ingestion (valid file, missing file, unsupported format, no audio, out of range)
- [ ] Write unit tests for scene splitter (normal video, static video, flickering video)
- [ ] Write unit test for `video_id` determinism
- [ ] Write integration test: `run_pipeline.py test_video.mp4` → SceneList output

---

## Phase 2 — Transcription & Signal Extraction

### Objective

Implement **transcription**, **audio energy analysis**, and **face detection** — the three signal extraction modules whose outputs feed into the scoring engine. After this phase, every scene has a word-level transcript, an audio energy measurement, and a face visibility ratio.

### Phase Invariants

1. Transcription must produce word-level timestamps — segment-level is insufficient for subtitle alignment
2. Transcription must use faster-whisper with a fixed model (`small` or `base`) and deterministic settings
3. Face detection must sample at 2fps — not every frame
4. Face detection bounding boxes must use normalized coordinates (0–1 range) for resolution independence
5. All three modules depend only on `IngestionResult` and `SceneList` — they can be developed and tested in parallel
6. Empty results are valid — no speech detected, no face detected — these are not errors
7. Audio energy extraction uses FFmpeg loudness analysis, not a separate library

### Deliverables

```
shorts_factory/
├── contracts/
│   ├── transcript.py          # Transcript, TranscriptSegment, Word DTOs
│   └── face.py                # FaceDetectionResult, SceneFaceData, FaceBBox DTOs
├── modules/
│   ├── transcription/
│   │   ├── __init__.py
│   │   └── transcribe.py      # faster-whisper integration, word-level extraction
│   ├── face_detection/
│   │   ├── __init__.py
│   │   └── detect.py          # MediaPipe face detection, 2fps sampling, EMA smoothing
│   └── audio_analysis/
│       ├── __init__.py
│       └── analyze.py         # FFmpeg RMS energy extraction per scene
```

### Execution Flow

**Transcription:**

1. Receive `IngestionResult` (video path)
2. Extract audio from video via FFmpeg → temporary WAV file
3. Load faster-whisper model (small, CPU mode)
4. Transcribe with word-level timestamps enabled
5. Group words into segments aligned with scene boundaries from `SceneList`
6. Delete temporary WAV file
7. Return `Transcript` DTO

**Audio Energy Analysis:**

1. Receive `IngestionResult` and `SceneList`
2. For each scene, extract audio segment using FFmpeg
3. Compute RMS energy using FFmpeg's `astats` filter
4. Normalize across video: `score = (scene_rms - min_rms) / (max_rms - min_rms)`
5. Return per-scene energy scores (attached to scene data in scoring phase)

**Face Detection:**

1. Receive `IngestionResult` and `SceneList`
2. For each scene:
   a. Extract frames at 2fps using FFmpeg
   b. Run MediaPipe Face Detection on each frame
   c. Filter detections below confidence threshold (0.7)
   d. Apply EMA smoothing to bounding boxes (alpha = 0.3)
   e. Compute `face_visible_ratio` (frames with face / total frames)
   f. Select largest bounding box if multiple faces detected
3. Return `FaceDetectionResult` DTO

### Failure Handling

- **faster-whisper model not downloaded** → attempt auto-download, if offline → abort with dependency error
- **No speech detected** → return empty `Transcript` with zero segments (valid state)
- **MediaPipe fails to load** → abort pipeline with `CRITICAL` log and dependency check instructions
- **No face detected in any scene** → return `FaceDetectionResult` with all `face_visible_ratio = 0.0` (valid state, triggers fallback layout in compositor)
- **Audio extraction fails** → abort pipeline (audio is required for transcription)
- **FFmpeg crashes during frame extraction** → retry once per scene, skip scene on second failure

### Idempotency Rules

- Transcript results are cached by `video_id` — if transcription already exists, return cached result
- Face detection results are cached per `scene_id` in the `scenes` table (`face_visible_ratio` column)
- Audio energy results are cached per `scene_id` in the `scenes` table (`audio_energy_score` column)
- Temporary files (extracted audio, extracted frames) are cleaned up after processing

### Exit Criteria

- [ ] `Transcript`, `TranscriptSegment`, `Word` DTOs defined with all fields
- [ ] `FaceDetectionResult`, `SceneFaceData`, `FaceBBox` DTOs defined with all fields
- [ ] Transcription produces word-level timestamps (not just segment-level)
- [ ] Transcription returns empty result for videos with no speech (not an error)
- [ ] Face detection samples at 2fps, not every frame
- [ ] Face detection applies EMA smoothing with configurable alpha
- [ ] Face detection returns normalized bounding boxes (0–1 range)
- [ ] Audio energy extraction returns per-scene normalized RMS values
- [ ] All three modules are independently testable with mock `IngestionResult` and `SceneList`
- [ ] Integration test: real video → transcription → non-empty Transcript with word timestamps
- [ ] Integration test: real video → face detection → FaceDetectionResult with visibility ratios
- [ ] Integration test: real video → audio analysis → per-scene energy scores

### Tasks

- [ ] Define `Transcript`, `TranscriptSegment`, `Word` DTOs in `contracts/transcript.py`
- [ ] Define `FaceDetectionResult`, `SceneFaceData`, `FaceBBox` DTOs in `contracts/face.py`
- [ ] Implement `modules/transcription/transcribe.py` with faster-whisper, word-level timestamps
- [ ] Implement `modules/face_detection/detect.py` with MediaPipe, 2fps sampling, EMA smoothing
- [ ] Implement `modules/audio_analysis/analyze.py` with FFmpeg RMS extraction
- [ ] Update `core/orchestrator.py` to wire scene_splitter → [transcription, face_detection, audio_analysis]
- [ ] Write unit tests for transcription (speech present, no speech, confidence scores)
- [ ] Write unit tests for face detection (face visible, no face, multiple faces, EMA smoothing correctness)
- [ ] Write unit tests for audio energy (varying energy, flat energy, normalization range)
- [ ] Write integration test: full signal extraction on a 30-second test video

---

## Phase 3 — Scoring Engine

### Objective

Implement the **deterministic scoring engine** that evaluates every scene across five factors and produces a ranked `ScoredSceneList`. This is the decision layer that determines which scenes become Shorts.

### Phase Invariants

1. Scoring formula is purely rule-based — no ML models, no randomness, no external API calls
2. All five factors (keyword, audio_energy, face_presence, scene_activity, sentence_density) must be computed for every scene
3. Missing signals default to 0 — they never cause a crash or skip
4. Composite score is normalized to [0, 1] range via min-max normalization within the current video
5. Score weights must be configurable via `config.yaml` — not hardcoded
6. Identical inputs and identical configuration must produce identical scores on every run

### Deliverables

```
shorts_factory/
├── contracts/
│   └── scoring.py             # ScoredSceneList, ScoredScene DTOs
├── modules/
│   └── scoring/
│       ├── __init__.py
│       ├── score.py           # Main scoring engine: compute all factors, rank
│       ├── keywords.py        # Keyword extraction and scoring
│       └── activity.py        # Scene activity (motion proxy) computation
```

### Execution Flow

1. Receive `SceneList`, `Transcript`, `FaceDetectionResult`, and per-scene audio energy data
2. For each scene, compute five factor scores:

   **Keyword Score:**
   - Extract words from transcript for this scene's time range
   - Count matches against engagement keyword list (configurable)
   - Score = `min(keyword_count / total_word_count, 1.0)` — capped at 1.0

   **Audio Energy Score:**
   - Taken directly from audio analysis output (already normalized 0–1)

   **Face Presence Score:**
   - Taken directly from `face_visible_ratio` in FaceDetectionResult (already 0–1)

   **Scene Activity Score:**
   - Sample frames at 1fps from scene
   - Compute pixel difference between consecutive frames
   - Normalize to 0–1 based on video-wide min/max

   **Sentence Density Score:**
   - Compute words per second for the scene
   - 2–4 words/second = 1.0 (optimal)
   - Outside range: linearly decrease toward 0

3. Compute weighted composite: `(keyword × w1 + audio × w2 + face × w3 + activity × w4 + density × w5) / sum(weights)`
4. Rank all scenes by composite score descending
5. Store all scores in `scenes` table
6. Return `ScoredSceneList` DTO

### Failure Handling

- **Transcript is empty** → keyword_score = 0, sentence_density_score = 0 for all scenes; other factors still computed
- **Face detection empty** → face_presence_score = 0 for all scenes; other factors still computed
- **All scenes score identically** (degenerate case) → fall back to temporal distribution: assign descending scores based on position in video (spread clips evenly)
- **Scene activity computation fails** (frame extraction error) → scene_activity_score = 0 for affected scene, log `WARN`

### Idempotency Rules

- Scores are written to the `scenes` table — if scores already exist for a `scene_id`, they are not overwritten (unless `--force` flag)
- Scoring is a pure function of its inputs — deterministic by construction
- No side effects beyond database writes

### Exit Criteria

- [ ] `ScoredSceneList` and `ScoredScene` DTOs defined with all per-factor and composite scores
- [ ] All five scoring factors implemented and tested independently
- [ ] Weights loaded from `config.yaml`, not hardcoded
- [ ] Missing signals produce 0 score for affected factor, not a crash
- [ ] Composite score normalized to [0, 1] range
- [ ] Ranking is deterministic and stable (consistent ordering for equal scores)
- [ ] Identical inputs produce identical scores on every run
- [ ] Integration test: scored scene list from real video matches expected ranking

### Tasks

- [ ] Define `ScoredSceneList` and `ScoredScene` DTOs in `contracts/scoring.py`
- [ ] Implement keyword scoring with configurable keyword list
- [ ] Implement audio energy score passthrough from analysis results
- [ ] Implement face presence score passthrough from detection results
- [ ] Implement scene activity scoring via frame differencing
- [ ] Implement sentence density scoring with optimal range calculation
- [ ] Implement weighted composite computation with configurable weights
- [ ] Implement min-max normalization across all scenes
- [ ] Implement deterministic ranking with stable sort
- [ ] Write unit tests for each factor independently (known inputs → expected scores)
- [ ] Write unit test for composite computation (verify weighting and normalization)
- [ ] Write unit test for degenerate case (all identical scores → temporal distribution fallback)
- [ ] Write integration test: transcription + face + audio → scoring → ranked scene list

---

## Phase 4 — Clip Builder

### Objective

Implement the **clip builder** that merges adjacent high-scoring scenes into clips of exactly 30–60 seconds. This is the module that transforms scored scenes into publishable clip definitions.

### Phase Invariants

1. No clip may be shorter than 30 seconds — clips below this floor are discarded
2. No clip may be longer than 60 seconds — clips above this ceiling are split at the nearest scene boundary to 45 seconds
3. Only temporally contiguous scenes may be merged — no jump cuts
4. Clip selection prioritizes highest composite scores first
5. Each scene may belong to at most one clip — once consumed, it cannot be reused
6. Clip IDs are deterministic: `SHA256(video_id + start_ms + end_ms)[:16]`
7. Target output: 10–15 clips per 1-hour video, minimum 5

### Deliverables

```
shorts_factory/
├── contracts/
│   └── clip.py                # ClipList, ClipDefinition DTOs
├── modules/
│   └── clip_builder/
│       ├── __init__.py
│       └── build.py           # Scene merging algorithm, duration enforcement, rejection logic
```

### Execution Flow

1. Receive `ScoredSceneList` (all scenes ranked by composite score)
2. Sort scenes by composite score descending
3. Starting from the highest-scored unconsumed scene:
   a. Mark it as the clip nucleus
   b. Expand outward temporally — add adjacent scenes (prefer higher-scored adjacent)
   c. Continue until cumulative duration ≥ 30 seconds
   d. If duration exceeds 60 seconds, remove lowest-scored edge scene
   e. Mark all scenes in this clip as consumed
4. Compute `clip_id`: `SHA256(video_id + start_ms_of_first_scene + end_ms_of_last_scene)[:16]`
5. Compute `average_score`: mean of composite scores of constituent scenes
6. Apply rejection criteria:
   - Duration < 30s → discard
   - Average score < `min_composite_score` from config → discard
   - Face visibility ratio < 0.1 AND all fallback layouts invalid → discard
   - No transcript AND no audio energy peaks → discard
   - > 50% temporal overlap with already-selected higher-scoring clip → discard
7. Repeat step 3 with remaining unconsumed scenes
8. If fewer than `min_clips_per_run` clips produced, lower scoring threshold by 0.05 and repeat step 3 once
9. Cap at `max_clips_per_run`
10. Return `ClipList` DTO

### Failure Handling

- **Fewer than 5 clips after all scenes processed** → lower `min_composite_score` threshold by 0.05, reprocess unclaimed scenes. Repeat up to 3 times. If still < 5 → accept what exists, log `WARN`
- **No valid clips at all** → abort pipeline for this video with status `partial`, log `ERROR`
- **All clips exceed 60 seconds** → force-split at scene boundary nearest to 45s, creating two shorter clips from one long one

### Idempotency Rules

- If `clips` table already contains clips for this `video_id`, return cached `ClipList`
- Clip IDs are deterministic — rerun produces identical IDs
- `INSERT ... ON CONFLICT DO NOTHING` on database writes

### Exit Criteria

- [ ] `ClipList` and `ClipDefinition` DTOs defined with all fields
- [ ] No clip shorter than 30 seconds in output
- [ ] No clip longer than 60 seconds in output
- [ ] All scenes within a clip are temporally contiguous
- [ ] Highest-scored scenes are selected first
- [ ] Each scene belongs to at most one clip
- [ ] Clip IDs are deterministic
- [ ] Rejection criteria correctly discard low-quality clips
- [ ] Threshold lowering produces additional clips when initial pool is too small
- [ ] Integration test: 1-hour video → 10–15 clips, all within [30, 60] seconds

### Tasks

- [ ] Define `ClipList` and `ClipDefinition` DTOs in `contracts/clip.py`
- [ ] Implement greedy scene merging algorithm (nucleus expansion)
- [ ] Implement duration enforcement (floor 30s, ceiling 60s)
- [ ] Implement contiguity check (no gaps between merged scenes)
- [ ] Implement rejection criteria (5 rejection rules)
- [ ] Implement threshold lowering fallback for insufficient clips
- [ ] Implement deterministic `clip_id` computation
- [ ] Update `core/orchestrator.py` to wire scoring → clip_builder
- [ ] Write unit tests for merging (normal case, edge cases, all long scenes, all short scenes)
- [ ] Write unit test for rejection (each criterion independently)
- [ ] Write unit test for threshold lowering behavior
- [ ] Write integration test: scored scene list → clip builder → valid ClipList

---

## Phase 5 — Composition Engine

### Objective

Implement **face-aware video composition** — the module responsible for combining the face camera region and gameplay region into a single 9:16 vertical frame. This includes the compositor's fallback logic for missing face data.

### Phase Invariants

1. Output resolution is always 1080x1920 — no exceptions
2. Layout is always 65% gameplay (top) / 35% face (bottom) when face is detected
3. Face region uses 1.2× zoom, centered on the detected bounding box
4. If face visibility ratio < 0.3 for a clip, fallback to full-gameplay layout with zoom
5. Bounding box smoothing (EMA) is applied to prevent jitter — never use raw per-frame bounding boxes
6. No letterboxing — both regions must fill their allocated space
7. Compositor does NOT add audio — it produces a silent composite video stream

### Deliverables

```
shorts_factory/
├── contracts/
│   └── composite.py           # CompositeStream DTO
├── modules/
│   └── compositor/
│       ├── __init__.py
│       ├── compose.py         # Main composition logic: face + gameplay → 9:16
│       ├── face_crop.py       # Face region extraction with bbox + zoom
│       ├── gameplay_crop.py   # Gameplay region center-crop to 9:16
│       └── fallback.py        # Full-gameplay fallback layout (Ken Burns effect)
```

### Execution Flow

1. Receive `ClipDefinition` and `FaceDetectionResult` for the clip's scene range
2. Determine layout mode:
   - If average `face_visible_ratio` across clip's scenes ≥ 0.3 → **split layout**
   - If < 0.3 → **fallback layout**
3. **Split Layout:**
   a. For each frame (at source fps):
   - Extract gameplay region: center-crop source to 9:16, scale to 1080×1248
   - Extract face region: crop around smoothed bounding box, apply 1.2× zoom, scale to 1080×672
   - Stack gameplay (top) + face (bottom) on 1080×1920 canvas
     b. Encode as intermediate video (no audio) using FFmpeg
4. **Fallback Layout:**
   a. For each frame: center-crop gameplay to 9:16, apply slight zoom + slow pan (Ken Burns)
   b. Encode as intermediate video (no audio) using FFmpeg
5. Write intermediate composite to `output/{video_id}/clips/{clip_id}/composite.mp4`
6. Return `CompositeStream` DTO

### Failure Handling

- **Face at edge of frame** → clamp crop to frame boundary, pad with blurred edge pixels
- **Source resolution too low for 1080x1920** → upscale with bicubic interpolation, log `WARN`
- **FFmpeg composition crash** → retry once with simpler filters (no zoom, no smooth), if still fails → skip clip
- **Bounding box interpolation produces negative coordinates** → clamp to 0

### Idempotency Rules

- If `composite.mp4` already exists at the expected path → skip composition, return cached `CompositeStream`
- Intermediate files use `.tmp` suffix during creation, renamed atomically on completion

### Exit Criteria

- [ ] `CompositeStream` DTO defined with all fields
- [ ] Split layout produces correct 65/35 vertical split at 1080x1920
- [ ] Face region correctly zoomed at 1.2× and centered on bounding box
- [ ] Gameplay region correctly center-cropped to fill top portion
- [ ] No letterboxing or black bars in output
- [ ] Fallback layout activated when face visibility < 0.3
- [ ] EMA-smoothed bounding boxes produce stable face tracking
- [ ] Intermediate file uses atomic rename pattern (`.tmp` → final)
- [ ] Integration test: clip with face → split layout composite video
- [ ] Integration test: clip without face → fallback layout composite video

### Tasks

- [ ] Define `CompositeStream` DTO in `contracts/composite.py`
- [ ] Implement `gameplay_crop.py` — center-crop to 9:16 and scale to 1080×1248
- [ ] Implement `face_crop.py` — bbox-based crop with 1.2× zoom, EMA-smoothed coordinates, scale to 1080×672
- [ ] Implement `compose.py` — stack gameplay + face on 1080×1920 canvas via FFmpeg filter chain
- [ ] Implement `fallback.py` — full-gameplay layout with Ken Burns effect
- [ ] Implement layout mode decision logic (face_visible_ratio threshold check)
- [ ] Implement edge-case handling (face at frame edge, low resolution)
- [ ] Update `core/orchestrator.py` to wire clip_builder → compositor (per-clip)
- [ ] Write unit tests for gameplay crop (various source resolutions)
- [ ] Write unit tests for face crop (center, edge, missing bbox)
- [ ] Write unit tests for layout mode decision
- [ ] Write integration test: real clip → composite video at 1080x1920

---

## Phase 6 — Rendering Pipeline

### Objective

Implement the **hook generator**, **TTS synthesis**, **subtitle generation**, and **final renderer** — the four modules that transform a silent composite video into a complete, publish-ready Short with narration, subtitles, and mixed audio.

### Phase Invariants

1. Hook text is purely template-based — no LLM, no randomness
2. No template may be reused within a single batch of clips
3. TTS uses Edge TTS with a fixed voice setting — deterministic for the same text input
4. Subtitles use ASS format with word-level timing for karaoke-style highlighting
5. Subtitles must not overlap the face region (bottom 35%) — positioned in the gameplay area
6. Audio mix ratio is fixed: 70% gameplay / 30% narration, with gameplay ducking during narration
7. Final output must be H.264 High Profile, CRF 18–23, 1080x1920, 30fps, AAC 128kbps
8. Any rendered clip outside 30–60 seconds is rejected — not stored, not queued

### Deliverables

```
shorts_factory/
├── contracts/
│   ├── hook.py                # HookResult DTO
│   ├── tts.py                 # TTSResult DTO
│   ├── subtitle.py            # SubtitleResult DTO
│   └── render.py              # RenderedClip DTO
├── modules/
│   ├── hook_generator/
│   │   ├── __init__.py
│   │   ├── generate.py        # Template-based hook + story generation
│   │   └── templates.py       # Template pool (30+ patterns)
│   ├── tts/
│   │   ├── __init__.py
│   │   └── synthesize.py      # Edge TTS integration, volume normalization
│   ├── subtitle/
│   │   ├── __init__.py
│   │   └── generate.py        # Word-level ASS subtitle generation
│   └── renderer/
│       ├── __init__.py
│       └── render.py          # Final MP4 assembly (composite + TTS + audio + subtitles)
```

### Execution Flow

**Hook Generator:**

1. Receive `ClipDefinition` and `Transcript` for the clip's time range
2. Extract keywords from transcript segment
3. Select template from pool based on deterministic rotation (hash of `clip_id` modulo pool size)
4. Fill template parameters: `{subject}`, `{object}`, `{adjective}`, `{emoji}` from extracted keywords and config
5. If no transcript text → select generic template from fallback pool
6. Validate: hook ≤ 15 words, story ≤ 2 sentences
7. Return `HookResult` DTO

**TTS Synthesis:**

1. Receive `HookResult` (hook_text + story_text)
2. Synthesize using Edge TTS with configured voice
3. Normalize volume to -14 LUFS using FFmpeg
4. If Edge TTS unavailable → fallback to pyttsx3
5. If generated audio > clip duration → truncate story, keep hook intact
6. Write to `output/{video_id}/clips/{clip_id}/narration.wav`
7. Return `TTSResult` DTO

**Subtitle Generator:**

1. Receive `Transcript` (word-level timestamps for clip range) and `ClipDefinition`
2. Generate ASS subtitle file with:
   - Word-level timing for karaoke highlighting
   - Large bold font (48pt+), white text with black outline
   - Center-bottom positioning within the top 65% (gameplay area)
   - Maximum 2 simultaneous lines
3. Write to `output/{video_id}/clips/{clip_id}/subtitles.ass`
4. Return `SubtitleResult` DTO

**Final Renderer:**

1. Receive `CompositeStream`, `TTSResult`, original audio (extracted from source), `SubtitleResult`
2. Build FFmpeg command:
   - Video: composite stream (already 1080x1920)
   - Audio track 1: gameplay audio at 70% volume with ducking during narration
   - Audio track 2: TTS narration at 30% volume
   - Subtitles: burn-in from ASS file
   - Codec: H.264 High Profile, CRF from config (default 20)
   - Audio: AAC, 128kbps, stereo
   - Frame rate: 30fps
3. Execute FFmpeg
4. Validate output:
   - Duration within [30, 60] seconds → reject if not
   - Resolution = 1080x1920 → reject if not
   - File size < 100MB → re-encode with higher CRF if exceeded
5. Write to `output/{video_id}/clips/{clip_id}/final.mp4`
6. Delete intermediate `composite.mp4` (unless keep-intermediates flag)
7. Return `RenderedClip` DTO

### Failure Handling

- **Edge TTS offline** → fallback to pyttsx3; if both fail → render without narration, log `ERROR`
- **Hook template pool exhausted** → reset rotation index, reuse templates with log `WARN`
- **FFmpeg render crash** → retry once with lower CRF (24); if still fails → skip clip, log `ERROR`
- **Output duration mismatch > 1s** → reject clip entirely, do not store
- **File size > 100MB** → re-encode with CRF 24; if still > 100MB → re-encode with CRF 28
- **ASS subtitle generation fails** → render without subtitles, log `ERROR`
- **Font not found for subtitles** → fallback to system default sans-serif bold font

### Idempotency Rules

- If `final.mp4` exists at expected path → skip rendering, return cached `RenderedClip`
- TTS audio cached by text hash — same hook text never re-synthesized
- Intermediate files (composite.mp4) cleaned after successful render
- All writes use atomic rename pattern

### Exit Criteria

- [ ] `HookResult`, `TTSResult`, `SubtitleResult`, `RenderedClip` DTOs defined
- [ ] Hook generator uses 30+ templates with deterministic rotation
- [ ] No template reused within a single batch
- [ ] TTS produces normalized audio (-14 LUFS)
- [ ] Subtitle ASS file has word-level timing
- [ ] Subtitles positioned in gameplay area (not over face region)
- [ ] Final MP4 is 1080x1920, H.264, 30fps, AAC
- [ ] Audio mix is 70% gameplay / 30% narration
- [ ] Rendered duration is within [30, 60] seconds
- [ ] File size < 100MB
- [ ] Clips outside duration range are rejected
- [ ] Integration test: full render pipeline produces valid MP4 from composite + TTS + subtitles

### Tasks

- [ ] Define `HookResult` DTO in `contracts/hook.py`
- [ ] Define `TTSResult` DTO in `contracts/tts.py`
- [ ] Define `SubtitleResult` DTO in `contracts/subtitle.py`
- [ ] Define `RenderedClip` DTO in `contracts/render.py`
- [ ] Implement `modules/hook_generator/templates.py` with 30+ parameterized patterns
- [ ] Implement `modules/hook_generator/generate.py` with keyword extraction and template filling
- [ ] Implement `modules/tts/synthesize.py` with Edge TTS and pyttsx3 fallback
- [ ] Implement `modules/subtitle/generate.py` with word-level ASS generation
- [ ] Implement `modules/renderer/render.py` with FFmpeg composition and validation
- [ ] Implement audio mixing (70/30 with ducking)
- [ ] Implement intermediate file cleanup
- [ ] Update `core/orchestrator.py` to wire per-clip: hook → TTS → subtitle → render
- [ ] Write unit tests for hook generation (normal, empty transcript, template rotation)
- [ ] Write unit tests for TTS (synthesis, normalization, fallback)
- [ ] Write unit tests for subtitle timing (word alignment, safe area)
- [ ] Write unit tests for renderer (validation, rejection, re-encode logic)
- [ ] Write integration test: composite video → full render → valid final.mp4

---

## Phase 7 — Metadata & Thumbnail Generation

### Objective

Implement the **thumbnail generator** and **metadata generator** — the two modules that produce the visual and textual assets required for YouTube discoverability.

### Phase Invariants

1. Thumbnail must be 1280x720 JPEG
2. Thumbnail must prioritize frames with visible faces
3. Thumbnail text overlay is maximum 2–3 words
4. Title must be 40–60 characters with 1–2 emojis
5. No two titles in the same batch may be identical
6. Tags include both static (from channel config) and dynamic (from transcript)
7. Metadata is deterministic — same clip always produces same title, description, tags

### Deliverables

```
shorts_factory/
├── contracts/
│   ├── thumbnail.py           # ThumbnailResult DTO
│   └── metadata.py            # MetadataResult DTO
├── modules/
│   ├── thumbnail/
│   │   ├── __init__.py
│   │   ├── generate.py        # Frame selection, crop, text overlay
│   │   └── frame_scorer.py    # Score frames by face, color, motion, clarity
│   └── metadata/
│       ├── __init__.py
│       ├── generate.py        # Title, description, tags generation
│       └── templates.py       # Title and description templates
```

### Execution Flow

**Thumbnail Generation:**

1. Receive `ClipDefinition`, `FaceDetectionResult`, `HookResult`, and source video path
2. Extract candidate frames at 1fps from the clip
3. Score each frame:
   - `face_score` = 3 × (1 if face detected with confidence > 0.8, else 0)
   - `color_score` = 2 × normalized color variance
   - `motion_score` = 1 × inter-frame difference
   - `clarity_score` = 1 × Laplacian variance (reject blurry)
4. Select `best_frame = argmax(total_score)` — tiebreak by preferring first 30% of clip
5. Compose thumbnail layout:
   - Primary: face left (40%), gameplay right (60%), text overlay
   - Fallback: zoomed gameplay center, text at top
6. Add text overlay: 2–3 words from hook text, bold sans-serif, white with black stroke
7. Post-process: saturation boost +15%, contrast boost +10%
8. Write to `output/{video_id}/clips/{clip_id}/thumbnail.jpg` (JPEG quality 95)
9. Return `ThumbnailResult` DTO

**Metadata Generation:**

1. Receive `HookResult`, `Transcript` for clip, `ClipDefinition`
2. Generate title:
   - Select template based on hash of `clip_id` modulo template pool size
   - Fill with keywords from transcript and hook text
   - Enforce 40–60 characters
   - Add 1–2 emojis based on emotional tone mapping
   - Verify uniqueness within batch — if duplicate, append clip index
3. Generate description:
   - Line 1: hook sentence
   - Lines 3–5: hashtags (static from config + dynamic from transcript)
   - Line 6: channel branding from config
4. Generate tags:
   - Static tags from channel config
   - Dynamic tags: top 5 nouns/phrases from clip transcript
   - Deduplicate, filter against allowed word list
   - Total: 10–15 tags
5. Write to `output/{video_id}/clips/{clip_id}/metadata.json`
6. Return `MetadataResult` DTO

### Failure Handling

- **No frame with face** → use highest motion/color frame with gameplay only
- **Font not found** → fallback to system default bold sans-serif
- **Frame extraction fails** → use frame at clip midpoint
- **Duplicate title** → append clip index suffix (e.g. " #2")
- **Title over 60 characters** → truncate at last word boundary, re-add emoji
- **No keywords extractable** → use generic template from fallback pool

### Idempotency Rules

- If `thumbnail.jpg` exists at expected path → skip, return cached `ThumbnailResult`
- If `metadata.json` exists at expected path → skip, return cached `MetadataResult`
- Thumbnail and metadata generation are independent — can run in parallel

### Exit Criteria

- [ ] `ThumbnailResult` and `MetadataResult` DTOs defined
- [ ] Thumbnail is 1280x720 JPEG with quality 95
- [ ] Thumbnail prioritizes face-containing frames
- [ ] Text overlay is max 2–3 words, bold, high contrast
- [ ] Titles are 40–60 characters with 1–2 emojis
- [ ] No duplicate titles within a batch
- [ ] Tags combine static + dynamic, 10–15 total
- [ ] Description follows template with hashtags
- [ ] Metadata is deterministic (same clip → same output)
- [ ] Integration test: rendered clip → thumbnail + metadata generation → valid outputs

### Tasks

- [ ] Define `ThumbnailResult` DTO in `contracts/thumbnail.py`
- [ ] Define `MetadataResult` DTO in `contracts/metadata.py`
- [ ] Implement `modules/thumbnail/frame_scorer.py` with multi-factor frame scoring
- [ ] Implement `modules/thumbnail/generate.py` with layout, text overlay, post-processing
- [ ] Implement `modules/metadata/templates.py` with title and description templates
- [ ] Implement `modules/metadata/generate.py` with title, description, tags logic
- [ ] Update `core/orchestrator.py` to wire per-clip: [thumbnail, metadata] (parallel, independent)
- [ ] Write unit tests for frame scoring (face present, no face, blurry frame)
- [ ] Write unit tests for text overlay (word count, positioning, font fallback)
- [ ] Write unit tests for title generation (normal, duplicate, truncation, emoji)
- [ ] Write unit tests for tag generation (static + dynamic, deduplication)
- [ ] Write integration test: clip → thumbnail.jpg (correct resolution) + metadata.json (valid schema)

---

## Phase 8 — Storage & Scheduling

### Objective

Implement the **storage module** and **scheduler** — the persistence layer that saves all pipeline outputs, manages clip lifecycle state, and assigns publish dates.

### Phase Invariants

1. All writes are atomic — clip is either fully stored (all files + DB record) or not at all
2. `INSERT ... ON CONFLICT DO NOTHING` semantics for all clip records — idempotent by design
3. File paths stored as relative paths (portable across machines)
4. Scheduler orders clips by composite score descending — best clips publish first
5. One clip per day, configurable publish time
6. Pipeline run status is tracked for resumability

### Deliverables

```
shorts_factory/
├── contracts/
│   └── storage.py             # StorageRecord DTO
├── modules/
│   ├── storage/
│   │   ├── __init__.py
│   │   └── store.py           # File persistence, DB writes, lifecycle management
│   └── scheduler/
│       ├── __init__.py
│       └── schedule.py        # Publish date assignment, queue management
```

### Execution Flow

**Storage:**

1. Receive `RenderedClip`, `ThumbnailResult`, `MetadataResult` for a single clip
2. Verify all files exist at their expected paths:
   - `final.mp4` — rendered video
   - `thumbnail.jpg` — thumbnail image
   - `metadata.json` — title, description, tags
3. Compute checksums for integrity verification
4. Write clip record to `clips` table:
   - `status = 'generated'`
   - All file paths as relative paths
   - All metadata fields populated
5. On success → update status to `queued`
6. Return `StorageRecord` DTO

**Scheduler:**

1. Fetch all clips with `status = queued`, ordered by `composite_score DESC`
2. Find last scheduled date in database (or tomorrow if empty)
3. For each queued clip:
   - Assign next available publish slot (one per day, configurable time)
   - Skip dates that already have a `scheduled` or `published` clip
   - Update `status = 'scheduled'`, set `scheduled_at`
4. Return updated `StorageRecord` list

### Failure Handling

- **File missing at expected path** → mark clip as `failed`, log `ERROR`, continue to next clip
- **Disk full during write** → abort pipeline with `CRITICAL`, partial clips cleaned up on next run
- **Database locked** → retry with backoff (100ms, 500ms, 1000ms), max 3 retries
- **No clips in queue** → scheduler is a no-op (not an error)
- **Schedule conflict** → skip to next available date

### Idempotency Rules

- `INSERT ... ON CONFLICT DO NOTHING` on clip_id — already-stored clips are never overwritten
- File existence check before write — skip already-written files
- Scheduler checks `scheduled_at` before assigning — already-scheduled clips are not rescheduled
- Orphaned `.tmp` files from interrupted runs are cleaned up at pipeline start

### Exit Criteria

- [ ] `StorageRecord` DTO defined
- [ ] All file paths stored as relative paths
- [ ] Clip lifecycle follows: generated → queued → scheduled → published | failed
- [ ] `INSERT ... ON CONFLICT DO NOTHING` prevents duplicate storage
- [ ] Scheduler assigns one clip per day, ordered by score
- [ ] Scheduler skips dates with existing scheduled/published clips
- [ ] Pipeline run status recorded in `pipeline_runs` table
- [ ] Integration test: render outputs → storage → scheduling → 10+ days of scheduled clips

### Tasks

- [ ] Define `StorageRecord` DTO in `contracts/storage.py`
- [ ] Implement `modules/storage/store.py` with file verification and atomic DB writes
- [ ] Implement `modules/scheduler/schedule.py` with daily slot assignment
- [ ] Implement orphaned file cleanup on pipeline startup
- [ ] Implement pipeline run tracking (start, progress, completion in `pipeline_runs`)
- [ ] Update `core/orchestrator.py` to wire per-clip: [render + thumbnail + metadata] → storage → scheduler
- [ ] Write unit tests for storage (normal, missing files, duplicate clip_id)
- [ ] Write unit tests for scheduler (empty queue, existing schedule, conflict resolution)
- [ ] Write unit test for orphaned file cleanup
- [ ] Write integration test: full clip → storage → scheduling → verified DB state

---

## Phase 9 — Publisher

### Objective

Implement the **publisher module** — the cron-triggered uploader that pushes scheduled clips to YouTube with full metadata and thumbnail, then updates lifecycle state.

### Phase Invariants

1. Publisher runs as a separate cron process — decoupled from the main pipeline
2. Only clips with `status = scheduled` and `scheduled_at <= now` are eligible
3. Clips are initially uploaded as **unlisted**, then switched to **public** after a configurable delay (default 30 minutes)
4. YouTube API v3 with OAuth2 — credentials stored locally, outside the repository
5. Retry strategy: 3 attempts with exponential backoff (60s, 300s, 900s)
6. Failed clips do not block subsequent clips
7. Publishing is idempotent — if a clip already has a `youtube_id`, it is not re-uploaded

### Deliverables

```
shorts_factory/
├── modules/
│   └── publisher/
│       ├── __init__.py
│       ├── publish.py         # YouTube upload orchestration
│       ├── youtube_client.py  # YouTube Data API v3 wrapper (OAuth2)
│       └── visibility.py     # Unlisted → public transition after delay
├── scripts/
│   └── publish_cron.py        # Standalone cron entry point
```

### Execution Flow

1. Load configuration and database connection
2. Query: `SELECT * FROM clips WHERE status = 'scheduled' AND scheduled_at <= now() ORDER BY scheduled_at ASC LIMIT 1`
3. If no clip eligible → exit (no-op)
4. For the eligible clip:
   a. Read all assets from filesystem (video, thumbnail, metadata)
   b. Authenticate with YouTube API (OAuth2 refresh token)
   c. Upload video with:
   - Title from `metadata.json`
   - Description from `metadata.json`
   - Tags from `metadata.json`
   - Category: Gaming (or configured category)
   - Privacy: unlisted
     d. After successful upload → set thumbnail via YouTube API
     e. Update DB: `status = 'published'`, `youtube_id = {id}`, `published_at = now()`
5. After configurable delay (30 min) → update video privacy to public

### Failure Handling

- **OAuth2 token expired** → attempt refresh; if refresh fails → log `ERROR`, retry on next cron cycle
- **Upload timeout** → retry up to 3 times with backoff [60s, 300s, 900s]
- **YouTube quota exceeded** → pause publishing for 24 hours, log `WARN`
- **Video rejected by YouTube** → mark as `failed`, store rejection reason in `error_message`
- **Thumbnail upload fails** (video uploaded OK) → log `WARN`, do not mark video as failed — it still has an auto-generated thumbnail
- **4th failure** → mark as `failed`, alert operator, retain all assets

### Idempotency Rules

- If clip already has `youtube_id` → skip upload, consider published
- Cron runs are stateless — each invocation checks current DB state, acts accordingly
- Upload is tracked by `clip_id` → same clip is never uploaded twice (unless `youtube_id` is cleared manually)

### Exit Criteria

- [ ] Publisher queries only `scheduled` clips with `scheduled_at <= now`
- [ ] Video uploaded as unlisted with correct title, description, tags
- [ ] Thumbnail uploaded separately after video confirmation
- [ ] Privacy transitions to public after configurable delay
- [ ] Retry strategy: 3 attempts, exponential backoff
- [ ] Failed clips logged with reason, do not block queue
- [ ] Cron script is standalone (does not import pipeline modules)
- [ ] Integration test: mock YouTube API → publish flow → status updated to published

### Tasks

- [ ] Implement `modules/publisher/youtube_client.py` with OAuth2 authentication and upload methods
- [ ] Implement `modules/publisher/publish.py` with upload orchestration, retry, and status tracking
- [ ] Implement `modules/publisher/visibility.py` with delayed unlisted → public transition
- [ ] Create `scripts/publish_cron.py` as standalone entry point
- [ ] Implement cron scheduling documentation (crontab example)
- [ ] Write unit tests for YouTube client (mock API responses: success, failure, quota)
- [ ] Write unit tests for retry logic (1st, 2nd, 3rd failure, dead letter)
- [ ] Write unit tests for visibility transition timing
- [ ] Write integration test with mocked YouTube API: scheduled clip → published

---

## Phase 10 — Observability & Analytics

### Objective

Implement **structured observability** — pipeline-level analytics, performance tracking, and quality metrics that enable the operator to understand system behavior and improve output quality over time.

### Phase Invariants

1. Observability MUST NOT affect pipeline determinism — it is purely read-side
2. All metrics are computed from existing data in SQLite — no additional external systems
3. Analytics are generated as a summary report after each pipeline run
4. No real-time dashboards or web UIs — CLI output only
5. Quality metrics feed into human-in-the-loop review, not into automated adjustments (no feedback loops in v1)

### Deliverables

```
shorts_factory/
├── modules/
│   └── analytics/
│       ├── __init__.py
│       ├── pipeline_report.py  # Per-run summary: clips, scores, durations, timing
│       ├── quality_metrics.py  # Face visibility stats, score distributions, rejection rates
│       └── publish_report.py   # Publishing status: uploaded, failed, pending
```

### Execution Flow

**Post-Pipeline Report (runs automatically after each pipeline execution):**

1. Query `pipeline_runs` for current run
2. Aggregate:
   - Total clips generated, rejected, stored
   - Average composite score
   - Duration distribution (min, max, mean)
   - Face visibility distribution
   - Per-stage timing from logs
   - Disk space consumed
3. Print structured summary to stdout
4. Write JSON report to `output/{video_id}/report.json`

**Quality Metrics (queryable on demand):**

- Score distribution histogram (bins of 0.1)
- Face visibility across all clips (histogram)
- Template usage frequency (detect overuse)
- Rejection rate by criteria (which rejections fire most)

**Publishing Report (queryable on demand):**

- Clips published vs. scheduled vs. failed
- Average upload success rate
- Queue depth (days of content remaining)

### Failure Handling

- Report generation failure → log `WARN`, do not block pipeline completion
- Missing data (e.g., no clips generated) → report "0 clips" with empty distributions

### Exit Criteria

- [ ] Post-pipeline summary printed with clips generated, scores, durations, timing
- [ ] JSON report written to output directory
- [ ] Quality metrics queryable (score distribution, face visibility, rejection rates)
- [ ] Publishing report shows queue depth and upload status
- [ ] Analytics do not affect pipeline determinism

### Tasks

- [ ] Implement `modules/analytics/pipeline_report.py` with run summary aggregation
- [ ] Implement `modules/analytics/quality_metrics.py` with score and face visibility stats
- [ ] Implement `modules/analytics/publish_report.py` with publishing status tracking
- [ ] Add report generation as final step in `core/orchestrator.py`
- [ ] Write unit tests for report aggregation (various clip counts, edge cases)
- [ ] Write CLI command for on-demand quality and publishing reports

---

## Parallel Development Strategy

### Branch Naming Convention

```
feature/{module_name}          — new module implementation
fix/{module_name}/{issue}      — bug fix in existing module
refactor/{module_name}         — internal restructuring
chore/{description}            — documentation, config, CI
```

**Examples:**

```
feature/scene-splitter
feature/scoring-engine
feature/thumbnail-generator
fix/face-detection/bbox-jitter
refactor/clip-builder
chore/update-config-schema
```

### Module Isolation Rules

1. Each module lives in its own Python package under `modules/`
2. No module may import another module's internal functions or classes
3. The only shared code is the `contracts/` package (DTO definitions)
4. Each module's public API is a single entry function that accepts input DTOs and returns an output DTO
5. Tests for module A must not require module B to be implemented — all inputs are constructable from DTOs directly

### DTO Contract Change Management

- New fields may be added to any DTO at any time (additive changes)
- Existing field names and types may NEVER be changed or removed
- If a field must be renamed, a new field is added and the old field is deprecated (marked optional with default)
- All DTO changes require updating the contract definition file AND notifying downstream module owners
- DTO changes are always merged to `main` BEFORE module changes that depend on them

### Merge Rules

1. All PRs require passing CI (unit tests + lint)
2. DTO contract changes are merged first, in isolation
3. Module implementations are merged independently once their DTO contracts exist on `main`
4. The orchestrator is updated last, wiring newly-merged modules into the pipeline
5. Integration tests run on `main` after module merge — broken integration blocks the next module merge

### Development Parallelism Matrix

```
Phase 0 ──────────────────────────────────────────→

Phase 1 ──────→ [ingestion]  [scene_splitter]
                      │              │
Phase 2 ──────→ [transcription] [face_detection] [audio_analysis]  ← all parallel
                      │              │                │
Phase 3 ──────→ [scoring] ←──── depends on all three ──┘
                      │
Phase 4 ──────→ [clip_builder]
                      │
                ┌─────┴─────────────────────────┐
Phase 5 ──────→ [compositor]                    │
                      │                          │
Phase 6 ──────→ [hook_gen] [tts] [subtitle] [renderer]  ← parallel except renderer
                      │      │       │          │
Phase 7 ──────→ [thumbnail] [metadata]          │  ← parallel with each other
                      │         │                │
Phase 8 ──────→ [storage] [scheduler]           │  ← parallel with each other
                      │         │                │
Phase 9 ──────→ [publisher]                      │
                                                  │
Phase 10 ─────→ [analytics]  ← can start after Phase 8
```

**Key parallelization points:**

- **Phase 2**: Transcription, face detection, and audio analysis are fully independent — develop in parallel
- **Phase 6**: Hook generator, TTS, and subtitle modules are independently developable (renderer integrates them)
- **Phase 7**: Thumbnail and metadata modules are fully independent
- **Phase 8**: Storage and scheduler have minimal coupling

---

## Deterministic System Invariants

The following invariants are **non-negotiable**. They must hold true in every pipeline run, on every machine, for every input. Any violation is a critical bug.

### 1. Same Input → Same Output

Given identical input video file and identical `config.yaml`, the pipeline MUST produce:

- Identical scene boundaries
- Identical transcriptions (same model, same settings)
- Identical face detection results (same sampling rate, same threshold)
- Identical composite scores (same formula, same weights)
- Identical clip selections (same merging algorithm)
- Identical clip IDs (deterministic hash computation)
- Identical hook text (deterministic template selection)
- Identical subtitles (same word timestamps)
- Identical thumbnails (same frame selection, same text overlay)
- Identical metadata (same templates, same keyword extraction)

**Exception:** Edge TTS has minor synthesis variance across runs. TTS output is cached by text hash to ensure determinism on subsequent runs.

### 2. Idempotent Execution

Running the pipeline twice on the same input MUST:

- Produce zero new database rows
- Write zero new files
- Return the cached result immediately
- Complete in < 5 seconds (cache check only)

### 3. No Randomness

The pipeline MUST NOT use:

- `random.random()` or any PRNG without fixed seed
- `datetime.now()` as a decision input (only for logging timestamps)
- Network-dependent logic during processing (offline-first)
- Environment variables that change pipeline behavior (config.yaml is the single source of truth)

### 4. No External Runtime Dependencies

During video processing (ingestion through rendering), the pipeline MUST NOT:

- Make network requests
- Query external APIs
- Read from mutable external state
- Depend on services being available

**Exception:** The publisher module (Phase 9) requires YouTube API access. This is the only module with network dependency, and it runs as a separate cron process.

### 5. Forward-Only State Transitions

- Pipeline runs transition: `started → analyzing → building → completed | partial | failed`
- Clips transition: `generated → queued → scheduled → published | failed`
- No backward transitions are permitted
- State transitions are atomic (within a SQLite transaction)

### 6. Content-Addressable Identity

All entity IDs are derived from content:

- `video_id` = `SHA256(first_10MB + file_size)[:16]`
- `scene_id` = `{video_id}_{start_ms}_{end_ms}`
- `clip_id` = `SHA256(video_id + start_ms + end_ms)[:16]`

IDs are never random UUIDs (except `run_id` for pipeline execution audit, which is not content-derived).

### 7. Deterministic Ordering

All ordered collections MUST use explicit, deterministic sort keys. No implicit ordering is allowed.

| Collection       | Primary Sort Key  | Tiebreaker         | Direction |
| ---------------- | ----------------- | ------------------ | --------- |
| Scored scenes    | `composite_score` | `start_time` (ASC) | DESC      |
| Clip candidates  | `composite_score` | `start_time` (ASC) | DESC      |
| Scheduled clips  | `composite_score` | `start_time` (ASC) | DESC      |
| Scenes (display) | `start_time`      | `scene_id` (ASC)   | ASC       |

**Rules:**

- Every `ORDER BY` clause MUST include a tiebreaker column to ensure deterministic results when primary keys are equal
- No code may rely on database insertion order or Python `dict` ordering for deterministic behavior
- All sorting must be explicitly stated in the query or algorithm — no implicit defaults

---

## Not In This Roadmap

The following are explicitly excluded from the implementation scope. They are not deferred features — they are architectural decisions against inclusion.

### Microservices or Distributed Systems

The system runs as a single Python process backed by a single SQLite file. No message queues, no container orchestration, no service mesh.

### Paid APIs or Cloud Services

No OpenAI, Google Cloud, AWS, Anthropic, ElevenLabs. Every computation runs locally. The only external API is YouTube Data API v3 for publishing (free within standard quotas).

### Autonomous AI Agents

No LangChain, AutoGPT, CrewAI, or any agent-based runtime. Every pipeline decision is deterministic and hardcoded.

### Real-Time Processing

No live streaming, no webhooks, no event-driven architecture. Batch processing only.

### Web or Mobile Interface

No dashboard, no web UI, no mobile app. CLI-only interaction. Output inspection via filesystem.

### ML-Based Scoring

Scoring is rule-based with configurable weights. No trained models, no inference pipelines, no feature stores.

### Multi-Language Support (v1)

English only for transcription, hooks, metadata, subtitles. Internationalization deferred to v2+.

### Content Moderation AI

No automated safety scanning. Content safety is handled at the source level (controlled recording environment).

### Feedback Loops (v1)

No automated scoring weight adjustment based on published Short performance. Quality improvement is manual via config.yaml tuning.

---

## Architecture Protection Rules

These rules protect the system's architectural integrity across all development phases. Violations must be caught in code review.

### Rule 1: No Breaking DTO Contracts

DTO contracts in the `contracts/` package are the system's integration backbone.

- New fields may be added (with defaults for backward compatibility)
- Existing fields may NEVER be renamed, retyped, or removed
- Every DTO change requires a PR that modifies ONLY the contracts package — no module code mixed in

### Rule 2: No Cross-Module Imports

Module A must NEVER import from Module B's internal package.

- The only shared imports are from `contracts/` and `core/`
- If Module A needs data from Module B, it must receive it via the orchestrator passing Module B's output DTO
- Direct filesystem reads of another module's output directory are forbidden — use the DTO

### Rule 3: No Bypassing the Orchestrator

All module execution flows through the central orchestrator.

- No module may invoke another module directly
- No module may read the database to discover another module's state
- The orchestrator is the single point that sequences module execution and passes DTOs

### Rule 4: No Introducing Async Complexity

The pipeline is synchronous and sequential (with explicit parallel sections managed by the orchestrator).

- No `asyncio`, no threading pools, no concurrent.futures (except where explicitly specified in the orchestrator for independent stages like thumbnail + metadata)
- No background workers within the pipeline process
- The publisher runs as a separate process — but it is NOT async; it is a synchronous cron script

### Rule 5: No Network Calls During Processing

From ingestion through rendering, zero network calls.

- No HTTP requests
- No DNS lookups
- No cloud storage access
- Edge TTS is an exception — if it requires network, the TTS module must handle offline fallback (pyttsx3)

### Rule 6: No Mutable Global State

- No global variables modified during pipeline execution
- No singleton objects with mutable state
- Configuration is loaded once at startup and treated as immutable for the duration of the run
- Database is the only mutable state, and all access goes through the storage module

### Rule 7: No Silent Failures

Every failure must produce a structured log entry with:

- The module name
- The clip_id (if applicable)
- The error reason
- The action taken (skip, retry, abort)

No exception may be caught and silently swallowed. `except: pass` is never acceptable.

---

_End of implementation roadmap._
