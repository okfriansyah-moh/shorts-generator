# Shorts Factory — Orchestrator Specification

> **Version:** 1.0
> **Date:** 2026-03-24
> **Status:** Design Phase
> **Type:** System Execution Specification (Authoritative)
> **Author:** System Architect

This document defines the **exact runtime execution behavior** of the Shorts Factory pipeline orchestrator. It specifies how stages execute, how state transitions occur, how failures propagate, how checkpointing works, how resume works, and how idempotency is enforced at every level.

This specification is **authoritative**. Where ambiguity exists in other documents, this document takes precedence for all questions of execution order, state management, and failure handling.

---

## Table of Contents

- [1. Orchestrator Role](#1-orchestrator-role)
- [2. Pipeline Execution Model](#2-pipeline-execution-model)
- [3. Stage Definition](#3-stage-definition)
- [4. Checkpointing Strategy](#4-checkpointing-strategy)
- [5. Resume Behavior](#5-resume-behavior)
- [6. Idempotency Enforcement](#6-idempotency-enforcement)
- [7. State Authority Rules](#7-state-authority-rules)
- [8. Failure Handling Policy](#8-failure-handling-policy)
- [9. Retry Strategy](#9-retry-strategy)
- [10. Deterministic Ordering Rules](#10-deterministic-ordering-rules)
- [11. Concurrency Model](#11-concurrency-model)
- [12. Orchestrator Invariants](#12-orchestrator-invariants)
- [13. Failure Recovery Scenarios](#13-failure-recovery-scenarios)
- [14. Observability Hooks](#14-observability-hooks)
- [15. Final Execution Summary](#15-final-execution-summary)

---

## 1. Orchestrator Role

### 1.1 Definition

The orchestrator is the **single entry point** for all pipeline execution. It is implemented in `core/orchestrator.py` and invoked exclusively through `run_pipeline.py`. No module may invoke another module directly. No module may read the database to discover another module's state. No external script may call a module function without going through the orchestrator.

### 1.2 Responsibilities

The orchestrator owns exactly five concerns:

| Concern                   | Description                                                                                                                                                                           |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- | ------------------------------------------------- |
| **Execution Order**       | Defines and enforces the canonical stage sequence. No stage may run before its prerequisites. No stage may be skipped.                                                                |
| **State Transitions**     | Owns all writes to the `pipeline_runs` table. Transitions pipeline run status (`started → analyzing → building → completed                                                            | partial | failed`). No module may write to `pipeline_runs`. |
| **Checkpoint Management** | Persists `last_completed_stage` after each successful stage. Uses this field to determine resume point on restart.                                                                    |
| **Failure Handling**      | Catches all exceptions from module execution. Decides whether to retry, skip, or abort based on failure policy. Logs all failures with structured context.                            |
| **DTO Routing**           | Passes output DTOs from upstream modules as input DTOs to downstream modules. The orchestrator is the only component that holds references to multiple module outputs simultaneously. |

### 1.3 What the Orchestrator Does NOT Do

| Exclusion                                  | Reason                                                                                                                                       |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| No domain logic                            | The orchestrator does not compute scores, detect faces, or generate text. It only sequences modules that do.                                 |
| No direct database queries for domain data | It reads `pipeline_runs` for state management and `clips` for progress tracking. It never queries `scenes` or `videos` for processing logic. |
| No file I/O for domain artifacts           | It does not read or write video files, thumbnails, or metadata. Only modules do.                                                             |
| No configuration interpretation            | It reads the stage list from config. It does not interpret scoring weights, FFmpeg flags, or template pools.                                 |

### 1.4 Single Process Guarantee

The orchestrator runs as a single Python process. There is exactly one orchestrator instance per pipeline execution. There is no daemon mode, no background worker pool, no secondary process. The entire pipeline — from ingestion through scheduling — executes within a single process boundary.

The only exception is the publisher, which runs as a separate cron-triggered process outside the main pipeline. The publisher has its own entry point (`scripts/publish_cron.py`) and does not interact with the orchestrator.

---

## 2. Pipeline Execution Model

### 2.1 Execution Principle

The pipeline executes as a **strict sequential stage loop**. Each stage runs to completion before the next stage begins. There are no parallel stages at the pipeline level. Within a stage that processes multiple clips (per-clip stages), clips are processed sequentially in deterministic order.

### 2.2 Execution Algorithm

The orchestrator follows this exact algorithm on every invocation. Every step is mandatory. No step may be skipped or reordered.

```
ORCHESTRATOR EXECUTION ALGORITHM
================================

STEP 1: INITIALIZE
  1.1  Load config.yaml → validate all fields → abort if invalid
  1.2  Initialize structured logger (stdout + file target)
  1.3  Initialize SQLite connection (WAL mode)
  1.4  Run pending database migrations
  1.5  Verify external dependencies (FFmpeg, FFprobe, Python ≥ 3.10)
       → abort with CRITICAL if any missing
  1.6  Parse CLI arguments → extract video file path
  1.7  Verify input file exists and is readable
       → abort with CRITICAL if not

STEP 2: PRE-FLIGHT CHECKS
  2.1  Probe input video with FFprobe:
       → extract duration, resolution, codec, audio stream presence
  2.2  Validate: duration within [30, 120] minutes → abort if not
  2.3  Validate: audio stream present → abort if not
  2.4  Validate: format is MP4/MKV/AVI → abort if not
  2.5  Compute available disk space
  2.6  Validate: disk space ≥ 3× input file size → abort if not
  2.7  Log all pre-flight results as structured INFO

STEP 3: DETERMINE VIDEO IDENTITY
  3.1  Read first 10MB of input file
  3.2  Compute video_id = SHA256(first_10MB + str(file_size))[:16]
  3.3  Query pipeline_runs WHERE video_id = {video_id}
       AND status = 'completed'
  3.4  IF completed run exists:
       → log "Video already processed" as INFO
       → return early (exit code 0)
       → pipeline does NOT re-execute

STEP 4: LOAD OR CREATE PIPELINE RUN
  4.1  Query pipeline_runs WHERE video_id = {video_id}
       AND status IN ('started', 'analyzing', 'building', 'partial', 'failed')
  4.2  IF existing incomplete run found:
       → load run_id and last_completed_stage from that row
       → log "Resuming pipeline run {run_id} from stage after {last_completed_stage}"
  4.3  IF no existing run found:
       → generate new run_id (UUID4)
       → INSERT INTO pipeline_runs (run_id, video_id, status='started',
         last_completed_stage=NULL, config_snapshot={serialized_config})
       → log "Starting new pipeline run {run_id}"

STEP 5: DETERMINE RESUME POINT
  5.1  Read last_completed_stage from pipeline_runs row
  5.2  IF last_completed_stage IS NULL:
       → resume_index = 0 (start from first stage)
  5.3  IF last_completed_stage IS NOT NULL:
       → find index of last_completed_stage in STAGE_ORDER
       → resume_index = found_index + 1
  5.4  Validate resume_index is within STAGE_ORDER bounds
       → if resume_index ≥ len(STAGE_ORDER): all stages done, run cleanup

STEP 6: EXECUTE STAGE LOOP
  6.1  FOR stage_index FROM resume_index TO len(STAGE_ORDER) - 1:

       6.1.1  stage = STAGE_ORDER[stage_index]

       6.1.2  LOG stage start:
              → {"run_id", "video_id", "stage": stage.name,
                 "status": "started", "timestamp"}

       6.1.3  UPDATE pipeline_runs SET status = stage.phase_status
              (e.g., 'analyzing' for analysis stages, 'building' for build stages)

       6.1.4  PRE-STAGE VALIDATION:
              → verify all required input DTOs are available
              → verify disk space still sufficient (≥ 500MB remaining)
              → if validation fails → execute FAILURE HANDLER (Section 8)

       6.1.5  EXECUTE STAGE:
              → call stage.execute(input_dtos) → output_dto
              → measure execution duration in milliseconds
              → catch all exceptions → route to FAILURE HANDLER (Section 8)

       6.1.6  POST-STAGE VALIDATION:
              → verify output_dto is not None
              → verify output_dto type matches expected type
              → for file-producing stages: verify output files exist on disk
              → if validation fails → execute FAILURE HANDLER (Section 8)

       6.1.7  CHECKPOINT:
              → UPDATE pipeline_runs
                SET last_completed_stage = stage.name
                WHERE run_id = {run_id}
              → this write MUST succeed before proceeding
              → if DB write fails → abort pipeline (Section 8.1)

       6.1.8  LOG stage completion:
              → {"run_id", "video_id", "stage": stage.name,
                 "status": "success", "duration_ms", "timestamp"}

       6.1.9  Store output_dto in memory for downstream stages

  6.2  AFTER all stages complete:
       → UPDATE pipeline_runs SET status = 'completed',
         completed_at = CURRENT_TIMESTAMP,
         clips_generated = {count}
       → log pipeline completion summary

STEP 7: CLEANUP
  7.1  Delete orphaned .tmp files in output/{video_id}/
  7.2  Log total pipeline duration
  7.3  Print human-readable summary to stdout
  7.4  Exit with code 0
```

### 2.3 Stage Phase Mapping

The `pipeline_runs.status` field reflects which phase of execution the orchestrator is in. The orchestrator updates this field at specific transition points:

| Pipeline Status | Active Stages                                                                      | Updated When                                                |
| --------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `started`       | None yet — pre-flight and ingestion                                                | Pipeline run row created                                    |
| `analyzing`     | `ingestion`, `scene_splitter`, `transcription`, `face_detection`, `audio_analysis` | First analysis stage begins                                 |
| `building`      | `scoring`, `clip_builder`, and all per-clip stages                                 | Scoring stage begins                                        |
| `completed`     | All stages finished successfully                                                   | Final stage completes                                       |
| `partial`       | All stages attempted, some clips failed                                            | Building completed with clip failures below abort threshold |
| `failed`        | Pipeline aborted                                                                   | Any stage-level failure or abort threshold reached          |

### 2.4 DTO Flow Diagram

The orchestrator holds all intermediate DTOs in memory and passes them forward:

```
                     Orchestrator (DTO holder)
                     ┌─────────────────────────────────────────────┐
                     │                                             │
ingestion ──────────→│ ingestion_result ─────────→ scene_splitter  │
                     │                │                    │       │
                     │                │                    ▼       │
                     │                ├──→ transcription ──→ transcript
                     │                │                            │
                     │                ├──→ face_detection ──→ face_result
                     │                │                            │
                     │                └──→ audio_analysis ──→ audio_data
                     │                                             │
                     │ scene_list + transcript + face_result        │
                     │   + audio_data ─────────→ scoring            │
                     │                              │              │
                     │                              ▼              │
                     │                        scored_scene_list     │
                     │                              │              │
                     │                              ▼              │
                     │                        clip_builder          │
                     │                              │              │
                     │                              ▼              │
                     │                         clip_list            │
                     │                              │              │
                     │              FOR EACH clip IN clip_list:     │
                     │                              │              │
                     │    clip + transcript ──→ hook_generator      │
                     │                              │              │
                     │         hook_result ──→ tts                  │
                     │                              │              │
                     │    transcript + tts ──→ subtitle             │
                     │                              │              │
                     │    clip + face_result ──→ compositor         │
                     │                              │              │
                     │    composite + tts                           │
                     │      + subtitle ────→ renderer               │
                     │                              │              │
                     │    clip + face + hook ──→ thumbnail          │
                     │                              │              │
                     │    hook + transcript ──→ metadata            │
                     │                              │              │
                     │    rendered + thumb                          │
                     │      + metadata ────→ storage                │
                     │                                             │
                     │ all storage_records ──→ scheduler            │
                     └─────────────────────────────────────────────┘
```

The orchestrator never exposes one module's DTO to a module that does not list it in its input contract. DTO routing is explicit, not broadcast.

---

## 3. Stage Definition

### 3.1 Canonical Stage List

The pipeline consists of exactly 13 stages, executed in this fixed order. This order is immutable. No stage may be added, removed, or reordered without updating this specification.

| Index | Stage Name            | Stage Type  | Granularity   | Input DTOs                                                                           | Output DTO                                          |
| ----- | --------------------- | ----------- | ------------- | ------------------------------------------------------------------------------------ | --------------------------------------------------- |
| 0     | `ingestion`           | Video-level | Once per run  | File path (string)                                                                   | `IngestionResult`                                   |
| 1     | `scene_splitter`      | Video-level | Once per run  | `IngestionResult`                                                                    | `SceneList`                                         |
| 2     | `transcription`       | Video-level | Once per run  | `IngestionResult`                                                                    | `Transcript`                                        |
| 3     | `face_detection`      | Video-level | Once per run  | `IngestionResult`, `SceneList`                                                       | `FaceDetectionResult`                               |
| 4     | `audio_analysis`      | Video-level | Once per run  | `IngestionResult`, `SceneList`                                                       | Audio energy data                                   |
| 5     | `scoring`             | Video-level | Once per run  | `SceneList`, `Transcript`, `FaceDetectionResult`, Audio energy                       | `ScoredSceneList`                                   |
| 6     | `clip_builder`        | Video-level | Once per run  | `ScoredSceneList`                                                                    | `ClipList`                                          |
| 7     | `per_clip_processing` | Clip-level  | Once per clip | `ClipDefinition`, `Transcript`, `FaceDetectionResult`, `HookResult` (via sub-stages) | `RenderedClip`, `ThumbnailResult`, `MetadataResult` |
| 8     | `storage`             | Clip-level  | Once per clip | `RenderedClip`, `ThumbnailResult`, `MetadataResult`                                  | `StorageRecord`                                     |
| 9     | `scheduler`           | Batch-level | Once per run  | List of `StorageRecord`                                                              | Updated `StorageRecord` list                        |

### 3.2 Per-Clip Processing Sub-Stages

Stage 7 (`per_clip_processing`) is a composite stage that iterates over every clip in the `ClipList` and executes the following sub-stages **sequentially per clip** in this exact order:

| Sub-Index | Sub-Stage Name   | Input DTOs                                            | Output DTO        |
| --------- | ---------------- | ----------------------------------------------------- | ----------------- |
| 7.0       | `hook_generator` | `ClipDefinition`, `Transcript`                        | `HookResult`      |
| 7.1       | `tts`            | `HookResult`                                          | `TTSResult`       |
| 7.2       | `subtitle`       | `Transcript`, `TTSResult`, `ClipDefinition`           | `SubtitleResult`  |
| 7.3       | `compositor`     | `ClipDefinition`, `FaceDetectionResult`               | `CompositeStream` |
| 7.4       | `renderer`       | `CompositeStream`, `TTSResult`, `SubtitleResult`      | `RenderedClip`    |
| 7.5       | `thumbnail`      | `ClipDefinition`, `FaceDetectionResult`, `HookResult` | `ThumbnailResult` |
| 7.6       | `metadata`       | `HookResult`, `Transcript`, `ClipDefinition`          | `MetadataResult`  |

Each clip must complete ALL sub-stages before the next clip begins. No interleaving of clips.

### 3.3 Stage Input/Output Requirements

Every stage defines strict preconditions and postconditions. The orchestrator validates both before and after execution.

**Preconditions** (checked before stage executes):

| Stage                 | Required Precondition                                                         |
| --------------------- | ----------------------------------------------------------------------------- |
| `ingestion`           | Input file exists, is readable, disk space sufficient                         |
| `scene_splitter`      | `IngestionResult` available in memory                                         |
| `transcription`       | `IngestionResult` available in memory                                         |
| `face_detection`      | `IngestionResult` and `SceneList` available in memory                         |
| `audio_analysis`      | `IngestionResult` and `SceneList` available in memory                         |
| `scoring`             | `SceneList`, `Transcript`, `FaceDetectionResult`, audio energy data available |
| `clip_builder`        | `ScoredSceneList` available in memory                                         |
| `per_clip_processing` | `ClipList` with ≥ 1 clip, all analysis DTOs available                         |
| `storage`             | `RenderedClip`, `ThumbnailResult`, `MetadataResult` for this clip available   |
| `scheduler`           | At least one `StorageRecord` with status `queued`                             |

**Postconditions** (checked after stage executes):

| Stage                 | Required Postcondition                                                                       |
| --------------------- | -------------------------------------------------------------------------------------------- |
| `ingestion`           | `IngestionResult` has non-empty `video_id`, valid `duration_seconds` > 0, valid `resolution` |
| `scene_splitter`      | `SceneList` has ≥ 1 scene, all scenes have valid `scene_id`, no scene < 3s or > 20s          |
| `transcription`       | `Transcript` object returned (may be empty — that is valid)                                  |
| `face_detection`      | `FaceDetectionResult` object returned (may have all-zero visibility — that is valid)         |
| `audio_analysis`      | Audio energy data returned with one entry per scene                                          |
| `scoring`             | `ScoredSceneList` with all scenes scored, all composites in [0, 1] range                     |
| `clip_builder`        | `ClipList` with ≥ 1 clip, all clips within [30, 60] seconds                                  |
| `per_clip_processing` | For each clip: all output files exist on disk, all DTOs populated                            |
| `storage`             | `StorageRecord` written to DB, clip status = `queued`, all file paths valid                  |
| `scheduler`           | All queued clips have `scheduled_at` set, status = `scheduled`                               |

If any postcondition fails, the orchestrator treats the stage as failed and routes to the failure handler.

---

## 4. Checkpointing Strategy

The orchestrator implements two independent checkpointing mechanisms: pipeline-level and clip-level. Both use the SQLite database as the sole source of truth.

### 4.1 Pipeline-Level Checkpoint

**Storage location:** `pipeline_runs.last_completed_stage`

**Update timing:** The orchestrator updates `last_completed_stage` **immediately after** a stage completes successfully and **after** the postcondition check passes. The checkpoint write is the **last operation** of every stage — it occurs after all stage outputs are persisted.

**Checkpoint write procedure:**

```
1. Stage executes successfully
2. Postcondition validation passes
3. All output DTOs stored in memory
4. For file-producing stages: verify output files exist on disk
   → IF any expected file is missing → mark stage as failed, do NOT checkpoint
5. Verify DB records match outputs (filesystem ↔ database consistency)
   → IF mismatch detected → mark stage as failed, do NOT checkpoint
6. UPDATE pipeline_runs SET last_completed_stage = '{stage_name}'
   WHERE run_id = '{run_id}'
7. COMMIT
8. If DB write fails → abort pipeline immediately (database integrity failure)
9. Proceed to next stage
```

**Rules:**

| Rule                      | Description                                                                                                                                                                                      |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **No skip-forward**       | `last_completed_stage` advances by exactly one stage at a time. The orchestrator never writes a stage name that is not the immediate successor of the previous value.                            |
| **No automatic rollback** | The orchestrator never sets `last_completed_stage` to a previous value. If a stage fails, the checkpoint remains at the last successfully completed stage. Rollback requires the `--force` flag. |
| **Atomic writes**         | The checkpoint update is a single SQL `UPDATE` within a transaction. If the write fails, the transaction rolls back and the checkpoint remains unchanged.                                        |
| **Commit-before-proceed** | The orchestrator MUST confirm the checkpoint write committed successfully before beginning the next stage. A failed checkpoint write is treated as a pipeline-level failure (Section 8.1).       |

**Checkpoint state progression example:**

```
Pipeline start:        last_completed_stage = NULL
After ingestion:       last_completed_stage = 'ingestion'
After scene_splitter:  last_completed_stage = 'scene_splitter'
After transcription:   last_completed_stage = 'transcription'
After face_detection:  last_completed_stage = 'face_detection'
After audio_analysis:  last_completed_stage = 'audio_analysis'
After scoring:         last_completed_stage = 'scoring'
After clip_builder:    last_completed_stage = 'clip_builder'
After per_clip_processing: last_completed_stage = 'per_clip_processing'
After storage:         last_completed_stage = 'storage'
After scheduler:       last_completed_stage = 'scheduler'
```

### 4.2 Clip-Level Checkpoint

**Storage location:** `clips` table, `status` column + per-clip file existence on disk.

**Purpose:** Within the `per_clip_processing` stage, multiple clips are processed sequentially. If the pipeline crashes mid-way through clip processing, some clips will be fully processed and some will not. The clip-level checkpoint enables resuming from the exact clip where processing stopped.

**Clip processing tracking:**

The orchestrator does NOT checkpoint individual sub-stages within a clip. The clip is the atomic unit. Either all sub-stages complete for a clip (and it is written to `clips` table with `status = 'generated'`), or none of them are persisted.

**Checkpoint write procedure per clip:**

```
1. Execute all sub-stages (hook → tts → subtitle → compositor → renderer → thumbnail → metadata)
2. All sub-stages succeed
3. Execute storage stage for this clip:
   → INSERT INTO clips (clip_id, ..., status = 'generated') ON CONFLICT (clip_id) DO NOTHING
   → Write all files to output/{video_id}/clips/{clip_id}/
   → Update status to 'queued'
4. COMMIT
5. If any sub-stage fails → route to clip failure handler (Section 8.2)
6. Log clip completion
7. Proceed to next clip
```

**Rules:**

| Rule                                         | Description                                                                                                                                                                                                        |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Skip completed clips**                     | Before processing a clip, the orchestrator checks: does `clip_id` exist in the `clips` table with `status` IN (`generated`, `queued`, `scheduled`, `published`)? If yes → skip this clip entirely.                 |
| **Process only incomplete clips**            | If a clip exists with `status = 'failed'` or no row exists → process it.                                                                                                                                           |
| **Allow partial progress**                   | If 7 of 12 clips complete and then the pipeline crashes, on resume the orchestrator re-enters `per_clip_processing`, iterates the full clip list, skips the 7 completed clips, and processes only the remaining 5. |
| **No partial clip writes**                   | There is no checkpoint between sub-stages within a single clip. If the pipeline crashes between the `tts` and `subtitle` sub-stages for clip X, the entire clip X is reprocessed from `hook_generator` on resume.  |
| **Intermediate files are not authoritative** | The existence of `composite.mp4` without a corresponding `clips` row does NOT mean the clip is partially done. The orchestrator ignores intermediate files. Only the database row constitutes a completed clip.    |

### 4.3 Checkpoint Interaction Between Levels

The pipeline-level checkpoint (`last_completed_stage`) for `per_clip_processing` is set **only after all clips have been processed** (or skipped/failed within threshold). It is NOT updated per-clip.

```
Pipeline run starts
→ Stages 0–6 complete (each checkpointed in pipeline_runs)
→ Stage 7 (per_clip_processing) begins:
   → Clip 1: processed → stored in clips table
   → Clip 2: processed → stored in clips table
   → ...
   → Clip N: processed → stored in clips table
→ Stage 7 completes
→ CHECKPOINT: last_completed_stage = 'per_clip_processing'
```

If the pipeline crashes between Clip 5 and Clip 6:

- `pipeline_runs.last_completed_stage` still equals `clip_builder` (the stage BEFORE per_clip_processing)
- Clips 1–5 exist in the `clips` table
- On resume, the orchestrator re-enters `per_clip_processing`, skips clips 1–5, processes 6–N

This design ensures that the pipeline-level checkpoint always reflects a fully completed stage, never a partially completed one.

---

## 5. Resume Behavior

### 5.1 Resume Detection

When the orchestrator starts, it determines whether this is a fresh run or a resume:

```
1. Compute video_id from input file
2. Query: SELECT run_id, last_completed_stage, status
          FROM pipeline_runs
          WHERE video_id = {video_id}
          ORDER BY started_at DESC
          LIMIT 1
3. CASE status:
   'completed'  → exit early, video already processed
   'started'    → resume from stage 0 (ingestion)
   'analyzing'  → resume from stage after last_completed_stage
   'building'   → resume from stage after last_completed_stage
   'partial'    → resume from per_clip_processing (retry failed clips)
   'failed'     → resume from stage after last_completed_stage
   NULL (no row) → create new run, start from stage 0
```

### 5.2 Resume Point Determination

The resume point is computed as:

```
IF last_completed_stage IS NULL:
    resume_from = STAGE_ORDER[0]  # ingestion
ELSE:
    idx = STAGE_ORDER.index(last_completed_stage)
    resume_from = STAGE_ORDER[idx + 1]
```

There is no ambiguity. `last_completed_stage` names the last stage that **fully completed and was checkpointed**. The next stage in `STAGE_ORDER` is the resume target.

### 5.3 DTO Reconstruction on Resume

When resuming, the orchestrator must reconstruct DTOs for stages that already completed in a previous run. It does NOT re-execute those stages. Instead:

**Video-level stages (0–6) reconstruct from database:**

| Stage            | DTO Reconstruction Source                                                                                                                                                |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ingestion`      | Query `videos` table by `video_id` → rebuild `IngestionResult`                                                                                                           |
| `scene_splitter` | Query `scenes` table by `video_id` → rebuild `SceneList`                                                                                                                 |
| `transcription`  | Query `scenes` table `transcript_text` column → rebuild `Transcript`. If word-level data is needed, read cached transcript file from `output/{video_id}/transcript.json` |
| `face_detection` | Query `scenes` table `face_visible_ratio` column + read cached face data from `output/{video_id}/face_data.json` → rebuild `FaceDetectionResult`                         |
| `audio_analysis` | Query `scenes` table `audio_energy_score` column → rebuild audio energy data                                                                                             |
| `scoring`        | Query `scenes` table (all score columns populated) → rebuild `ScoredSceneList`                                                                                           |
| `clip_builder`   | Query `clips` table by `video_id` → rebuild `ClipList` from stored clip definitions                                                                                      |

**Clip-level stages (7–8) use clip table for skip logic:**

The orchestrator does not reconstruct per-clip DTOs from completed clips. It simply checks `clips.status` and skips clips that already exist.

### 5.4 Resume Scenarios

**Scenario A: Crash during `transcription` (stage 2)**

```
State at crash:
  pipeline_runs.last_completed_stage = 'scene_splitter'
  pipeline_runs.status = 'analyzing'
  scenes table: populated with scene data
  transcript: NOT written

On resume:
  1. Load run from pipeline_runs
  2. last_completed_stage = 'scene_splitter' → resume_from = 'transcription'
  3. Reconstruct IngestionResult from videos table
  4. Reconstruct SceneList from scenes table
  5. Execute transcription with IngestionResult as input
  6. Continue from face_detection onward
```

**Scenario B: Crash during `per_clip_processing` (stage 7), clip 6 of 12**

```
State at crash:
  pipeline_runs.last_completed_stage = 'clip_builder'
  pipeline_runs.status = 'building'
  clips table: clips 1–5 with status 'queued', clips 6–12 not present
  output/: clips 1–5 have final.mp4, thumbnail.jpg, metadata.json
           clip 6 may have partial intermediate files (.tmp)

On resume:
  1. Load run from pipeline_runs
  2. last_completed_stage = 'clip_builder' → resume_from = 'per_clip_processing'
  3. Reconstruct all upstream DTOs (IngestionResult, SceneList, Transcript,
     FaceDetectionResult, audio data, ScoredSceneList, ClipList) from DB
  4. Enter per_clip_processing
  5. For each clip in ClipList:
     → clip 1: EXISTS in clips table with status 'queued' → SKIP
     → clip 2: EXISTS → SKIP
     → ...
     → clip 5: EXISTS → SKIP
     → clip 6: NOT in clips table → PROCESS (full sub-stage sequence)
     → clip 7–12: NOT in clips table → PROCESS
  6. Clean orphaned .tmp files for clip 6 before reprocessing
  7. After all clips processed → checkpoint 'per_clip_processing'
```

**Scenario C: Partial completion (some clips failed, pipeline exited with `partial` status)**

```
State:
  pipeline_runs.last_completed_stage = 'per_clip_processing'
  pipeline_runs.status = 'partial'
  clips table: clips 1–10 with status 'queued', clips 11–12 with status 'failed'

On resume:
  1. Load run from pipeline_runs
  2. status = 'partial' → re-enter per_clip_processing
  3. For each clip in ClipList:
     → clips 1–10: status 'queued' → SKIP
     → clip 11: status 'failed' → REPROCESS
     → clip 12: status 'failed' → REPROCESS
  4. If clip 11/12 succeed → update to 'queued'
  5. If still failing → leave as 'failed', continue
  6. After all clips attempted → re-evaluate:
     → If all successful → status = 'completed'
     → If some still failed but below threshold → status = 'partial'
     → If above threshold → status = 'failed'
```

### 5.5 Resume Guards

The orchestrator enforces the following guards during resume:

| Guard                    | Behavior                                                                                                                                                                                                                                                                                          |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Config consistency**   | On resume, the current `config.yaml` is compared against `pipeline_runs.config_snapshot`. If scoring weights, clip durations, or other pipeline-affecting settings have changed, the orchestrator logs a `WARN` and proceeds (does not abort — config changes are the operator's responsibility). |
| **Input file unchanged** | On resume, the orchestrator recomputes `video_id` from the input file. If it differs from the stored `video_id`, the resume is aborted (the input file has changed — this is a different video).                                                                                                  |
| **Database integrity**   | On resume, the orchestrator verifies the `pipeline_runs` row is consistent: `run_id` matches, `video_id` matches, `last_completed_stage` is a valid stage name. Any inconsistency triggers an abort.                                                                                              |

---

## 6. Idempotency Enforcement

Idempotency is enforced at three levels. Each level uses its own identity key and its own deduplication mechanism.

### 6.1 Video-Level Idempotency

**Identity key:** `video_id` = `SHA256(first_10MB + str(file_size))[:16]`

**Enforcement:**

```
1. Before any processing begins, compute video_id
2. Query pipeline_runs WHERE video_id = {video_id} AND status = 'completed'
3. IF row exists → log "Already processed", exit 0
4. No processing occurs. No files written. No database rows created.
```

**Guarantees:**

- The same video file produces the same `video_id` on every machine, every run
- A completed pipeline run for this `video_id` prevents any re-execution
- Override: `--force` flag deletes all data for this `video_id` before starting

### 6.2 Scene-Level Idempotency

**Identity key:** `scene_id` = `{video_id}_{start_ms}_{end_ms}`

**Enforcement:**

```
1. Before inserting a scene into the scenes table:
   → INSERT INTO scenes (scene_id, ...) VALUES (...) ON CONFLICT (scene_id) DO NOTHING
2. Before analyzing a scene (transcription, face detection, scoring):
   → SELECT * FROM scenes WHERE scene_id = {scene_id}
   → IF all required columns are populated → skip analysis for this scene
3. Analysis results are written per-scene:
   → UPDATE scenes SET transcript_text = ..., keyword_score = ...
     WHERE scene_id = {scene_id}
```

**Guarantees:**

- No scene is analyzed twice (transcription, face detection, scoring)
- Re-running analysis stages skips already-analyzed scenes
- The `scenes` table is the cache — if a scene has a `composite_score`, scoring is done for that scene

### 6.3 Clip-Level Idempotency

**Identity key:** `clip_id` = `SHA256(video_id + str(start_ms) + str(end_ms))[:16]`

**Enforcement:**

```
1. Before processing a clip (sub-stages):
   → SELECT status FROM clips WHERE clip_id = {clip_id}
   → IF status IN ('generated', 'queued', 'scheduled', 'published') → SKIP
   → IF status = 'failed' → REPROCESS
   → IF no row → PROCESS

2. After all sub-stages complete:
   → INSERT INTO clips (clip_id, ..., status = 'generated') ON CONFLICT (clip_id) DO NOTHING
   → UPDATE clips SET status = 'queued' WHERE clip_id = {clip_id}

3. File writes:
   → Write to output/{video_id}/clips/{clip_id}/final.mp4.tmp
   → On success: rename final.mp4.tmp → final.mp4
   → If final.mp4 already exists → skip write
```

**Guarantees:**

- No clip is processed twice (unless it previously failed)
- No clip files are overwritten (atomic rename prevents partial overwrites)
- No duplicate `clip_id` rows in the database (`INSERT ... ON CONFLICT DO NOTHING`)
- Re-running the pipeline on the same video with the same config produces zero new clips

### 6.4 Idempotency Test

The orchestrator satisfies this behavioral contract:

```
run_pipeline(video_A, config_X) → produces N clips, M database rows, K files
run_pipeline(video_A, config_X) → produces 0 new clips, 0 new rows, 0 new files
                                   completes in < 5 seconds (cache check only)
```

---

## 7. State Authority Rules

### 7.1 Database is Authoritative

The SQLite database is the **single source of truth** for all pipeline state. Every question about "what has been done" is answered by querying the database. The filesystem is a derived artifact.

| Question                       | Authoritative Source                                    | NOT Authoritative                           |
| ------------------------------ | ------------------------------------------------------- | ------------------------------------------- |
| Has this video been processed? | `pipeline_runs` table                                   | Existence of `output/{video_id}/` directory |
| Has this scene been analyzed?  | `scenes` table (score columns populated)                | Existence of `scene_001.json` file          |
| Has this clip been rendered?   | `clips` table (status ≠ NULL)                           | Existence of `final.mp4` file               |
| Is this clip scheduled?        | `clips.status = 'scheduled'`                            | Existence of `metadata.json` with a date    |
| Is this clip published?        | `clips.status = 'published'` AND `youtube_id` populated | Nothing else                                |

### 7.2 Filesystem is Derived

Files on disk are **outputs**, not **state**. The orchestrator makes decisions based on database state, then verifies that files match expectations.

**File verification rules:**

```
AFTER writing a clip to the database:
1. Verify: output/{video_id}/clips/{clip_id}/final.mp4 exists
2. Verify: output/{video_id}/clips/{clip_id}/thumbnail.jpg exists
3. Verify: output/{video_id}/clips/{clip_id}/metadata.json exists
4. IF any file missing:
   → mark clip as 'failed' in database
   → log ERROR with missing file path
   → the database status overrides the file state
```

### 7.3 Consistency Rules

| Rule                                        | Description                                                                                                                                                         |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **DB says clip exists → file must exist**   | After storing a clip, the orchestrator verifies all expected files exist on disk. If a file is missing, the clip is marked as `failed`.                             |
| **File exists → DB may or may not have it** | An orphaned file (no matching DB row) is garbage. It is cleaned up during the next pipeline run's cleanup step.                                                     |
| **Never infer state from filesystem**       | The orchestrator never checks "does `final.mp4` exist?" to determine if a clip is rendered. It checks `clips.status`.                                               |
| **Never infer state from file timestamps**  | File modification times are irrelevant. Only database timestamps are used for ordering and scheduling.                                                              |
| **Never read another module's files**       | The orchestrator does not read `composite.mp4` to determine if composition is done. It checks whether the compositor module returned a valid `CompositeStream` DTO. |

### 7.4 Orphaned File Handling

Files can become orphaned when:

- The pipeline crashes after writing a file but before the corresponding DB write
- A `.tmp` file is created but never renamed (process killed during write)
- A clip is manually deleted from the database but files remain

**Cleanup procedure (runs at pipeline start, Step 7.1):**

```
1. Scan output/{video_id}/clips/ for all clip directories
2. For each clip directory:
   a. Extract clip_id from directory name
   b. Query clips table for clip_id
   c. IF no row exists → delete entire clip directory
   d. IF row exists with status 'failed':
      → delete all files BUT keep the directory
      → clip will be reprocessed
3. Scan for .tmp files anywhere in output/{video_id}/
   → delete all .tmp files (interrupted writes)
```

---

## 8. Failure Handling Policy

### 8.1 Stage-Level Failure

A stage-level failure occurs when a video-level stage (stages 0–6, 9) throws an unrecoverable exception or fails postcondition validation.

**Behavior:**

```
1. Catch exception from stage execution
2. Log CRITICAL:
   → {"run_id", "video_id", "stage", "error": str(exception),
      "status": "failed", "timestamp"}
3. UPDATE pipeline_runs
   SET status = 'failed',
       error_log = {structured_error_json},
       completed_at = CURRENT_TIMESTAMP
   WHERE run_id = {run_id}
4. COMMIT
5. Execute cleanup (delete .tmp files)
6. Exit with code 1
```

**No retry at stage level.** If `scene_splitter` fails, the pipeline aborts. The operator must diagnose and fix the issue, then re-run. The pipeline will resume from the failed stage (the checkpoint points to the last _successful_ stage).

**Exceptions to the no-retry rule:**

| Stage                     | Retry Allowed | Condition                                                                          |
| ------------------------- | ------------- | ---------------------------------------------------------------------------------- |
| `scene_splitter`          | Once          | If PySceneDetect fails with a non-standard threshold, retry with default threshold |
| `transcription`           | Once          | If faster-whisper model is not cached, retry triggers auto-download                |
| All file-producing stages | Once          | If FFmpeg process times out (> 300s), retry once                                   |

On retry failure, the stage fails permanently and the pipeline aborts.

### 8.2 Clip-Level Failure

A clip-level failure occurs when any sub-stage within `per_clip_processing` fails for a specific clip.

**Behavior:**

```
1. Catch exception from clip sub-stage
2. Log ERROR:
   → {"run_id", "video_id", "clip_id", "sub_stage", "error",
      "status": "failed", "timestamp"}
3. Increment clip_failure_count for this run
4. Delete any partial outputs for this clip:
   → Remove .tmp files in output/{video_id}/clips/{clip_id}/
   → Do NOT write a 'failed' row to clips table
      (the absence of a row means "not processed")
5. Check failure threshold:
   → IF clip_failure_count > (total_clips * 0.5):
     → ABORT pipeline (Section 8.3)
   → ELSE:
     → log WARN: "Skipping clip {clip_id}, continuing"
     → proceed to next clip
```

**Key distinction:** A failed clip does NOT abort the pipeline (unless the threshold is breached). The pipeline continues to the next clip. At the end of `per_clip_processing`, if any clips failed:

- `pipeline_runs.status` = `partial` (not `completed`)
- Failed clips have no row in `clips` table (they will be retried on next run)

### 8.3 Failure Threshold

The pipeline aborts if clip processing failures exceed 50% of total clips in the current run.

**Threshold computation:**

```
total_clips = len(ClipList)
max_failures = floor(total_clips * 0.5)

IF clip_failure_count > max_failures:
    → ABORT pipeline
    → UPDATE pipeline_runs SET status = 'failed',
        error_log = "Clip failure threshold exceeded:
                     {clip_failure_count}/{total_clips} clips failed"
    → exit code 1
```

**Examples:**

| Total Clips | Max Failures | Abort At         |
| ----------- | ------------ | ---------------- |
| 12          | 6            | 7th clip failure |
| 10          | 5            | 6th clip failure |
| 5           | 2            | 3rd clip failure |

### 8.4 Failure Categorization

| Failure Type                       | Severity | Action                                       | Pipeline Effect   |
| ---------------------------------- | -------- | -------------------------------------------- | ----------------- |
| Config file missing/invalid        | CRITICAL | Abort before processing                      | Exit code 1       |
| FFmpeg/FFprobe not found           | CRITICAL | Abort before processing                      | Exit code 1       |
| Input file not found               | CRITICAL | Abort at ingestion                           | Status = `failed` |
| Input file corrupt (FFprobe fails) | CRITICAL | Abort at ingestion                           | Status = `failed` |
| Disk space exhausted               | CRITICAL | Abort immediately, cleanup                   | Status = `failed` |
| Database write failure             | CRITICAL | Abort immediately                            | Status = `failed` |
| Scene splitter crash               | ERROR    | Retry once → abort                           | Status = `failed` |
| Transcription model load failure   | ERROR    | Retry once (download) → abort                | Status = `failed` |
| MediaPipe load failure             | ERROR    | Abort                                        | Status = `failed` |
| No scenes detected                 | WARN     | Create single scene for full video           | Continue          |
| No speech detected                 | WARN     | Empty transcript (valid state)               | Continue          |
| No face detected                   | WARN     | Fallback layout                              | Continue          |
| FFmpeg render timeout (per clip)   | ERROR    | Retry once → skip clip                       | Continue          |
| Edge TTS failure (per clip)        | ERROR    | Fallback to pyttsx3 → skip clip if both fail | Continue          |
| Thumbnail generation failure       | ERROR    | Skip clip                                    | Continue          |
| Metadata generation failure        | ERROR    | Skip clip                                    | Continue          |

---

## 9. Retry Strategy

### 9.1 Per-Stage Retry

Video-level stages have limited retry capabilities. Retries are **in-process** (not on re-run — immediate retry within the same pipeline execution).

| Stage            | Max Retries | Retry Condition                    | Retry Behavior                                               |
| ---------------- | ----------- | ---------------------------------- | ------------------------------------------------------------ |
| `scene_splitter` | 1           | PySceneDetect exception            | Retry with default threshold value (ignore custom threshold) |
| `transcription`  | 1           | Model not found / download failure | Attempt model download, then retry                           |
| `face_detection` | 0           | N/A                                | No retry — MediaPipe load is deterministic                   |
| `audio_analysis` | 1           | FFmpeg extraction failure          | Retry once with default FFmpeg settings                      |
| `scoring`        | 0           | N/A                                | No retry — pure computation, failure is a bug                |
| `clip_builder`   | 0           | N/A                                | No retry — pure computation, failure is a bug                |

**Retry execution:**

```
try:
    result = stage.execute(input_dtos)
except StageException as e:
    if stage.retries_remaining > 0:
        stage.retries_remaining -= 1
        log WARN: "Stage {stage.name} failed, retrying ({stage.max_retries - stage.retries_remaining}/{stage.max_retries})"
        result = stage.execute_with_fallback(input_dtos)
    else:
        raise  # propagates to stage failure handler (Section 8.1)
```

### 9.2 Per-Clip Retry

Within `per_clip_processing`, individual sub-stages have retry capabilities:

| Sub-Stage        | Max Retries | Retry Behavior                                          |
| ---------------- | ----------- | ------------------------------------------------------- |
| `hook_generator` | 0           | No retry — template logic is deterministic              |
| `tts`            | 1           | Retry with fallback engine (Edge TTS → pyttsx3)         |
| `subtitle`       | 0           | No retry — ASS generation is deterministic              |
| `compositor`     | 1           | Retry with simplified filter chain (no zoom, no smooth) |
| `renderer`       | 1           | Retry with higher CRF (lower quality, more reliable)    |
| `thumbnail`      | 1           | Retry with midpoint frame (skip frame scoring)          |
| `metadata`       | 0           | No retry — template logic is deterministic              |

**Per-clip retry does NOT restart the entire clip.** If `renderer` fails and retries, only the renderer sub-stage is re-executed. The preceding sub-stage outputs (hook, tts, subtitle, composite) are still in memory.

### 9.3 Backoff Strategy

For in-process retries, there is no delay between attempts. The retry is immediate because:

- No external service rate limiting applies (all local processing)
- The failure is either transient (FFmpeg hiccup) or permanent (bug). Delay doesn't help either case.
- The retry uses fallback parameters, which addresses the most common transient failure (resource contention, parameter sensitivity)

**Exception:** The publisher module (separate process) uses exponential backoff:

| Attempt     | Delay                |
| ----------- | -------------------- |
| 1st failure | 60 seconds           |
| 2nd failure | 300 seconds          |
| 3rd failure | 900 seconds          |
| 4th failure | Abort, mark `failed` |

---

## 10. Deterministic Ordering Rules

Every list processed by the orchestrator has an explicit, deterministic sort order. No implicit ordering is ever relied upon.

### 10.1 Scene Ordering

**Sort key:** `start_time ASC`

All scene lists are sorted by scene start time in ascending chronological order. This applies to:

- `SceneList` output from `scene_splitter`
- `ScoredSceneList` output from `scoring` (within a given video)
- Scene data reconstructed from database on resume

**Database query:**

```sql
SELECT * FROM scenes
WHERE video_id = ?
ORDER BY start_time ASC
```

### 10.2 Scored Scene Ordering

**Sort key:** `composite_score DESC, start_time ASC`

Scenes ranked by composite score use descending score as the primary sort key. Ties are broken by ascending start time (earlier scenes rank higher). This order is used by:

- `clip_builder` for nucleus selection
- Any display or reporting of "top scenes"

**The tiebreaker is critical.** Without it, two scenes with identical composite scores could swap positions between runs if the sort is unstable. The `start_time ASC` tiebreaker guarantees stable ordering.

### 10.3 Clip Processing Order

**Sort key:** `start_time ASC`

Clips are processed in chronological order of their `start_time` (the start time of their first constituent scene). This order is used by:

- The per-clip processing loop in `per_clip_processing`
- Hook template rotation (clip index determines template selection)
- Storage writes

**Why chronological, not by score:**

- Template rotation depends on a deterministic clip index. Chronological order provides this.
- Score-based ordering is used for _scheduling_ (best clips publish first) but not for _processing_.
- Chronological processing creates predictable filesystem ordering.

### 10.4 Scheduling Order

**Sort key:** `composite_score DESC, start_time ASC`

Clips are scheduled for publishing in descending score order. Highest-quality clips publish first.

```sql
UPDATE clips SET scheduled_at = ?, status = 'scheduled'
WHERE clip_id IN (
    SELECT clip_id FROM clips
    WHERE video_id = ? AND status = 'queued'
    ORDER BY composite_score DESC, start_time ASC
)
```

### 10.5 Ordering Invariant

```
For any two clips A and B from the same video:
  IF A.composite_score > B.composite_score:
    A is scheduled before B (publishes first)
    A may be processed before or after B (chronological, not score-based)
  IF A.composite_score == B.composite_score:
    The clip with earlier start_time is scheduled first
    The clip with earlier start_time is processed first
```

No exception. No randomization. No operator override (unless manual DB edit).

---

## 11. Concurrency Model

### 11.1 Single-Process Execution

The orchestrator executes as a single Python process with a single thread of control for all pipeline stages. There is no threading, no multiprocessing, no asyncio event loop.

**Rationale:**

- Video processing (FFmpeg) is already I/O and CPU bound — additional Python-level parallelism adds overhead without benefit
- SQLite's single-writer constraint makes concurrent DB writes problematic
- Deterministic execution requires predictable ordering — concurrency introduces non-determinism in execution order
- Debugging a single-threaded pipeline is trivially straightforward

### 11.2 FFmpeg Parallelism

FFmpeg itself uses internal threading for video encoding/decoding. This is managed by FFmpeg, not by the orchestrator. The orchestrator invokes FFmpeg as a subprocess and waits for completion.

```
Orchestrator thread:  [invoke FFmpeg] ──wait──> [FFmpeg returns] → [continue]
FFmpeg subprocess:    [internal multi-threading for encoding]
```

The orchestrator does NOT launch multiple FFmpeg processes simultaneously.

### 11.3 No Shared Mutable State

Within the pipeline execution:

- No global variables are modified after initialization
- Configuration is loaded once and treated as immutable
- DTOs are created by one module and consumed by another — they are not modified in transit
- The database is the only mutable state, accessed through explicit transactions

### 11.4 Optional Future Parallelism

The architecture permits (but does not require) the following parallelism in future versions:

| Parallelism Point           | Description                                                                          | Current    | Future (Optional)                                   |
| --------------------------- | ------------------------------------------------------------------------------------ | ---------- | --------------------------------------------------- |
| Independent analysis stages | `transcription`, `face_detection`, `audio_analysis` have no dependency on each other | Sequential | Could run in parallel (ProcessPoolExecutor)         |
| Independent per-clip stages | `thumbnail` and `metadata` have no dependency on each other                          | Sequential | Could run in parallel within a clip                 |
| Multiple clip processing    | Clip N and Clip N+1 are independent after `clip_builder`                             | Sequential | Could process in parallel (with per-clip isolation) |

**These are explicitly NOT implemented in v1.** They are documented here as permitted future optimizations that would not violate orchestrator invariants, provided each parallel unit has its own isolated DTO context and database transaction.

---

## 12. Orchestrator Invariants

The following invariants are **non-negotiable runtime guarantees**. They must hold true for every pipeline execution. Violation of any invariant is a critical bug that must be fixed immediately.

### Invariant 1: No Stage Runs Twice

```
For any pipeline run R and any stage S:
  S executes AT MOST once during R.

Enforcement:
  - Pipeline-level checkpoint prevents re-running completed stages on resume
  - There is no "re-run stage X" capability
  - The --force flag deletes all state and starts fresh (a new run, not a re-run of stages)
```

### Invariant 2: No Clip Processed Twice

```
For any pipeline run R and any clip C:
  C's sub-stages execute AT MOST once during R.

Enforcement:
  - Before processing clip C, check clips table: if C exists with status ∉ {'failed'} → skip
  - Clip-level checkout is binary: fully processed or not at all
  - On resume, completed clips are skipped by clip_id lookup
```

### Invariant 3: No Partial Writes

```
For any file F written by any module:
  F is either fully written and valid, or F does not exist.
  There is never a partially-written F on disk.

Enforcement:
  - All file writes use atomic rename: write to F.tmp, then rename F.tmp → F
  - If the process crashes between write and rename, only F.tmp exists
  - F.tmp files are cleaned up on next pipeline start
  - If F already exists, it is not overwritten (skip)
```

### Invariant 4: All Outputs Validated Before Checkpoint

```
For any stage S that produces output O:
  The checkpoint for S is written ONLY AFTER:
    1. O passes postcondition validation
    2. For file-producing stages: files verified to exist on disk
    3. For database-producing stages: rows verified to be committed

Enforcement:
  - Postcondition check is mandatory (Section 3.3)
  - Checkpoint write is the LAST operation in the stage (Section 4.1)
  - If postcondition fails → stage failure handler → no checkpoint written
```

### Invariant 5: Stage Order is Immutable

```
For any pipeline run R:
  Stages execute in exactly the order defined in STAGE_ORDER.
  No stage is skipped. No stage is reordered.

Enforcement:
  - STAGE_ORDER is a constant tuple defined in the orchestrator
  - The execution loop iterates STAGE_ORDER by index
  - Resume computes the resume index from STAGE_ORDER
  - There is no configuration option to change stage order
```

### Invariant 6: Database and Filesystem are Consistent

```
For any clip C with status ∈ {'queued', 'scheduled', 'published'}:
  The following files MUST exist:
    output/{video_id}/clips/{clip_id}/final.mp4
    output/{video_id}/clips/{clip_id}/thumbnail.jpg
    output/{video_id}/clips/{clip_id}/metadata.json

Enforcement:
  - Post-storage verification checks all three files
  - If any file missing → clip status set to 'failed'
  - Orphaned file cleanup removes files without DB rows
  - DB rows without files trigger failure status
```

### Invariant 7: Forward-Only State Transitions

```
For pipeline_runs.status:
  started → analyzing → building → completed | partial | failed
  No backward transitions. Ever.

For clips.status:
  generated → queued → scheduled → published | failed
  No backward transitions (except manual retry: failed → scheduled, max 3×).

Enforcement:
  - UPDATE statements include WHERE clause checking current status
  - Transition function validates: new_status is a valid successor of current_status
  - Invalid transitions raise an exception (treated as a bug)
```

### Invariant 8: Deterministic Clip Identity

```
For any video V processed with config C:
  clip_id = SHA256(video_id + str(start_ms) + str(end_ms))[:16]
  This value is identical on every run, every machine, every Python version.

Enforcement:
  - clip_id computation uses only deterministic inputs (video_id, start_ms, end_ms)
  - No randomness, no timestamps, no UUIDs in clip_id
  - run_id uses UUID4 (non-deterministic) but is NOT used for content identity
```

---

## 13. Failure Recovery Scenarios

This section defines the exact system behavior for specific crash scenarios. Each scenario states the crash point, the resulting state, and the recovery behavior on next invocation.

### Scenario 1: Crash During Transcription

**Crash point:** Process killed while faster-whisper is processing audio.

**State after crash:**

| Component            | State                                                             |
| -------------------- | ----------------------------------------------------------------- |
| `pipeline_runs`      | `status = 'analyzing'`, `last_completed_stage = 'scene_splitter'` |
| `videos` table       | Row exists with valid `video_id`                                  |
| `scenes` table       | All scene rows exist (scene_splitter completed)                   |
| Temporary audio file | May exist in temp directory (WAV extracted from video)            |
| Transcript file      | Does NOT exist (transcription did not complete)                   |

**Recovery on next run:**

```
1. Compute video_id → matches existing run
2. Load pipeline_runs → last_completed_stage = 'scene_splitter'
3. Resume from 'transcription' (index 2)
4. Reconstruct IngestionResult from videos table
5. Reconstruct SceneList from scenes table
6. Execute transcription from scratch (no partial transcript caching)
7. Clean up any orphaned temp audio files
8. Continue from face_detection onward
```

### Scenario 2: Crash During Rendering (Clip 8 of 12)

**Crash point:** FFmpeg process killed during rendering of clip 8.

**State after crash:**

| Component       | State                                                                                                                                                             |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline_runs` | `status = 'building'`, `last_completed_stage = 'clip_builder'`                                                                                                    |
| `clips` table   | Clips 1–7: `status = 'queued'`. Clips 8–12: no rows.                                                                                                              |
| Filesystem      | Clips 1–7: complete (final.mp4, thumbnail.jpg, metadata.json). Clip 8: may have `composite.mp4`, may have `final.mp4.tmp` (partial render). Clips 9–12: no files. |

**Recovery on next run:**

```
1. Compute video_id → matches existing run
2. Load pipeline_runs → last_completed_stage = 'clip_builder'
3. Resume from 'per_clip_processing' (index 7)
4. Reconstruct all upstream DTOs from database
5. Rebuild ClipList from clips already computed (stored in DB or recomputed deterministically)
6. Enter per_clip_processing loop:
   → Clips 1–7: exist in clips table with status 'queued' → SKIP
   → Clip 8: no row in clips table → clean up .tmp and partial files → PROCESS from scratch
   → Clips 9–12: no row → PROCESS
7. After all clips → checkpoint 'per_clip_processing'
8. Continue to storage, scheduler
```

### Scenario 3: Crash During Database Write (Storage Stage)

**Crash point:** Process killed between writing files to disk and committing the `clips` table INSERT.

**State after crash:**

| Component     | State                                                    |
| ------------- | -------------------------------------------------------- |
| `clips` table | Row was NOT committed (transaction rolled back on crash) |
| Filesystem    | Files MAY exist (written before DB commit)               |

**Recovery on next run:**

```
1. Resume normally (per_clip_processing resumes)
2. For the affected clip:
   → No row in clips table → clip is treated as "not processed"
   → Orphaned files in output/{video_id}/clips/{clip_id}/ are detected during cleanup
   → Orphaned files are deleted
   → Clip is reprocessed from scratch
3. The reprocessed clip produces identical files (deterministic pipeline)
4. This time, both file writes AND DB commit succeed atomically
```

### Scenario 4: Crash During Scoring

**Crash point:** Exception during composite score computation.

**State after crash:**

| Component       | State                                                                                                                              |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline_runs` | `status = 'analyzing'`, `last_completed_stage = 'audio_analysis'`                                                                  |
| `scenes` table  | All scenes have `transcript_text`, `face_visible_ratio`, `audio_energy_score`, but NO `composite_score` (scoring did not complete) |

**Recovery on next run:**

```
1. Resume from 'scoring' (index 5)
2. Reconstruct SceneList, Transcript, FaceDetectionResult, audio data from database
3. Execute scoring from scratch:
   → Scoring reads per-scene data from input DTOs (reconstructed from DB)
   → Computes all five factors + composite score
   → Writes scores to scenes table
4. Continue from clip_builder onward
```

### Scenario 5: Disk Space Exhaustion During Pipeline

**Crash point:** Disk space drops below 500MB during rendering.

**State after crash:**

| Component                | State                                                     |
| ------------------------ | --------------------------------------------------------- |
| `pipeline_runs`          | `status = 'failed'`, `error_log = "Disk space exhausted"` |
| Partially rendered clips | Some clips complete, current clip likely has .tmp files   |

**Recovery on next run:**

```
1. Operator frees disk space
2. Re-runs pipeline with same input
3. Pipeline detects existing run with status 'failed'
4. Resumes from stage after last_completed_stage
5. Orphaned .tmp files are cleaned up
6. Completed clips are skipped (exist in clips table)
7. Processing continues for remaining clips
```

### Scenario 6: Crash During Pipeline Run Creation (Step 4)

**Crash point:** Process killed between generating `run_id` and committing the INSERT to `pipeline_runs`.

**State after crash:**

| Component       | State                                                      |
| --------------- | ---------------------------------------------------------- |
| `pipeline_runs` | No row exists for this video_id (INSERT was not committed) |

**Recovery on next run:**

```
1. Compute video_id
2. Query pipeline_runs → no row found
3. Create new pipeline run (new run_id)
4. Start from stage 0 (ingestion)
5. This is identical to a first-time run — no state to recover
```

---

## 14. Observability Hooks

### 14.1 Stage-Level Logging

The orchestrator emits structured log entries at the start and end of every stage. These are the **minimum required** log entries — individual modules may add their own.

**Stage start log:**

```json
{
  "run_id": "run-a1b2c3d4",
  "video_id": "f8e2a9c1b3d7",
  "stage": "scene_splitter",
  "event": "stage_started",
  "timestamp": "2026-03-24T10:01:15.000Z"
}
```

**Stage completion log:**

```json
{
  "run_id": "run-a1b2c3d4",
  "video_id": "f8e2a9c1b3d7",
  "stage": "scene_splitter",
  "event": "stage_completed",
  "status": "success",
  "duration_ms": 182400,
  "timestamp": "2026-03-24T10:04:17.400Z"
}
```

**Stage failure log:**

```json
{
  "run_id": "run-a1b2c3d4",
  "video_id": "f8e2a9c1b3d7",
  "stage": "scene_splitter",
  "event": "stage_failed",
  "status": "failed",
  "error": "PySceneDetect returned no scenes",
  "error_type": "NoScenesDetected",
  "will_retry": true,
  "retry_attempt": 1,
  "duration_ms": 95200,
  "timestamp": "2026-03-24T10:02:47.200Z"
}
```

### 14.2 Clip-Level Logging

Within `per_clip_processing`, the orchestrator logs per-clip start, completion, and skip events:

**Clip processing start:**

```json
{
  "run_id": "run-a1b2c3d4",
  "video_id": "f8e2a9c1b3d7",
  "clip_id": "c4d5e6f7a8b9",
  "clip_index": 3,
  "total_clips": 12,
  "event": "clip_processing_started",
  "timestamp": "2026-03-24T10:12:30.000Z"
}
```

**Clip skipped (already processed):**

```json
{
  "run_id": "run-a1b2c3d4",
  "video_id": "f8e2a9c1b3d7",
  "clip_id": "c4d5e6f7a8b9",
  "clip_index": 3,
  "event": "clip_skipped",
  "reason": "already_processed",
  "existing_status": "queued",
  "timestamp": "2026-03-24T10:12:30.010Z"
}
```

**Clip processing completed:**

```json
{
  "run_id": "run-a1b2c3d4",
  "video_id": "f8e2a9c1b3d7",
  "clip_id": "c4d5e6f7a8b9",
  "clip_index": 3,
  "total_clips": 12,
  "event": "clip_processing_completed",
  "status": "success",
  "duration_ms": 87500,
  "sub_stages": {
    "hook_generator": { "status": "success", "duration_ms": 45 },
    "tts": { "status": "success", "duration_ms": 3200 },
    "subtitle": { "status": "success", "duration_ms": 120 },
    "compositor": { "status": "success", "duration_ms": 42100 },
    "renderer": { "status": "success", "duration_ms": 38900 },
    "thumbnail": { "status": "success", "duration_ms": 2800 },
    "metadata": { "status": "success", "duration_ms": 35 }
  },
  "timestamp": "2026-03-24T10:13:57.500Z"
}
```

### 14.3 Pipeline-Level Metrics

At pipeline completion (or abort), the orchestrator emits a summary metric event:

```json
{
  "run_id": "run-a1b2c3d4",
  "video_id": "f8e2a9c1b3d7",
  "event": "pipeline_summary",
  "status": "completed",
  "total_duration_ms": 1245000,
  "stages": {
    "ingestion": { "duration_ms": 8500, "status": "success" },
    "scene_splitter": { "duration_ms": 182400, "status": "success" },
    "transcription": { "duration_ms": 420000, "status": "success" },
    "face_detection": { "duration_ms": 240000, "status": "success" },
    "audio_analysis": { "duration_ms": 15000, "status": "success" },
    "scoring": { "duration_ms": 3200, "status": "success" },
    "clip_builder": { "duration_ms": 800, "status": "success" },
    "per_clip_processing": { "duration_ms": 960000, "status": "success" },
    "storage": { "duration_ms": 4500, "status": "success" },
    "scheduler": { "duration_ms": 600, "status": "success" }
  },
  "clips": {
    "total_built": 12,
    "processed": 12,
    "skipped": 0,
    "failed": 0,
    "scheduled": 12
  },
  "scores": {
    "min_composite": 0.32,
    "max_composite": 0.89,
    "avg_composite": 0.64
  },
  "disk_usage_bytes": 1932000000,
  "timestamp": "2026-03-24T10:21:45.000Z"
}
```

### 14.4 Pipeline Status Tracking

The `pipeline_runs` table provides persistent status tracking:

```sql
SELECT run_id, video_id, status, last_completed_stage,
       clips_generated, started_at, completed_at, error_log
FROM pipeline_runs
WHERE video_id = ?
ORDER BY started_at DESC;
```

This query answers:

- Has this video been processed?
- How many clips were generated?
- Did the pipeline complete or fail?
- Where did it stop? (for debugging)
- What error occurred? (for diagnosis)

### 14.5 Human-Readable Console Output

In addition to structured JSON logs, the orchestrator prints human-readable progress to stdout:

```
[1/10] Ingesting video... done (1h 12m, 1920x1080)
[2/10] Splitting scenes... done (187 scenes)
[3/10] Transcribing audio... done (4,231 words)
[4/10] Detecting faces... done (avg visibility: 0.72)
[5/10] Analyzing audio energy... done
[6/10] Scoring scenes... done (avg: 0.64, range: 0.32–0.89)
[7/10] Building clips... done (12 clips selected)
[8/10] Processing clips:
  [1/12] clip a3f2b1c9... rendered (42s, 78MB) ✓
  [2/12] clip b4e3c2d0... rendered (38s, 65MB) ✓
  ...
  [12/12] clip f8a1d3e2... rendered (51s, 92MB) ✓
[9/10] Storing clips... done (12 stored)
[10/10] Scheduling... done (12 Shorts, next 12 days)

Pipeline completed in 20m 45s.
  Clips: 12 generated, 0 failed
  Next publish: 2026-03-25 10:00 UTC
  Review: output/a3f2b1c9/clips/
```

---

## 15. Final Execution Summary

### 15.1 Why This Orchestrator Guarantees Determinism

The orchestrator is deterministic because:

1. **Fixed stage order** — `STAGE_ORDER` is a constant. No configuration, no runtime decision changes the order.
2. **Fixed clip order** — Clips are processed in chronological order (`start_time ASC`). Tiebreaker is deterministic.
3. **No randomness** — No `random.random()`, no `uuid4()` for content identity, no `datetime.now()` as a decision input.
4. **Content-addressable identity** — All IDs (video_id, scene_id, clip_id) are derived from content, not from runtime state.
5. **Deterministic template selection** — Hook templates are selected by `hash(clip_id) % pool_size`, which is deterministic.
6. **Deterministic scoring** — All scoring factors are computed from data, not from runtime context. Weights are config-driven.
7. **No external dependencies during processing** — No network calls, no API responses, no external clock reads (except for logging timestamps, which do not affect processing logic).

**Determinism guarantee:** Given identical input file + identical `config.yaml` → the orchestrator produces identical clips, identical scores, identical hook text, identical thumbnails, identical metadata, in the same order, with the same file contents, byte-for-byte (modulo Edge TTS variance, which is cached after first run).

### 15.2 Why This Orchestrator Guarantees Reliability

The orchestrator is reliable because:

1. **Two-level checkpointing** — Pipeline-level checkpoint (which stage) + clip-level checkpoint (which clips). No work is ever lost.
2. **Atomic writes** — All file operations use `.tmp` → rename. All database operations use transactions. No partial state.
3. **Crash recovery** — Any crash at any point is recoverable by re-running the same command. The orchestrator detects the incomplete run and resumes.
4. **Failure isolation** — A single clip failure does not kill the pipeline. Failed clips are skipped, and the rest continue.
5. **Threshold protection** — If too many clips fail (> 50%), the pipeline aborts rather than producing an unreliable batch.
6. **No silent failures** — Every exception produces a structured log with run_id, video_id, clip_id, stage, error, and action taken.

### 15.3 Why This Orchestrator Guarantees Resumability

The orchestrator is resumable because:

1. **Checkpoint after every stage** — `last_completed_stage` always reflects the latest fully completed stage.
2. **DTO reconstruction from database** — On resume, all upstream DTOs are rebuilt from persisted database state. No in-memory cache dependency.
3. **Clip skip logic** — On resume within `per_clip_processing`, completed clips are detected by `clip_id` in the `clips` table and skipped.
4. **Orphan cleanup** — On startup, orphaned `.tmp` files and directories without DB rows are cleaned. The resume starts from a consistent state.
5. **No destructive re-execution** — Resuming never overwrites existing files or database rows. `INSERT ... ON CONFLICT DO NOTHING` and file existence checks prevent duplication.

**Resumability guarantee:** The pipeline can be killed at any point (SIGKILL, power loss, OOM) and resumed by running the same command. It will pick up exactly where it left off, skip all completed work, reprocess only what was interrupted, and produce a correct final result.

---

_End of orchestrator specification._
