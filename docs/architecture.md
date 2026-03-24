# Shorts Factory — Architecture Document

> **Version:** 1.0  
> **Date:** 2026-03-23  
> **Status:** Design Phase  
> **Author:** System Architect

---

## Table of Contents

- [1. System Goal](#1-system-goal)
- [2. Core Design Principles](#2-core-design-principles)
- [3. Architecture Style](#3-architecture-style)
- [4. High-Level Pipeline](#4-high-level-pipeline)
- [5. Module Breakdown](#5-module-breakdown)
- [6. Face Detection & Composition Design](#6-face-detection--composition-design)
- [7. Clip Generation Strategy](#7-clip-generation-strategy)
- [8. Scoring System Design](#8-scoring-system-design)
- [9. Thumbnail Strategy](#9-thumbnail-strategy)
- [10. Metadata Strategy](#10-metadata-strategy)
- [11. Data & Storage Design](#11-data--storage-design)
- [12. Scheduling & Publishing](#12-scheduling--publishing)
- [13. Pipeline Behavior](#13-pipeline-behavior)
- [14. Performance Targets](#14-performance-targets)
- [15. Failure Modes & Safeguards](#15-failure-modes--safeguards)
- [16. Parallel Development Strategy](#16-parallel-development-strategy)
- [17. Explicit Non-Goals](#17-explicit-non-goals)
- [18. Final System Definition](#18-final-system-definition)

---

## 1. System Goal

The Shorts Factory is an autonomous, local-only content production pipeline that transforms long-form gameplay recordings into fully packaged YouTube Shorts — ready for scheduled publishing with zero cloud cost.

### Input

- One long-form gameplay video (30–120 minutes)
- Containing both screen capture (gameplay) and face camera (child reaction footage)

### Output (per run)

- 10–15 YouTube Shorts, each containing:
  - Vertical video (1080x1920, 30–60 seconds, H.264 MP4)
  - Composite layout: gameplay (top 65%) + face cam (bottom 35%)
  - TTS narration audio track
  - Burned-in animated subtitles
  - Thumbnail image (JPEG, 1280x720)
  - Title (40–60 characters, emoji-enhanced)
  - Description (SEO-optimized, hashtag-rich)
  - Tag set (static + dynamic)
  - Queue entry with scheduled publish timestamp

### System Metaphor

This is a **content factory**, not a video editor. Raw footage enters one end; scheduled, publish-ready Shorts exit the other. The human operator's role is reduced to a 5-minute daily review of thumbnails and titles.

---

## 2. Core Design Principles

Every design decision in this system is evaluated against these eight constraints. They are non-negotiable.

### 2.1 Deterministic Pipeline

Same input video, same configuration → identical output Shorts. No randomness, no network-dependent behavior, no non-deterministic model inference. Every scoring function, every template, every selection algorithm produces repeatable results. This enables debugging, regression testing, and trust in automated output.

### 2.2 Idempotent Execution

Running the pipeline twice on the same input produces no duplicates and no corruption. Every clip is assigned a content-addressable unique ID derived from source video hash + time range. If a clip already exists in storage, it is skipped — not overwritten, not duplicated.

### 2.3 Modular Monolith

All modules live in a single process, a single repository, a single deployment unit. Modules communicate through well-defined in-memory DTOs (Data Transfer Objects), not network calls. This eliminates serialization overhead, network failure modes, and operational complexity — while preserving clean separation of concerns.

### 2.4 Zero Cost (Local Execution Only)

No paid APIs. No cloud services. No GPU rental. Every dependency must run on a consumer-grade machine (CPU-first, GPU-optional). All models are open-source and run locally: faster-whisper for transcription, MediaPipe for face detection, Edge TTS for speech synthesis.

### 2.5 Batch Processing

The system processes an entire video in one batch run. There is no streaming, no real-time processing, no event-driven architecture. Batch mode simplifies error handling, enables global optimization (scoring across all scenes before clip selection), and matches the actual usage pattern: record once, process once, publish over days.

### 2.6 Minimal Dependencies

Every external library must justify its inclusion. Prefer FFmpeg (already required) over additional video libraries. Prefer PIL/Pillow (already required for thumbnails) over heavier image frameworks. The dependency tree must remain shallow and auditable.

### 2.7 High Cohesion, Low Coupling

Each module owns exactly one responsibility. The scene splitter knows nothing about thumbnails. The metadata generator knows nothing about face detection. Modules interact only through their declared input/output contracts. Internal implementation details are never leaked.

### 2.8 Human-in-the-Loop (Optional)

The pipeline runs fully autonomously. However, an optional review step exists between rendering and publishing. A human can inspect thumbnails, approve or reject titles, and reorder the queue. This review layer never blocks the pipeline — it only gates the publish step.

---

## 3. Architecture Style

### 3.1 Why Modular Monolith

A modular monolith provides the organizational benefits of service-oriented architecture without the operational tax of distributed systems.

**Benefits for this system:**

- **Single process** — No inter-process communication overhead. Video frames, audio buffers, and metadata flow through memory, not serialized over HTTP.
- **Atomic transactions** — A single SQLite database handles all state. No distributed transaction coordination.
- **Simple deployment** — One Python environment, one entry point (`run_pipeline.py`), one machine.
- **Debuggable** — A single stack trace captures the full execution path from ingestion to rendering.
- **Testable** — Each module is unit-testable in isolation via its DTO contract. Integration tests run the full pipeline in-process.

**Module boundaries are enforced through:**

- Separate Python packages per module
- Explicit DTO classes at module boundaries
- No direct imports between module internals (only public contracts)
- A central orchestrator that wires modules together

**Database ownership rule:**

- The orchestrator is the **only** component that writes to the database (via `database/adapter.py`)
- Modules are **pure computation** — they accept DTOs, return DTOs, and perform no I/O on shared state
- Modules MUST NOT import `sqlite3`, `psycopg2`, or any database driver
- Modules MUST NOT contain SQL strings or execute queries
- All dependencies between modules are **explicit in their DTO contracts** — no hidden coupling through filesystem, database, or global state

### 3.2 Why NOT Microservices

Microservices solve problems this system does not have:

- **No team scaling problem** — This is a single-developer or small-team project. Microservices exist to allow independent team deployment; that benefit is irrelevant here.
- **No independent scaling requirement** — All pipeline stages process the same video. There is no "transcription gets 10x more traffic than rendering" scenario.
- **Operational overhead is fatal** — Docker orchestration, service discovery, network retries, distributed tracing, log aggregation — each adds complexity that directly contradicts the $0-cost, simplicity-first mandate.
- **Data locality matters** — Video processing moves gigabytes of frame data between stages. Network serialization of video frames between services would be orders of magnitude slower than in-memory passing.

### 3.3 Why NOT Agent-Based Runtime Systems

Agent-based systems (LLM orchestrators, autonomous planners) introduce:

- **Non-determinism** — Agent decisions vary across runs, violating the deterministic pipeline principle.
- **Cost** — Most agent frameworks rely on paid LLM APIs.
- **Latency** — API round-trips add seconds per decision point.
- **Fragility** — Prompt drift, model version changes, and rate limits create unpredictable failure modes.

Every decision in this pipeline (which clips to select, what title to generate, which frame to thumbnail) is rule-based and template-driven. The domain is constrained enough that deterministic heuristics outperform general-purpose reasoning.

### 3.4 Why Deterministic Pipeline is Critical

For a content production system targeting a children's audience:

- **Safety** — Deterministic output can be audited. Non-deterministic systems may produce inappropriate content on edge cases that only appear in certain runs.
- **Reproducibility** — If a Short performs well, the operator must understand _why_ — which scoring factors led to its selection. Deterministic scoring enables this analysis.
- **Debugging** — When a clip is bad, the operator traces the scoring chain backward. Deterministic pipelines make this trace reliable.
- **Trust** — A parent publishing content for a child cannot tolerate "sometimes it works, sometimes it doesn't."

---

## 4. High-Level Pipeline

### 4.1 Pipeline Flow Diagram

```
                    ┌──────────────────┐
                    │   Input Video    │
                    │  (30–120 min)    │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │    Ingestion     │
                    │  (validate,      │
                    │   fingerprint)   │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Scene Splitter  │
                    │  (PySceneDetect  │
                    │   + FFmpeg)      │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Transcription   │
                    │ (faster-whisper) │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Face Detection  │
                    │  (MediaPipe)     │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Scoring Engine  │
                    │  (rule-based     │
                    │   multi-signal)  │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Clip Builder    │
                    │  (merge scenes   │
                    │   → 30–60s)      │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Hook Generator  │
                    │  (template-based │
                    │   narration)     │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  TTS Engine      │
                    │  (Edge TTS)      │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Subtitle Gen    │
                    │  (word-level     │
                    │   alignment)     │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Compositor      │
                    │  (face+gameplay  │
                    │   9:16 layout)   │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Final Renderer  │
                    │  (merge all      │
                    │   layers → MP4)  │
                    └────────┬─────────┘
                             │
                             ▼
              ┌──────────────┴──────────────┐
              │                             │
              ▼                             ▼
     ┌─────────────────┐          ┌─────────────────┐
     │   Thumbnail      │          │  Metadata Gen   │
     │   Generator      │          │  (title, desc,  │
     │   (frame+text)   │          │   tags)         │
     └────────┬────────┘          └────────┬────────┘
              │                             │
              └──────────────┬──────────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Storage Layer   │
                    │  (SQLite + FS)   │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Scheduler       │
                    │  (assign dates)  │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Publisher       │
                    │  (YouTube API    │
                    │   via cron)      │
                    └──────────────────┘
```

### 4.2 Stage Summary Table

| Stage               | Purpose                             | Input                                   | Output                                         | Constraint                              |
| ------------------- | ----------------------------------- | --------------------------------------- | ---------------------------------------------- | --------------------------------------- |
| Ingestion           | Validate and fingerprint source     | Raw video file                          | Validated video + SHA-256 hash                 | Must reject corrupt/unsupported formats |
| Scene Splitter      | Detect scene boundaries             | Video stream                            | List of scene segments (start, end, duration)  | min 3s, max 20s per scene               |
| Transcription       | Extract speech to text              | Audio stream                            | Word-level timestamped transcript              | Local model only, no API                |
| Face Detection      | Locate face in each frame           | Video frames (sampled)                  | Per-scene face bounding box + visibility ratio | Interval sampling, not every frame      |
| Scoring Engine      | Rank scenes by engagement potential | Transcript + audio + face + motion data | Scored scene list                              | Deterministic, rule-based only          |
| Clip Builder        | Merge scenes into 30–60s clips      | Scored scenes                           | Clip definitions (scene groups)                | Hard floor 30s, hard ceiling 60s        |
| Hook Generator      | Create narration text               | Clip transcript + context               | Hook string + story string                     | Template-based, max 15 words for hook   |
| TTS Engine          | Synthesize narration audio          | Hook + story text                       | Audio file (WAV/MP3)                           | Edge TTS, local offline mode            |
| Subtitle Generator  | Create timed subtitle track         | Transcript + clip boundaries            | SRT/ASS subtitle file                          | Word-level timing, large font           |
| Compositor          | Combine face + gameplay into 9:16   | Face track + gameplay track             | Composite video stream                         | 65/35 split, face centered              |
| Final Renderer      | Merge all layers into output        | Composite + TTS + subtitles             | MP4 file (1080x1920, H.264)                    | 30–60s, consistent quality              |
| Thumbnail Generator | Create click-worthy thumbnail       | Best frame + hook text                  | JPEG (1280x720)                                | Face visible, 2–3 word overlay          |
| Metadata Generator  | Produce title, description, tags    | Hook + transcript context               | Metadata JSON                                  | 40–60 char title, emoji rules           |
| Storage             | Persist clips and metadata          | All outputs                             | SQLite records + filesystem                    | Content-addressable IDs                 |
| Scheduler           | Assign publish dates                | Clip queue                              | Updated queue with timestamps                  | One Short per day                       |
| Publisher           | Upload to YouTube                   | Queued clip + metadata                  | Published status                               | Retry on failure, idempotent            |

---

## 5. Module Breakdown

### 5.1 Ingestion Module

**Responsibility:** Accept a raw video file, validate its format and integrity, compute a content-addressable fingerprint, and extract basic metadata (duration, resolution, codec).

**Input Contract:**

- File path to video (MP4, MKV, or AVI)
- Minimum duration: 30 minutes
- Maximum duration: 120 minutes
- Must contain both video and audio streams

**Output Contract:**

- `IngestionResult` DTO containing:
  - `video_id`: SHA-256 hash of first 10MB + file size (deterministic fingerprint)
  - `video_path`: validated absolute path
  - `duration_seconds`: total duration
  - `resolution`: (width, height)
  - `has_audio`: boolean
  - `codec_info`: video and audio codec names

**Failure Modes:**

- File not found or unreadable → abort with clear error
- Unsupported format → reject with supported format list
- No audio stream → abort (transcription requires audio)
- Duration out of range → reject with reason
- Corrupted file (FFprobe fails) → abort

**Constraints:**

- Must not modify the source file
- Fingerprint computation must be fast (read only first 10MB, not entire file)
- Must be the first module invoked — all downstream modules depend on the `video_id`

---

### 5.2 Scene Splitter Module

**Responsibility:** Detect visual scene boundaries in the source video, producing a list of temporal segments that represent coherent visual scenes.

**Input Contract:**

- `IngestionResult` (validated video path)

**Output Contract:**

- `SceneList` DTO: ordered list of `SceneSegment` objects, each containing:
  - `scene_id`: deterministic ID (`{video_id}_{start_ms}_{end_ms}`)
  - `start_time`: start timestamp (milliseconds)
  - `end_time`: end timestamp (milliseconds)
  - `duration`: computed duration (seconds)

**Failure Modes:**

- No scenes detected (static video) → treat entire video as one scene
- Too many micro-scenes (flickering content) → merge adjacent scenes below 3s threshold
- FFmpeg extraction failure → retry once, then abort pipeline for this video

**Constraints:**

- Minimum scene duration: 3 seconds (shorter scenes are merged with the previous scene)
- Maximum scene duration: 20 seconds (longer scenes are force-split at the midpoint)
- Detection method: content-aware (PySceneDetect adaptive threshold), not fixed-interval
- Must produce identical scene boundaries on repeated runs (deterministic threshold)

---

### 5.3 Transcription Module

**Responsibility:** Extract speech from the video audio track and produce a word-level timestamped transcript.

**Input Contract:**

- `IngestionResult` (video path for audio extraction)
- `SceneList` (for scene-aligned segmentation, optional optimization)

**Output Contract:**

- `Transcript` DTO containing:
  - `segments`: list of `TranscriptSegment`, each with:
    - `text`: transcribed text
    - `start_time`: start timestamp (milliseconds)
    - `end_time`: end timestamp (milliseconds)
    - `words`: list of `Word` objects with individual timestamps
    - `confidence`: per-segment confidence score

**Failure Modes:**

- No speech detected → return empty transcript (not an error — gameplay may have music only)
- Model loading failure → abort with dependency check message
- Audio extraction failure → abort pipeline
- Low confidence across entire transcript → flag for human review but do not block

**Constraints:**

- Model: faster-whisper (CTranslate2 backend), small or base model
- Language: English (hardcoded for this use case, configurable later)
- Must run on CPU (GPU optional for speed)
- Word-level timestamps are required (not just segment-level) — needed for subtitle alignment

---

### 5.4 Scoring Engine Module

**Responsibility:** Assign an engagement score to each scene based on multiple signals, producing a ranked list that drives clip building.

**Input Contract:**

- `SceneList` with durations
- `Transcript` with word-level data
- `FaceDetectionResult` with per-scene face visibility ratios
- Audio energy data (extracted via FFmpeg)

**Output Contract:**

- `ScoredSceneList` DTO: list of `ScoredScene` objects, each extending `SceneSegment` with:
  - `keyword_score`: float (0–1)
  - `audio_energy_score`: float (0–1)
  - `scene_activity_score`: float (0–1)
  - `face_presence_score`: float (0–1)
  - `sentence_density_score`: float (0–1)
  - `composite_score`: weighted float
  - `rank`: integer position

**Failure Modes:**

- Missing transcript → keyword and sentence scores default to 0
- Missing face data → face presence score defaults to 0
- All scenes score equally → fall back to temporal distribution (spread clips evenly)

**Constraints:**

- Scoring formula is deterministic and configurable via weights file
- Default weights: keyword × 3, audio_energy × 2, face_presence × 2, scene_activity × 1, sentence_density × 1
- No ML models — pure rule-based computation
- Score normalization: min-max within the current video (relative ranking)

---

### 5.5 Clip Builder Module

**Responsibility:** Merge consecutive high-scoring scenes into clips of 30–60 seconds, maximizing engagement signal density within the duration constraint.

**Input Contract:**

- `ScoredSceneList` (ranked scenes with durations)

**Output Contract:**

- `ClipList` DTO: list of `ClipDefinition` objects, each containing:
  - `clip_id`: deterministic ID (`{video_id}_clip_{index}`)
  - `scenes`: ordered list of constituent `ScoredScene` references
  - `start_time`: clip start (first scene start)
  - `end_time`: clip end (last scene end)
  - `total_duration`: sum of scene durations
  - `average_score`: mean composite score of constituent scenes

**Failure Modes:**

- Insufficient high-scoring scenes → lower threshold incrementally until minimum clip count (5) is reached
- Scene gaps create awkward transitions → only merge temporally adjacent scenes
- All clips exceed 60s → force-split at scene boundary nearest to 45s mark

**Constraints:**

- Hard minimum: 30 seconds (clips shorter than this are discarded)
- Hard maximum: 60 seconds (clips are split, not trimmed)
- Target: 10–15 clips per input video
- Scenes within a clip must be temporally contiguous (no jump-cut mashups)
- Selection priority: highest composite score first, then fill remaining slots chronologically

---

### 5.6 Face Detection Module

**Responsibility:** Detect and track the child's face position across video frames, producing per-scene face visibility metrics and bounding box coordinates for composition.

**Input Contract:**

- `IngestionResult` (video path)
- `SceneList` (scene boundaries for targeted extraction)

**Output Contract:**

- `FaceDetectionResult` DTO: list of `SceneFaceData` objects, each containing:
  - `scene_id`: reference to source scene
  - `face_visible_ratio`: float (0–1), fraction of sampled frames where face is detected
  - `bounding_boxes`: list of `FaceBBox` (x, y, width, height) per sampled frame
  - `average_bbox`: smoothed center bounding box for the scene
  - `confidence`: average detection confidence

**Failure Modes:**

- No face detected in any frame → flag scene as "faceless," trigger fallback layout
- Multiple faces detected → select largest bounding box (primary subject heuristic)
- Face flickers in/out → apply temporal smoothing (ignore gaps shorter than 0.5s)
- MediaPipe fails to load → abort with dependency error

**Constraints:**

- Detection library: MediaPipe Face Detection (short-range model)
- Sampling rate: 2 frames per second (not every frame — performance constraint)
- Bounding box smoothing: Exponential Moving Average (EMA) with alpha = 0.3
- Must run on CPU
- Output bounding boxes are in normalized coordinates (0–1 range) for resolution independence

---

### 5.7 Compositor Module

**Responsibility:** Combine the face camera region and gameplay region into a single 9:16 vertical frame.

**Input Contract:**

- `ClipDefinition` (time range)
- `FaceDetectionResult` (bounding boxes for face region)
- Source video path

**Output Contract:**

- `CompositeStream` DTO containing:
  - `clip_id`: reference
  - `composite_video_path`: path to intermediate composite video (no audio yet)
  - `resolution`: 1080x1920
  - `layout_metadata`: actual split ratio, face crop coordinates

**Failure Modes:**

- Face not detected for this clip → use fallback layout (gameplay full-frame with zoom)
- Face at edge of frame → clamp crop to frame boundary, apply padding
- Source resolution too low → upscale with bicubic interpolation (flag quality warning)

**Constraints:**

- Layout: top 65% (1080x1248) = gameplay, bottom 35% (1080x672) = face
- Face region: crop around detected bounding box, apply 1.2× zoom, center horizontally
- Gameplay region: center-crop from source to 9:16 aspect ratio
- No letterboxing — both regions must fill their allocated space
- Frame rate: match source (typically 30fps)

---

### 5.8 Hook Generator Module

**Responsibility:** Generate a short narration script (hook + story) for each clip based on transcript content and clip context.

**Input Contract:**

- `ClipDefinition` (scene references)
- `Transcript` segment for the clip's time range

**Output Contract:**

- `HookResult` DTO containing:
  - `clip_id`: reference
  - `hook_text`: string (max 15 words, attention-grabbing opener)
  - `story_text`: string (1–2 sentences, narrative continuation)
  - `template_id`: which template was used (for debugging/rotation)

**Failure Modes:**

- No transcript text for this clip → use generic template from rotation pool
- Template exhaustion (all templates used in this batch) → reset and reuse with variation
- Generated text exceeds length constraint → truncate at sentence boundary

**Constraints:**

- Purely template-based, no LLM
- Template pool: minimum 30 templates, rotated to avoid repetition within a batch
- Templates are parameterized with extracted keywords from transcript
- Hook must be emotionally charged (question, exclamation, or revelation pattern)
- Language: English, child-friendly vocabulary only

---

### 5.9 TTS Module

**Responsibility:** Convert hook and story text into natural-sounding speech audio.

**Input Contract:**

- `HookResult` (hook_text + story_text)

**Output Contract:**

- `TTSResult` DTO containing:
  - `clip_id`: reference
  - `audio_path`: path to synthesized audio file (WAV)
  - `duration_seconds`: audio duration
  - `word_timestamps`: per-word timing for subtitle sync (if available from engine)

**Failure Modes:**

- Edge TTS offline/unavailable → fallback to pyttsx3 (lower quality, always available)
- Generated audio longer than clip → truncate story, keep hook intact
- Audio quality artifacts → flag for human review, do not block

**Constraints:**

- Primary engine: Edge TTS (Microsoft, free, no API key required)
- Voice: child-friendly, energetic female voice
- Audio format: 16-bit WAV, 44.1kHz
- Volume normalization: -14 LUFS (broadcast standard)
- TTS audio must not overpower gameplay audio — mixed at 70% gameplay / 30% narration in renderer

---

### 5.10 Subtitle Module

**Responsibility:** Generate timed subtitle tracks from transcript and TTS word timestamps.

**Input Contract:**

- `Transcript` (word-level timestamps for the clip's range)
- `TTSResult` (word timestamps for narration overlay, optional)
- `ClipDefinition` (time boundaries)

**Output Contract:**

- `SubtitleResult` DTO containing:
  - `clip_id`: reference
  - `subtitle_path`: path to ASS/SRT subtitle file
  - `word_count`: total words
  - `style_preset`: which visual style was applied

**Failure Modes:**

- No transcript words in clip range → generate subtitles from TTS text only
- Timing misalignment > 500ms → recompute alignment using audio peaks
- Subtitle file write failure → retry, then flag

**Constraints:**

- Format: ASS (Advanced SubStation Alpha) for styled rendering
- Style: large bold font (minimum 48pt equivalent), high-contrast outline, center-bottom positioning
- Word-level animation: highlight current word (karaoke-style) for engagement
- Maximum 2 lines visible simultaneously
- Safe area: subtitles must not overlap with the face region (bottom 35%)

---

### 5.11 Renderer Module

**Responsibility:** Combine composite video, TTS audio, original audio (attenuated), and subtitle track into the final deliverable MP4.

**Input Contract:**

- `CompositeStream` (video layer)
- `TTSResult` (narration audio)
- Original audio track (extracted from source for the clip's range)
- `SubtitleResult` (subtitle file)

**Output Contract:**

- `RenderedClip` DTO containing:
  - `clip_id`: reference
  - `video_path`: path to final MP4
  - `duration_seconds`: actual duration
  - `file_size_bytes`: output file size
  - `resolution`: confirmed 1080x1920
  - `codec`: H.264 profile

**Failure Modes:**

- FFmpeg encoding failure → retry with fallback settings (lower bitrate)
- Output duration mismatch > 1s from expected → flag as error, do not store
- File size exceeds 100MB → re-encode with constrained quality

**Constraints:**

- Resolution: 1080x1920 (9:16)
- Codec: H.264 High Profile, CRF 18–23
- Audio: AAC, 128kbps, stereo
- Audio mix: gameplay audio at 70% volume, TTS at 30%, ducking gameplay during narration
- Duration: 30–60 seconds (enforced — reject any output outside this range)
- Frame rate: 30fps (re-encode if source differs)
- Subtitle burn-in: hardcoded into video (not separate track) for platform compatibility

---

### 5.12 Thumbnail Module

**Responsibility:** Generate a visually compelling thumbnail image for each clip that maximizes click-through rate.

**Input Contract:**

- `ClipDefinition` (time range for frame extraction)
- `FaceDetectionResult` (to find frames with visible face)
- `HookResult` (for overlay text)
- Source video path

**Output Contract:**

- `ThumbnailResult` DTO containing:
  - `clip_id`: reference
  - `thumbnail_path`: path to JPEG file
  - `resolution`: 1280x720
  - `text_overlay`: the words rendered on the thumbnail
  - `face_visible`: boolean (whether face appears in thumbnail)

**Failure Modes:**

- No frame with face detected → use highest-motion frame with gameplay only
- Text rendering fails (font missing) → fallback to system default bold font
- Frame extraction fails → use frame at clip midpoint

**Constraints:**

- Resolution: 1280x720 (YouTube thumbnail standard)
- Frame selection: score = face_visible × 3 + color_variance × 2 + motion_score × 1
- Layout options:
  - Primary: face on left (40%), gameplay on right (60%), text overlay
  - Fallback: zoomed face center, emoji overlay, text at top
- Text rules:
  - Maximum 2–3 words
  - Font size: minimum 72pt equivalent (must be readable at 120px thumbnail size)
  - High contrast: white text with black stroke (4px outline)
  - Position: top-center or bottom-center, never over face
- Color: boost saturation by 15%, increase contrast by 10% (children's content visual style)
- Format: JPEG, quality 95

---

### 5.13 Metadata Module

**Responsibility:** Generate title, description, and tags for each clip based on content signals.

**Input Contract:**

- `HookResult` (hook text as title seed)
- `Transcript` segment for the clip
- `ClipDefinition` (clip index for uniqueness)

**Output Contract:**

- `MetadataResult` DTO containing:
  - `clip_id`: reference
  - `title`: string (40–60 characters)
  - `description`: string (multi-line, with hashtags)
  - `tags`: list of strings (10–15 tags)
  - `category`: string (Gaming / Entertainment)

**Failure Modes:**

- Duplicate title generated → append clip index suffix
- Title exceeds 60 characters → truncate at last complete word, re-add emoji
- No keywords extracted → use generic template from rotation pool

**Constraints:**

- Title rules:
  - 40–60 characters
  - 1–2 emojis maximum (end of title preferred)
  - Emotional trigger word required (OMG, SECRET, AMAZING, CUTEST, etc.)
  - No clickbait that misrepresents content
- Description template:
  - Line 1: engaging summary (1 sentence)
  - Line 2: empty
  - Line 3–5: hashtags (5–8 tags as hashtags)
  - Line 6: channel branding
- Tags: combination of static tags (always included) and dynamic tags (extracted from transcript keywords)
- Static tags: configurable per channel (e.g. `["kids games", "cute game", "kids youtube", "gaming for kids"]`)
- Dynamic tags: top 5 nouns/phrases from clip transcript

---

### 5.14 Storage Module

**Responsibility:** Persist all pipeline outputs (video files, thumbnails, metadata) and manage clip lifecycle state in a local SQLite database.

**Input Contract:**

- `RenderedClip`, `ThumbnailResult`, `MetadataResult` (all outputs from rendering phase)

**Output Contract:**

- `StorageRecord` DTO containing:
  - `clip_id`: stored ID (confirmed written)
  - `status`: lifecycle state (generated, queued, published, failed)
  - `file_paths`: dictionary of all persisted file paths
  - `created_at`: timestamp

**Failure Modes:**

- Duplicate clip_id insertion → skip (idempotent by design)
- Disk full → abort pipeline with clear error before partial writes
- Database locked → retry with exponential backoff (max 3 attempts)
- Orphaned files (DB write succeeds, file write fails) → cleanup on next pipeline run

**Constraints:**

- Database: SQLite3, single file, WAL mode for concurrent reads
- File storage: organized directory structure (`/output/{video_id}/{clip_id}/`)
- Clip lifecycle: `generated → queued → scheduled → published | failed`
- All file paths stored as relative paths (portable across machines)
- Idempotency: clip_id is primary key, `INSERT ... ON CONFLICT DO NOTHING` semantics

---

### 5.15 Scheduler Module

**Responsibility:** Assign publish dates to queued clips, ensuring consistent daily output.

**Input Contract:**

- List of `StorageRecord` with status = `queued`
- Scheduling configuration (posts per day, start date, preferred time)

**Output Contract:**

- Updated `StorageRecord` list with:
  - `scheduled_at`: assigned publish timestamp
  - `status`: changed to `scheduled`

**Failure Modes:**

- No clips in queue → no-op (not an error)
- Schedule conflict (date already has a published clip) → skip to next available date
- Configuration missing → default: 1 post/day, starting tomorrow, 10:00 AM UTC

**Constraints:**

- Default: 1 Short per day
- Publish time: configurable (default 10:00 AM — optimized for kids audience timezone)
- Order: highest average score first (best clips publish earliest)
- No weekend/weekday distinction (daily cadence)

---

### 5.16 Publisher Module

**Responsibility:** Upload scheduled clips to YouTube with all metadata and thumbnail, then update status.

**Input Contract:**

- `StorageRecord` with status = `scheduled` and `scheduled_at <= now`

**Output Contract:**

- Updated `StorageRecord` with:
  - `status`: `published` or `failed`
  - `youtube_id`: YouTube video ID (on success)
  - `published_at`: actual publish timestamp
  - `error_message`: failure reason (on failure)

**Failure Modes:**

- YouTube API authentication failure → log error, retry on next cron cycle
- Upload timeout → retry up to 3 times with exponential backoff
- Quota exceeded → pause publishing, alert operator, resume next day
- Video rejected by YouTube → mark as `failed`, log rejection reason

**Constraints:**

- Upload via YouTube Data API v3 (OAuth2, credentials stored locally)
- Runs via cron job (not part of the main pipeline process)
- Retry strategy: 3 attempts, backoff [60s, 300s, 900s]
- Publishes as "unlisted" first, then switches to "public" (safety buffer for review)
- Thumbnail uploaded separately after video upload confirms

---

## 6. Face Detection & Composition Design

### 6.1 Why Face Detection is Critical for Retention

YouTube Shorts retention data consistently shows that **human faces in the first 2 seconds increase watch-through rate by 30–50%**. For children's content specifically:

- **Emotional mirroring** — Children engage more when they see another child's reaction. The face cam creates parasocial connection.
- **Visual anchor** — In a split-layout Short, the face provides a stable visual element that contrasts with fast-changing gameplay, reducing cognitive overload.
- **Thumbnail CTR** — Thumbnails with faces receive 2–3× higher click-through than gameplay-only thumbnails. Face detection enables automatic selection of face-present frames for thumbnails.
- **Algorithm signal** — YouTube's recommendation algorithm favors content with high early retention. Face-present openings directly improve this metric.

### 6.2 Detection Strategy

**Interval Sampling (NOT Per-Frame)**

Processing every frame at 30fps for a 60-second clip = 1,800 frames. At ~50ms per MediaPipe inference, that's 90 seconds of processing per clip — longer than the clip itself. Instead:

- Sample at **2fps** (1 frame every 500ms) — 120 frames per 60-second clip
- Processing time: ~6 seconds per clip (acceptable)
- Accuracy is sufficient because face position changes slowly relative to frame rate

**Temporal Smoothing (EMA)**

Raw bounding boxes jitter frame-to-frame due to detection variance. Without smoothing, the face region in the output video would visibly shake.

- Apply **Exponential Moving Average** with alpha = 0.3
- `smoothed_bbox = alpha × current_bbox + (1 - alpha) × previous_smoothed_bbox`
- This creates stable, smooth tracking that eliminates visual jitter
- Alpha = 0.3 provides responsive tracking (adapts to movement within ~1 second) while damping noise

**Detection Confidence Threshold**

- Accept detections with confidence ≥ 0.7
- Below 0.7: treat as "no face" for that frame (avoid false positives from hands, objects)

### 6.3 Handling Missing Face

Face may be missing because:

- Child moved out of frame
- Camera angle changed
- Lighting conditions degraded detection

**Fallback Strategy (Layered):**

1. **Gap bridging** — If face is missing for < 1 second (2 consecutive samples), interpolate bounding box from surrounding detections. Short absences are invisible in the final output.
2. **Partial clip fallback** — If face is missing for 1–5 seconds within a clip, hold the last known bounding box position (freeze the crop region). This appears as natural stillness.
3. **Full clip fallback** — If face visibility ratio < 0.3 (face absent in >70% of clip), abandon the split layout entirely. Use full-frame gameplay with slight zoom and pan (Ken Burns effect) to maintain visual interest.

### 6.4 Layout Strategy

```
┌─────────────────────┐
│                     │
│     GAMEPLAY        │  ← 65% height (1080 × 1248)
│   (center-cropped   │     Source: gameplay capture
│    to 9:16)         │     Transform: center-crop + scale
│                     │
│                     │
├─────────────────────┤
│                     │
│      FACE           │  ← 35% height (1080 × 672)
│   (tracked +        │     Source: face camera
│    zoomed 1.2×)     │     Transform: crop around bbox + zoom
│                     │
└─────────────────────┘

Total: 1080 × 1920 (9:16)
```

**Why 65/35 and not 50/50:**

- Gameplay is the primary content that drives discoverability (game titles, visual action)
- Face is the retention driver but needs less screen real estate to be effective
- 35% height at 1080px width gives 672px of vertical space — more than enough for a face and upper body
- 65% for gameplay preserves enough game UI to remain contextually meaningful

### 6.5 Why Overlay Composition vs. Cropping

**Overlay (chosen):** Two independent video regions composited onto a single canvas.

**Cropping (rejected):** Cutting the source video to show only one region at a time.

Overlay wins because:

- **Both signals preserved simultaneously** — Viewer sees gameplay AND reaction at all times. Cropping forces a choice.
- **Predictable layout** — Every Short has the same visual structure. Viewers develop familiarity, which increases trust and watch time.
- **Simpler face tracking** — Only need to track face position within the face camera region, not across a single mixed frame.
- **Thumbnail consistency** — Every thumbnail can be composed from the same split layout, creating brand visual identity.

---

## 7. Clip Generation Strategy

### 7.1 Why 30–60 Seconds

- **YouTube Shorts requirement** — Shorts must be ≤ 60 seconds. This is a platform hard constraint.
- **Minimum engagement threshold** — Clips under 30 seconds don't provide enough narrative arc (setup → conflict → resolution). They feel like incomplete fragments.
- **Optimal range for children's content** — Attention span data for the target demographic (ages 4–10) shows peak engagement at 35–50 seconds for game highlight content.
- **Monetization threshold** — YouTube's Shorts monetization requires sufficient watch time. Longer Shorts within the limit earn more per view.

### 7.2 Scene Merging Algorithm

Clips are NOT single scenes. Most scenes are 3–20 seconds — far below the 30s minimum. The Clip Builder must merge adjacent scenes intelligently.

**Merging Strategy:**

1. Sort scenes by composite score (descending)
2. Starting from the highest-scored scene, expand outward temporally:
   - Add the next adjacent scene (forward or backward in time)
   - Prefer adding the adjacent scene with the higher score
   - Continue until cumulative duration ≥ 30 seconds
3. If duration exceeds 60 seconds, remove the lowest-scored scene from the edges
4. Mark all scenes used in this clip as consumed
5. Repeat from step 1 with remaining unconsumed scenes

**Key Rule:** Scenes within a clip must be temporally contiguous. No jump cuts. This preserves narrative coherence — the gameplay story flows naturally.

### 7.3 Scoring Influence on Clip Building

The scoring engine produces a ranked scene list. The Clip Builder uses this ranking to:

- **Seed clips around peak moments** — The highest-scored scene becomes the nucleus of a clip. Adjacent scenes are added to reach minimum duration.
- **Maximize signal density** — When choosing which adjacent scene to add, prefer the one with higher composite score.
- **Distribute quality** — After building the top 5 clips, remaining scenes must still produce acceptable clips. A minimum score threshold prevents publishing low-quality content.

### 7.4 Rejection Criteria

A clip is rejected (not queued) if:

- **Duration < 30 seconds** after all available adjacent scenes are consumed
- **Average composite score < 0.2** (bottom quintile of all possible clips)
- **Face visibility ratio < 0.1** AND no valid fallback layout possible
- **No transcript words** AND no audio energy peaks (likely dead air or loading screen)
- **Duplicate content** — Clip overlaps > 50% temporally with a higher-scored clip already selected

---

## 8. Scoring System Design

### 8.1 Scoring Factors

The scoring engine evaluates each scene independently across five dimensions. All factors produce normalized scores in the [0, 1] range.

**Factor 1: Keyword Score**

- Scan transcript text for engagement keywords: "wow", "omg", "look", "secret", "found", "amazing", "cute", "new", "surprise", "build", "explore", "discover"
- Score = (keyword_count / word_count) capped at 1.0
- Weight: 3× (highest weight — keywords are the strongest signal for clip-worthy moments)

**Factor 2: Audio Energy Score**

- Compute RMS audio energy per scene using FFmpeg loudness analysis
- Normalize relative to video's mean energy
- High energy = excitement (cheering, gasping, loud gameplay moments)
- Score = (scene_rms - video_min_rms) / (video_max_rms - video_min_rms)
- Weight: 2×

**Factor 3: Scene Activity Score**

- Measure pixel change rate between sampled frames (motion proxy)
- High activity = action sequences, building, moving through game world
- Low activity = menus, loading screens, idle
- Score = normalized motion magnitude
- Weight: 1× (lowest weight — activity alone isn't sufficient for engagement)

**Factor 4: Face Presence Score**

- Directly from `face_visible_ratio` in FaceDetectionResult
- Score = face_visible_ratio (already 0–1)
- Weight: 2× (face presence is a strong retention signal)

**Factor 5: Sentence Density Score**

- Measure words per second in the scene's transcript
- Optimal range: 2–4 words/second (natural speaking pace)
- Score = 1.0 if within optimal range, decreasing linearly outside
- Very low density (silence) → score near 0
- Very high density (rush speech) → score decreases
- Weight: 1×

### 8.2 Composite Score Formula

```
composite = (keyword × 3 + audio_energy × 2 + face_presence × 2 + scene_activity × 1 + sentence_density × 1) / 9
```

Dividing by sum of weights (9) normalizes the composite to [0, 1].

### 8.3 Why Deterministic Rules Over ML

- **Cost** — Training and running an ML model requires data collection, labeling, compute. Rule-based scoring costs nothing.
- **Explainability** — When a clip scores high, the operator can see exactly why: "keyword score 0.9, face presence 0.85." ML models are opaque.
- **Stability** — ML models drift. Rule-based systems produce identical scores on identical input forever.
- **Domain fit** — Kids gameplay content has strong, consistent engagement signals. Keywords like "wow" and "secret" are reliable across videos. The domain doesn't need learned features.
- **Iteration speed** — Adjusting a weight takes 1 second. Retraining a model takes hours.
- **No training data dependency** — There is no labeled dataset of "good clips vs bad clips" to train on. Rule-based scoring works with zero training data.

---

## 9. Thumbnail Strategy

### 9.1 Why Thumbnails are Critical for CTR

Thumbnails are the single most impactful factor for YouTube Shorts discovery. A Short's impression-to-click conversion (CTR) is determined almost entirely by its thumbnail when shown in the Shorts shelf, search results, and suggested videos.

For children's content:

- **Color and faces win** — Bright, saturated images with visible faces outperform dark or text-heavy thumbnails
- **Simplicity wins** — 2–3 word text overlays outperform paragraph-style text (which is unreadable at thumbnail size)
- **Consistency wins** — A recognizable visual template builds channel brand, increasing repeat viewership

### 9.2 Frame Selection Logic

Not all frames are equal. The thumbnail frame must be carefully selected.

**Frame Scoring Function:**

For each candidate frame (sampled at 1fps across the clip):

- `face_score` = 3 × (1 if face detected and confidence > 0.8, else 0)
- `color_score` = 2 × normalized color variance (high variance = visually interesting)
- `motion_score` = 1 × inter-frame difference (high motion frames are usually mid-action)
- `clarity_score` = 1 × Laplacian variance (reject blurry frames)

**Selection:** `best_frame = argmax(face_score + color_score + motion_score + clarity_score)`

**Tiebreaker:** Prefer frames from the first 30% of the clip (hook moment).

### 9.3 Face Prioritization

- Frame MUST contain a visible face if any face-containing frame exists in the clip
- Face should occupy at least 15% of thumbnail area
- Face expression should show emotion (open mouth, wide eyes) — scored by bounding box height/width ratio as proxy for expression intensity

### 9.4 Text Rules

| Rule       | Constraint                                        |
| ---------- | ------------------------------------------------- |
| Word count | 2–3 words maximum                                 |
| Font size  | Minimum 72pt equivalent                           |
| Font style | Bold, sans-serif                                  |
| Color      | White text, black stroke (4px)                    |
| Position   | Top-center or bottom-center, never over face      |
| Content    | Emotional trigger word + emoji                    |
| Examples   | `OMG 😱`, `SECRET!`, `SO CUTE 🥺`, `NEW HOUSE 🏡` |

### 9.5 Visual Hierarchy

1. **Face** — Primary visual anchor (largest, most prominent element)
2. **Text** — Secondary (draws eye after face, communicates value proposition)
3. **Background** — Tertiary (gameplay context, not competing for attention)

Post-processing applied to all thumbnails:

- Saturation boost: +15%
- Contrast boost: +10%
- Slight vignette to draw focus inward

---

## 10. Metadata Strategy

### 10.1 Title Generation

Titles are generated from templates parameterized with transcript keywords.

**Template Patterns:**

- `OMG… {subject} Found a {adjective} {object} {emoji}`
- `This is the {superlative} {object} Ever {emoji}`
- `{subject} Built a {adjective} {object} {emoji}`
- `Wait Until You See This {object}! {emoji}`
- `{subject} Discovered Something {adjective}! {emoji}`

**Parameter Extraction:**

- `{subject}` → configurable per channel profile (e.g. "She", "Channel Name", "Short Name") (rotated)
- `{object}` → extracted noun from transcript (house, room, pet, garden)
- `{adjective}` → extracted or default (secret, cute, amazing, new, hidden)
- `{superlative}` → cutest, biggest, most amazing, prettiest
- `{emoji}` → selected from: 😱 🥺 🏡 ✨ 😍 🎮 💖 (mapped to emotion tone)

**Rules:**

- 40–60 characters (hard enforced)
- 1–2 emojis only
- No ALL-CAPS words except "OMG" or "NEW"
- Must not duplicate within a batch (uniqueness enforced)

### 10.2 Description Template

```
{hook_sentence} 😍

Watch {channel_name} {action} in this adorable gameplay video!

#{channel_hashtag} #{game_hashtag} #KidsGaming #{dynamic_tag_1} #{dynamic_tag_2}

---
🎮 Game: {game_name}
👧 Channel: {channel_name}
```

**Rules:**

- First line = emotional hook (reuse from HookResult)
- Hashtags: 5–8 total (3 static + 2–5 dynamic from transcript)
- Total length: 150–300 characters

### 10.3 Tag Strategy

**Static Tags (always included):**

- Configurable per channel profile. Example defaults:
- kids games
- cute game
- kids youtube
- gaming for kids
- kids shorts
- gameplay for kids

**Dynamic Tags (per-clip):**

- Top 5 nouns/phrases extracted from clip transcript
- Filtered against allowed word list (child-safe vocabulary)
- Deduplicated against static tags

**Total:** 10–15 tags per video (YouTube allows up to 500 characters of tags)

### 10.4 Consistency Rules

- No two clips in the same batch may share the same title
- Description must reference actual clip content (not generic filler)
- Tags are validated against character limit before storage
- Emoji usage is consistent with title emotion (no mismatch between sad emoji and excited title)

---

## 11. Data & Storage Design

### 11.1 SQLite Schema (Conceptual)

**Table: `videos`** — Source video registry

| Column           | Type      | Purpose                      |
| ---------------- | --------- | ---------------------------- |
| video_id         | TEXT PK   | SHA-256 fingerprint          |
| file_path        | TEXT      | Absolute path to source      |
| duration_seconds | REAL      | Total duration               |
| ingested_at      | TIMESTAMP | Processing timestamp         |
| status           | TEXT      | ingested / processed / error |

**Table: `clips`** — Generated Short registry

| Column          | Type      | Purpose                             |
| --------------- | --------- | ----------------------------------- |
| clip_id         | TEXT PK   | Deterministic ID (video_id + range) |
| video_id        | TEXT FK   | Source video reference              |
| start_time      | REAL      | Clip start (seconds)                |
| end_time        | REAL      | Clip end (seconds)                  |
| duration        | REAL      | Actual duration                     |
| composite_score | REAL      | Scoring engine output               |
| video_path      | TEXT      | Path to rendered MP4                |
| thumbnail_path  | TEXT      | Path to thumbnail JPEG              |
| title           | TEXT      | Generated title                     |
| description     | TEXT      | Generated description               |
| tags            | TEXT      | JSON array of tags                  |
| status          | TEXT      | Lifecycle state                     |
| scheduled_at    | TIMESTAMP | Planned publish time                |
| published_at    | TIMESTAMP | Actual publish time                 |
| youtube_id      | TEXT      | YouTube video ID (post-publish)     |
| error_message   | TEXT      | Failure reason if any               |
| created_at      | TIMESTAMP | Record creation time                |
| updated_at      | TIMESTAMP | Last modification time              |

**Table: `scenes`** — Scene-level data (analysis cache)

| Column             | Type    | Purpose                |
| ------------------ | ------- | ---------------------- |
| scene_id           | TEXT PK | Deterministic ID       |
| video_id           | TEXT FK | Source video reference |
| start_time         | REAL    | Scene start            |
| end_time           | REAL    | Scene end              |
| composite_score    | REAL    | Scene score            |
| face_visible_ratio | REAL    | Face detection result  |
| transcript_text    | TEXT    | Transcribed speech     |
| keyword_score      | REAL    | Individual factor      |
| audio_energy_score | REAL    | Individual factor      |

**Table: `pipeline_runs`** — Execution audit log

| Column          | Type      | Purpose                   |
| --------------- | --------- | ------------------------- |
| run_id          | TEXT PK   | UUID                      |
| video_id        | TEXT FK   | Source video              |
| started_at      | TIMESTAMP | Run start                 |
| completed_at    | TIMESTAMP | Run end                   |
| clips_generated | INTEGER   | Count of clips produced   |
| status          | TEXT      | success / partial / error |
| error_log       | TEXT      | Any errors encountered    |

### 11.2 Clip Lifecycle

```
              ┌─────────────┐
              │  generated   │  Pipeline produced all assets
              └──────┬───────┘
                     │
                     ▼
              ┌─────────────┐
              │   queued     │  Assets verified, ready for scheduling
              └──────┬───────┘
                     │
                     ▼
              ┌─────────────┐
              │  scheduled   │  Publish date assigned
              └──────┬───────┘
                     │
              ┌──────┴───────┐
              │              │
              ▼              ▼
       ┌────────────┐ ┌───────────┐
       │  published  │ │  failed   │
       └────────────┘ └─────┬─────┘
                            │
                            ▼
                     ┌────────────┐
                     │  retry     │  (back to scheduled, max 3×)
                     └────────────┘
```

### 11.3 Idempotency Design

- **clip_id** is derived deterministically: `SHA256(video_id + start_ms + end_ms)[:16]`
- Re-running the pipeline on the same video with the same configuration produces the same clip_ids
- Database uses `INSERT ... ON CONFLICT DO NOTHING` — existing clips are never overwritten
- File writes check for existence before writing — existing files are skipped
- Pipeline run status is tracked to detect and resume incomplete runs
- A complete re-process requires explicit `--force` flag (safety against accidental overwrites)

### 11.4 Filesystem Layout

```
output/
├── {video_id}/
│   ├── scenes/
│   │   ├── scene_001.json          (scene metadata)
│   │   └── scene_002.json
│   ├── clips/
│   │   ├── {clip_id}/
│   │   │   ├── composite.mp4       (intermediate)
│   │   │   ├── final.mp4           (deliverable)
│   │   │   ├── thumbnail.jpg       (1280x720)
│   │   │   ├── subtitles.ass       (subtitle track)
│   │   │   ├── narration.wav       (TTS audio)
│   │   │   └── metadata.json       (title, desc, tags)
│   │   └── {clip_id}/
│   │       └── ...
│   └── pipeline.log                (run log)
└── shorts.db                       (SQLite database)
```

---

## 12. Scheduling & Publishing

### 12.1 Daily Publishing Model

The system operates on a **one Short per day** cadence. This is optimal for:

- **Algorithm consistency** — YouTube favors channels with regular upload schedules
- **Audience habituation** — Regular viewers (kids + parents) develop a daily viewing routine
- **Content longevity** — 10–15 clips from one recording session provide 2+ weeks of content, reducing recording frequency
- **Quality control** — Daily pace gives the operator time to review upcoming Shorts

### 12.2 Queue Consumption

The scheduler runs after each pipeline execution and whenever new clips enter the `queued` state.

**Scheduling Algorithm:**

1. Fetch all clips with `status = queued`, ordered by `composite_score DESC`
2. Find the last scheduled date in the database (or use tomorrow if empty)
3. Assign each clip the next available publish slot:
   - One slot per day
   - Publish time: 10:00 AM UTC (configurable)
   - Skip dates that already have a `scheduled` or `published` clip
4. Update clip status to `scheduled` with the assigned timestamp

**Best clips publish first.** This front-loads the highest-quality content, maximizing early subscriber acquisition from the batch.

### 12.3 Retry Strategy

Publishing failures are inevitable (network issues, API rate limits, transient YouTube errors).

**Retry Policy:**

| Attempt     | Delay      | Action                           |
| ----------- | ---------- | -------------------------------- |
| 1st failure | 60 seconds | Immediate retry                  |
| 2nd failure | 5 minutes  | Retry with backoff               |
| 3rd failure | 15 minutes | Final retry                      |
| 4th failure | —          | Mark as `failed`, alert operator |

**Failed clips:**

- Remain in the queue with `status = failed`
- Are not automatically rescheduled (require operator intervention)
- Do not block subsequent clips from publishing
- Retain all assets for manual upload if needed

### 12.4 Publishing Safety

- Clips are initially uploaded as **unlisted**
- After 30-minute delay (configurable), status is changed to **public**
- This provides a safety buffer: if something is wrong, the operator can catch it before public exposure
- Publisher runs as a cron job, independent from the main pipeline process

---

## 13. Pipeline Behavior

### 13.1 Batch Processing

The pipeline operates in strict batch mode. A single invocation processes one complete input video end-to-end.

**Execution Flow:**

```
python run_pipeline.py input.mp4
```

1. Ingest and validate the input video
2. Split into scenes (all scenes, one pass)
3. Transcribe audio (full video, one pass)
4. Detect faces (all scenes, one pass per scene but parallelizable)
5. Score all scenes (requires steps 2–4 complete)
6. Build clips from scored scenes
7. For each clip (sequential, deterministic order):
   a. Generate hook
   b. Synthesize TTS
   c. Generate subtitles
   d. Compose face + gameplay layout
   e. Render final video
   f. Generate thumbnail
   g. Generate metadata
   h. Store to database + filesystem
8. Schedule all new clips
9. Print summary report

**No streaming.** Stage N must complete fully before stage N+1 begins (for stages with dependencies). Independent stages (thumbnail + metadata) may run in parallel.

### 13.2 Rerun Safety

If the pipeline is interrupted or rerun:

- **Already-ingested video** → detected by video_id, skips ingestion
- **Already-analyzed scenes** → detected by scene_id in database, skips analysis
- **Already-rendered clips** → detected by clip_id in database, skips rendering
- **Partially complete run** → `pipeline_runs` table tracks progress; resume from last completed stage

The operator can safely run the pipeline multiple times without producing duplicates or overwriting existing outputs.

### 13.3 Deterministic Outputs

Given the same input video and the same configuration file, the pipeline produces:

- Identical scene boundaries (PySceneDetect with fixed threshold)
- Identical transcriptions (faster-whisper with fixed model and seed)
- Identical scores (deterministic formula)
- Identical clip selections (deterministic merging algorithm)
- Identical hook text (deterministic template selection based on keyword hash)
- Identical TTS audio (same text → same audio with fixed voice settings)
- Identical thumbnails (same frame selection, same text overlay)
- Identical metadata (same templates, same keyword extraction)

The only non-deterministic element is Edge TTS synthesis, which has minor variance across runs. If perfect determinism is required, TTS output is cached by input text hash.

---

## 14. Performance Targets

### 14.1 Processing Time Budget

For a **1-hour input video** on a consumer machine (8-core CPU, 16GB RAM, no GPU):

| Stage                     | Estimated Time | Notes                                    |
| ------------------------- | -------------- | ---------------------------------------- |
| Ingestion                 | < 10 seconds   | Hash computation + FFprobe               |
| Scene Splitting           | 2–3 minutes    | PySceneDetect, single pass               |
| Transcription             | 5–8 minutes    | faster-whisper small model, CPU          |
| Face Detection            | 3–5 minutes    | MediaPipe, 2fps sampling                 |
| Scoring                   | < 5 seconds    | Pure computation, no I/O                 |
| Clip Building             | < 1 second     | Pure computation                         |
| Per-Clip Processing (×12) | 1–2 min each   | Hook + TTS + subtitle + compose + render |
| Thumbnails (×12)          | 10–15 sec each | Frame extraction + PIL processing        |
| Metadata (×12)            | < 1 sec each   | Template filling                         |
| Storage                   | < 5 sec total  | SQLite writes + file copies              |

**Total: ~20–30 minutes** for 10–12 clips from a 1-hour video.

### 14.2 GPU Optimization (Optional)

| Component                      | CPU Time  | GPU Time        |
| ------------------------------ | --------- | --------------- |
| Transcription (faster-whisper) | 5–8 min   | 1–2 min         |
| Face Detection (MediaPipe)     | 3–5 min   | 1–2 min         |
| Video Encoding (FFmpeg)        | 12–24 min | 4–8 min (NVENC) |

With GPU: total pipeline time drops to **~10–15 minutes** for 1-hour input.

### 14.3 Resource Constraints

- **Memory:** Peak usage ~4GB (Whisper model loaded + video frames in memory)
- **Disk:** ~2GB per batch output (12 clips × 150MB each + thumbnails + metadata)
- **CPU:** Multi-threaded where possible (FFmpeg, Whisper), but pipeline stages are sequential
- **Network:** Zero during pipeline execution. Only publisher requires network access.

---

## 15. Failure Modes & Safeguards

### 15.1 Critical Risk: Bad Clip Selection

**Risk:** Pipeline selects boring, repetitive, or contextually meaningless clips.

**Mitigation:**

- Multi-factor scoring prevents single-signal dominance
- Minimum score threshold rejects bottom-quintile clips
- Temporal distribution check ensures clips aren't clustered from one section of the video
- Human review layer (optional) provides final quality gate before publishing

### 15.2 Critical Risk: Face Detection Failure

**Risk:** MediaPipe fails to detect face consistently, leading to broken composition or full-gameplay fallback on every clip.

**Mitigation:**

- Confidence threshold (0.7) prevents false positives
- EMA smoothing bridges short detection gaps (< 1 second)
- Fallback layout chain: smooth interpolation → hold last bbox → full-gameplay zoom
- Per-scene face visibility ratio is logged — operator can spot systemic detection issues
- Pipeline-level alert if average face visibility across all clips < 0.3

### 15.3 Critical Risk: Poor Thumbnails

**Risk:** Generated thumbnails are blurry, faceless, or text-obscured — leading to low CTR.

**Mitigation:**

- Blur detection (Laplacian variance) rejects blurry frames
- Face-presence scoring prioritizes frames with visible face
- Text placement rules prevent face occlusion
- Thumbnail quality metrics logged for operator review
- Fallback thumbnail strategy if no frame meets quality threshold

### 15.4 Critical Risk: Repetitive Hooks

**Risk:** Template-based hook generation produces same-sounding titles and narrations across clips, reducing perceived content variety.

**Mitigation:**

- Template pool of 30+ patterns with parameterized slots
- Rotation enforcement: no template reused within a single batch
- Duplicate title detection across batch
- Keyword extraction diversifies template parameters
- Hook template pool is designed with structural variety (questions, exclamations, revelations, imperatives)

### 15.5 Critical Risk: Pipeline Corruption on Interruption

**Risk:** Pipeline interrupted mid-execution leaves partial outputs, orphaned files, or inconsistent database state.

**Mitigation:**

- Each clip is an atomic unit — fully written to disk + database or not at all
- Temporary files written with `.tmp` suffix, renamed atomically on completion
- Database uses WAL mode, transactions are per-clip
- Pipeline run table tracks completion status for resumability
- Startup check detects and cleans orphaned `.tmp` files from previous interrupted runs

### 15.6 Critical Risk: Disk Space Exhaustion

**Risk:** Video processing generates large intermediate files that fill available disk.

**Mitigation:**

- Pre-flight check: estimate required disk space (3× input file size) and verify availability
- Intermediate files (composite video before final render) deleted after successful rendering
- Pipeline reports total disk usage in summary output
- Configurable cleanup of intermediate artifacts vs. keep-all mode

---

## 16. Parallel Development Strategy

### 16.1 Why Parallel Development Matters

This system has 16 modules. Developing them sequentially would create an unnecessarily long critical path. The modular monolith architecture is specifically designed to enable parallel development — multiple modules developed, tested, and merged independently.

### 16.2 GitHub CLI Workflow

**Branch Naming Convention:**

```
feature/{module_name}          — new module implementation
fix/{module_name}/{issue}      — bug fix in existing module
refactor/{module_name}         — internal restructuring
```

**Examples:**

```
feature/scene-splitter
feature/scoring-engine
feature/thumbnail-generator
fix/face-detection/bbox-jitter
refactor/clip-builder
```

**Workflow per Module:**

1. Create feature branch from `main`
2. Implement module against its DTO contract
3. Write unit tests against contract
4. Open PR with module-specific reviewer checklist
5. Merge to `main` after CI passes (tests + lint)

### 16.3 Feature Branches per Module

Each module is developed on its own feature branch because:

- **No merge conflicts** — Modules live in separate packages with no cross-imports of internals
- **Independent CI** — Tests for module A don't need module B to be complete (mock inputs via DTO contracts)
- **Incremental integration** — Modules can be merged to `main` in any order. The orchestrator is the last piece, wiring everything together.
- **Clear ownership** — Each branch/PR maps to exactly one module, enabling focused code review

### 16.4 Why Modules Must Be Independent

Independence is the prerequisite for parallel development. It is enforced through:

- **No import of internal symbols** — Module A cannot import a private function from Module B. Only the public DTO and the module's entry function are importable.
- **Shared nothing** — No global state, no shared mutable variables, no singleton dependencies
- **Filesystem isolation** — Each module reads from its contracted input path and writes to its contracted output path. No module reads another module's working directory.

### 16.5 DTO Contracts as Integration Boundaries

**What is a DTO contract?**

A DTO (Data Transfer Object) is a plain data class that defines the shape of data flowing between modules. It contains no logic, no methods, no dependencies.

**Why DTOs prevent conflicts:**

- DTOs are defined in a shared `contracts` package (the only shared code)
- DTOs are versioned and immutable — fields are added, never removed or renamed
- Each module has an `input DTO` (what it receives) and an `output DTO` (what it produces)
- During development, any module can be tested by constructing its input DTO directly — no dependency on upstream modules being complete

**Contract Enforcement:**

```
contracts/
├── ingestion.py          — IngestionResult
├── scene.py              — SceneList, SceneSegment
├── transcript.py         — Transcript, TranscriptSegment, Word
├── face.py               — FaceDetectionResult, SceneFaceData, FaceBBox
├── scoring.py            — ScoredSceneList, ScoredScene
├── clip.py               — ClipList, ClipDefinition
├── hook.py               — HookResult
├── tts.py                — TTSResult
├── subtitle.py           — SubtitleResult
├── composite.py          — CompositeStream
├── render.py             — RenderedClip
├── thumbnail.py          — ThumbnailResult
├── metadata.py           — MetadataResult
└── storage.py            — StorageRecord
```

**Development Parallelism Matrix:**

| Module         | Can develop in parallel with       | Dependency                              |
| -------------- | ---------------------------------- | --------------------------------------- |
| Ingestion      | All others                         | None                                    |
| Scene Splitter | Transcription, Face Detection      | Ingestion contract only                 |
| Transcription  | Scene Splitter, Face Detection     | Ingestion contract only                 |
| Face Detection | Scene Splitter, Transcription      | Ingestion contract only                 |
| Scoring        | Clip Builder, Hook Gen             | Scene + Transcript + Face contracts     |
| Clip Builder   | Hook Gen, TTS, Thumbnail           | Scoring contract only                   |
| Hook Generator | TTS, Subtitle, Thumbnail, Metadata | Clip + Transcript contracts             |
| TTS            | Subtitle, Thumbnail, Metadata      | Hook contract only                      |
| Compositor     | Renderer                           | Face + Clip contracts                   |
| Renderer       | Storage                            | Composite + TTS + Subtitle contracts    |
| Thumbnail      | Metadata, Storage                  | Clip + Face + Hook contracts            |
| Metadata       | Thumbnail, Storage                 | Hook + Transcript contracts             |
| Storage        | Scheduler                          | Render + Thumbnail + Metadata contracts |
| Scheduler      | Publisher                          | Storage contract only                   |
| Publisher      | —                                  | Storage contract only                   |

**Key Insight:** Modules in the same row of the dependency table can be developed simultaneously by different developers, each working against the shared DTO contracts.

---

## 17. Explicit Non-Goals

The following are deliberately excluded from this system's scope. They are not future features — they are architectural decisions against inclusion.

### No Microservices

This system will never be split into independently deployed services. The overhead of service orchestration, network communication, and distributed state management is antithetical to the zero-cost, single-machine constraint. If the system needs to scale, it scales vertically (faster machine) or horizontally (run multiple independent instances on separate machines, each processing different videos).

### No Distributed Systems

No message queues (Kafka, RabbitMQ), no distributed databases (Postgres clusters), no container orchestration (Kubernetes). The system runs as a single process on a single machine, backed by a single SQLite file. This is not a limitation — it is a deliberate design constraint that eliminates an entire class of operational failure modes.

### No Paid APIs

No OpenAI, no Google Cloud, no AWS, no Anthropic, no ElevenLabs, no any paid service for content generation. Every computation runs locally using open-source models and tools. The only external API used is YouTube Data API for publishing, which is free within standard quotas.

### No Autonomous Runtime Agents

No LangChain, no AutoGPT, no CrewAI, no agent loops, no planning-and-execution cycles. Every pipeline decision is deterministic and hardcoded. Agents introduce non-determinism, cost, latency, and fragility — all of which violate core design principles.

### No Real-Time Processing

No live streaming, no real-time clip detection, no webhooks, no event-driven processing. The system processes recorded video in batch mode. Real-time processing would require fundamentally different architecture (streaming pipelines, low-latency inference) that is out of scope for this system's use case.

### No Content Moderation AI

No automated content safety scanning beyond basic keyword filtering. The system produces content from a controlled source (single recurring game, single child creator). Content safety is handled at the source level (game selection, recording environment), not at the pipeline level.

### No Multi-Language Support (v1)

English only for transcription, hooks, metadata, and subtitles. Internationalization is a v2+ concern that would multiply template complexity and testing surface.

### No Mobile or Web Interface

No dashboard, no web UI, no mobile app. Interaction is command-line only. Operator review happens by opening the output directory and inspecting files directly. A UI layer would add dependency weight without solving the core automation problem.

---

## 18. Final System Definition

The **Shorts Factory** is a deterministic content production pipeline that transforms long-form gameplay recordings into fully-packaged, publish-ready YouTube Shorts.

### What It Is

- **A content factory** — Raw video in, scheduled Shorts out. No creative decisions required from the operator.
- **A deterministic system** — Same input, same configuration, same output. Every time. Auditable, debuggable, trustworthy.
- **A modular monolith** — 16 well-bounded modules in a single process, communicating through typed DTO contracts. Simple to deploy, simple to debug, simple to extend.
- **A zero-cost pipeline** — Runs entirely on local hardware. No cloud bills, no API keys, no subscription fees. The only external service is YouTube's free upload API.
- **A batch processor** — One command, one video, 10–15 Shorts. Then walk away. Come back in 5 minutes to review thumbnails. Done.

### What It Optimizes For

- **Kids engagement** — Face-present composition, emotional hooks, bright thumbnails, short durations. Every design decision targets the 4–10 age demographic's attention patterns.
- **Operator efficiency** — One hour of recording → 2+ weeks of daily content. The pipeline eliminates the editing bottleneck entirely. Human involvement is reduced to a 5-minute daily review.
- **Publishing consistency** — Automated scheduling ensures daily uploads without operator intervention. Consistent cadence builds audience habit and algorithm trust.
- **Scalability through simplicity** — Adding a second child creator, a second game, or a second channel requires running the same pipeline with different inputs. No configuration changes, no infrastructure scaling.

### Architectural Commitments

| Decision                                | Rationale                                                                  |
| --------------------------------------- | -------------------------------------------------------------------------- |
| Modular monolith over microservices     | Single-machine execution, no network overhead, atomic transactions         |
| Deterministic over ML/AI                | Reproducible, explainable, zero-cost, no training data needed              |
| SQLite over Postgres/cloud DB           | Single-file, zero-config, zero-cost, sufficient for this scale             |
| Rule-based scoring over learned models  | Stable, debuggable, domain-appropriate, no data dependency                 |
| Template-based text over LLM generation | Deterministic, free, fast, child-safe guaranteed                           |
| Batch mode over streaming               | Matches usage pattern, enables global optimization, simpler error handling |
| Local execution over cloud              | Zero cost, no latency, data stays on-premises                              |

### The Operator's Experience

```
$ python run_pipeline.py ~/videos/gameplay_session_042.mp4

[1/8] Ingesting video... done (1h 12m, 1080p)
[2/8] Splitting scenes... done (187 scenes)
[3/8] Transcribing audio... done (4,231 words)
[4/8] Detecting faces... done (avg visibility: 0.72)
[5/8] Scoring and building clips... done (12 clips selected)
[6/8] Rendering clips... done (12/12 rendered)
[7/8] Generating thumbnails + metadata... done
[8/8] Scheduling... done (12 Shorts queued, next 12 days)

Summary:
  Clips generated: 12
  Average score: 0.68
  Total duration: 8m 24s
  Disk usage: 1.8 GB
  Next publish: 2026-03-24 10:00 UTC

Review thumbnails: open output/a3f2b1c9/clips/
```

The system converts creative labor into engineering automation. Record once. Process once. Publish for weeks.

---

_End of architecture document._
