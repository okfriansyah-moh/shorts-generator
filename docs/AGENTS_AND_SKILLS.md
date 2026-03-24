# Shorts Factory — Agents & Skills System

> Development acceleration system for the Shorts Factory pipeline.
> This document defines the agent/skill architecture that enforces consistency, reduces token usage, and enables safe parallel development.

---

## 1. System Overview

### Why Agents Alone Are Not Enough

An agent (e.g., `phase-builder`) receives a task, reads documentation, and produces code. But every invocation re-reads the same architectural constraints, re-parses the same DTO definitions, and re-derives the same validation rules. This creates three problems:

1. **Token waste** — Each agent invocation spends 30–50% of its context window re-reading foundational docs
2. **Reasoning drift** — Without shared enforcement logic, two agents may interpret the same constraint differently
3. **Duplication** — Validation rules (e.g., "clip duration must be 30–60s") get reimplemented in every agent instead of defined once

### Why Skills Are Required

Skills are **reusable reasoning units** that encode domain knowledge once and are loaded on-demand by any agent that needs them. They solve all three problems:

1. **Token efficiency** — A skill loads only its focused domain (~200–500 tokens) instead of the agent re-reading full docs (~5000+ tokens)
2. **Consistency** — All agents share the same DTO validation logic, the same pipeline ordering rules, the same determinism checks
3. **Single source of truth** — A constraint change updates one skill file, not five agent files

### How This Reduces Token Usage

```
WITHOUT SKILLS:
  phase-builder reads: architecture.md (5K) + roadmap (8K) + dto_contracts (6K) + orchestrator_spec (4K)
  module-builder reads: architecture.md (5K) + roadmap (8K) + dto_contracts (6K) + ...
  dto-guardian reads: dto_contracts (6K) + architecture.md (5K) + ...
  Total per session: ~60K+ tokens in doc reads alone

WITH SKILLS:
  phase-builder loads: dto skill (400) + pipeline skill (300) + determinism skill (300)
  module-builder loads: dto skill (400) + modularity skill (300) + testing skill (300)
  dto-guardian loads: dto skill (400) + idempotency skill (200)
  Total per session: ~3K tokens in skill reads
  Savings: ~95% reduction in repeated context loading
```

---

## 2. Agent vs Skill Model

| Component | Role                                                         | Scope                        | State                      | Context Cost                    |
| --------- | ------------------------------------------------------------ | ---------------------------- | -------------------------- | ------------------------------- |
| **Agent** | Executes a task (build module, validate DTOs, refactor code) | Task-specific, session-bound | Stateful within session    | High (reads docs + writes code) |
| **Skill** | Provides reusable domain knowledge and validation rules      | Cross-agent, cross-session   | Stateless (pure reference) | Low (~200–500 tokens per skill) |

### Design Rules

1. **Agents call skills, never the reverse** — Skills are passive knowledge; agents are active executors
2. **Skills are stateless** — No session memory, no side effects, no file writes
3. **Skills are composable** — An agent loads 2–4 skills per task, combining their constraints
4. **No logic duplication** — If two agents need the same validation, it lives in a skill
5. **Skills reference docs, agents reference skills** — Agents should rarely read raw docs directly

---

## 3. Agent Types

### 3.1 Phase Builder Agent

**Status:** Already implemented at `.github/agents/phase-builder.agent.md`

**Purpose:** Autonomous phase implementation from `docs/implementation_roadmap.md`. Reads phase definition, extracts tasks, implements modules with production-ready code and tests.

**Do not redefine.** All other agents coordinate with or delegate to this agent for phase-scoped work.

---

### 3.2 Orchestrator Agent

**File:** `.github/agents/orchestrator.agent.md`

**Purpose:** Enforce the execution model from `docs/orchestrator_spec.md` when building or modifying the orchestrator module.

**Responsibilities:**

- Validate stage ordering matches the 16-stage pipeline sequence
- Enforce checkpoint-after-every-stage pattern
- Ensure resume-from-last-checkpoint logic is correct
- Validate pre-flight checks (disk space, FFmpeg, Python version)
- Enforce pipeline run state transitions (`started → analyzing → building → completed | partial | failed`)
- Ensure clip state lifecycle (`generated → queued → scheduled → published | failed`)

**Skills used:** Pipeline Reasoning, Idempotency, Failure Handling, SQLite

**Database constraint:** All DB access must go through `database/adapter.py`. The orchestrator is the ONLY component that calls the adapter. See `docs/db_adapter_spec.md`.

---

### 3.3 DTO Guardian Agent

**File:** `.github/agents/dto-guardian.agent.md`

**Purpose:** Enforce DTO contracts from `docs/dto_contracts.md` and `contracts/` package. This is the highest-priority validation agent — DTO violations cascade into every downstream module.

**Responsibilities:**

- Validate that all module inputs/outputs use frozen dataclass DTOs from `contracts/`
- Detect contract drift (field renamed, type changed, field removed)
- Block invalid field usage (wrong type, missing required field, extra undeclared field)
- Enforce additive-only versioning (new fields OK, removals/renames forbidden)
- Validate DTO constraints (score ranges [0.0–1.0], duration [30–60]s, ID format)
- Ensure no raw dicts cross module boundaries

**Skills used:** DTO Interpretation, Module Boundary, Determinism Enforcement

---

### 3.4 Module Builder Agent

**File:** `.github/agents/module-builder.agent.md`

**Purpose:** Build individual pipeline modules safely within their boundaries.

**Responsibilities:**

- Create module package under `modules/{name}/` with proper `__init__.py`
- Implement module logic using only `contracts/` DTOs for input/output
- Ensure no cross-module imports (only `contracts/` types)
- Generate unit tests with fixture data (no GPU, no network, no real videos)
- Follow structured logging with required fields
- Read config from YAML, never hardcode thresholds

**Skills used:** DTO Interpretation, Module Boundary, Determinism Enforcement, Testing

**Database constraint:** Modules MUST NOT import `sqlite3` or any database driver. Modules MUST NOT contain SQL strings. All database access is handled by the orchestrator via `database/adapter.py`. See `docs/db_adapter_spec.md`.

**File:** `.github/agents/integration.agent.md`

**Purpose:** Connect modules through the orchestrator, validating end-to-end pipeline flow.

**Responsibilities:**

- Ensure DTO compatibility between adjacent modules (output of stage N matches input of stage N+1)
- Validate pipeline flow matches the 16-stage sequence
- Detect hidden coupling (shared files, implicit dependencies, import leaks)
- Write integration tests that exercise multi-stage sequences
- Verify checkpoint/resume behavior across stage boundaries

**Skills used:** Pipeline Reasoning, DTO Interpretation, Idempotency, Failure Handling

**Database constraint:** Integration tests must validate that the orchestrator uses `database/adapter.py` as the sole DB entry point and that no module bypasses it. See `docs/db_adapter_spec.md`.

**File:** `.github/agents/refactor.agent.md`

**Purpose:** Improve code structure without changing behavior or breaking contracts.

**Constraints:**

- No behavior change — output must be identical for same input
- No DTO changes — frozen contracts are untouchable
- No new dependencies — cannot add libraries
- No module boundary violations — cannot merge or cross modules
- Must preserve all existing tests (green before → green after)

**Skills used:** Module Boundary, Determinism Enforcement, Testing

### 3.5 Code Fixer

**File:** `.github/agents/code-fixer.agent.md`

**Purpose:** Automated quality gate and integration check fixer. Runs fully autonomously (no human interaction) to fix all failing checks so the integration branch passes every automated verification.

**Responsibilities:**

- Fix import failures (syntax errors, missing `__init__.py`, broken import chains)
- Fix lint violations via `ruff check --fix` with manual cleanup
- Fix test failures by correcting source code or test expectations
- Replace `print()` statements with `logging.getLogger(__name__).info()`
- Remove cross-module imports; refactor to DTO-based communication
- Move raw SQL out of `modules/` into `database/adapter.py`

**Skills used:** Code Quality Fixer, Architecture Reader, Modularity, Testing, Logging, SQLite

**Execution mode:** Fully autonomous (CI-like pipeline, no user interaction)

### 3.6 Conflict Resolver

**File:** `.github/agents/conflict-resolver.agent.md`

**Purpose:** Architecture-aware Git merge conflict resolver for parallel phase development. Uses union strategy — code from ALL phases is preserved; later phase is tiebreaker only for truly incompatible changes.

**Responsibilities:**

- Identify all conflicted files via `git diff --name-only --diff-filter=U`
- Resolve conflicts by combining both sides (union strategy)
- Handle `__init__.py` conflicts with union of all exports
- Handle migration file conflicts with sequential renumbering
- Handle test file conflicts by keeping ALL test functions
- Enforce architecture post-resolution (no cross-module imports, no raw SQL, no `print()`)

**Skills used:** Conflict Resolver, Modularity, Architecture Reader

**Execution mode:** Fully autonomous (CI-like pipeline, no user interaction)

### 3.7 Merge Reviewer

**File:** `.github/agents/merge-reviewer.agent.md`

**Purpose:** Post-merge integration reviewer for parallel phase development. Verifies all merged phases are fully implemented, architecturally compliant, and test-passing.

**Responsibilities:**

- Verify file existence, migrations, modules, contracts, and tests per phase checklist
- Audit code quality: frozen DTOs, determinism, structured logging, DB adapter usage, type hints
- Verify DTO compatibility across adjacent stages
- Run full quality gate and fix any issues found
- Synchronize documentation via docs-sync skill
- Generate per-phase completion summaries

**Skills used:** Merge Reviewer, Architecture Reader, Docs Sync, Doc Standardization, Code Quality Fixer

**Execution mode:** Fully autonomous (CI-like pipeline, no user interaction)

---

## 4. Skills System

### Skill 1 — DTO Interpretation

**File:** `.github/skills/dto/SKILL.md`

**Purpose:** Parse, validate, and enforce DTO contracts across all agent activities.

**Provides:**

- Complete DTO registry (22 DTOs, their fields, types, constraints)
- Producer/consumer mapping (which module creates/reads each DTO)
- Validation rules (ranges, formats, required fields)
- Anti-patterns (raw dicts, mutable state, cross-module types)

**Used by:** Phase Builder, DTO Guardian, Module Builder, Integration Agent

---

### Skill 2 — Pipeline Reasoning

**File:** `.github/skills/pipeline/SKILL.md`

**Purpose:** Understand and enforce the 16-stage sequential pipeline.

**Provides:**

- Stage ordering and dependencies
- Input/output DTO mapping per stage
- Checkpoint behavior per stage
- Parallelism matrix (which stages can be developed in parallel)

**Used by:** Phase Builder, Orchestrator, Integration Agent

---

### Skill 3 — Determinism Enforcement

**File:** `.github/skills/determinism/SKILL.md`

**Purpose:** Detect and prevent non-deterministic behavior.

**Provides:**

- Forbidden patterns (`random`, `datetime.now()` as logic input, network-dependent decisions)
- ID generation rules (SHA-256 based, content-addressable)
- Sorting requirements (deterministic tiebreakers)
- TTS caching requirement (cache by text hash)

**Used by:** Phase Builder, Module Builder, Refactor Agent

---

### Skill 4 — Idempotency

**File:** `.github/skills/idempotency/SKILL.md`

**Purpose:** Ensure all operations are safely repeatable.

**Provides:**

- ID computation formulas (video_id, scene_id, clip_id)
- Database semantics (ON CONFLICT DO NOTHING, atomic writes)
- File write patterns (write to .tmp, atomic rename)
- Resume behavior (skip existing, process only missing)

**Used by:** Phase Builder, Orchestrator, Integration Agent

---

### Skill 5 — Module Boundary

**File:** `.github/skills/modularity/SKILL.md`

**Purpose:** Enforce the modular monolith architecture.

**Provides:**

- Import rules (only `contracts/` shared across modules)
- Package structure (`modules/{name}/__init__.py` pattern)
- File ownership per phase
- Anti-patterns (cross-imports, shared mutable state, global singletons)

**Used by:** Phase Builder, Module Builder, DTO Guardian, Refactor Agent

---

### Skill 6 — Failure Handling

**File:** `.github/skills/failure/SKILL.md`

**Purpose:** Enforce retry logic, failure thresholds, and graceful degradation.

**Provides:**

- Retry policies (per-clip: 2 retries; publishing: 3 retries with exponential backoff)
- Failure thresholds (>50% clip failures → abort pipeline)
- Graceful degradation (face detection optional, empty transcript handled)
- State transitions on failure
- FFmpeg timeout handling (300s per clip)

**Used by:** Orchestrator, Integration Agent, Phase Builder

---

### Skill 7 — Token Optimization

**File:** `.github/skills/token-optimization/SKILL.md`

**Purpose:** Minimize context window usage across all agent interactions.

**Provides:**

- Context loading strategy (skills first, docs only when needed)
- Progressive disclosure (load overview → load detail only if needed)
- Caching patterns (don't re-read unchanged docs)
- Prompt compression techniques
- When to use subagents for isolated reads

**Used by:** All agents (meta-skill)

---

### Skill 8 — Configuration Validation (Additional)

**File:** `.github/skills/config-validation/SKILL.md`

**Purpose:** Enforce that all tunable parameters live in YAML config with no hardcoded values.

**Provides:**

- Config structure reference (all sections of `config.yaml`)
- Magic number detection rules
- Environment variable override patterns
- Validation rules for each config section

**Used by:** Phase Builder, Module Builder, Refactor Agent

---

### Skill 9 — Structured Logging (Additional)

**File:** `.github/skills/logging/SKILL.md`

**Purpose:** Enforce consistent structured JSON logging across all modules.

**Provides:**

- Required base fields (7 fields: `run_id`, `video_id`, `clip_id`, `stage`, `status`, `error_reason`, `duration_ms`, `timestamp`)
- Stage-specific extension fields
- Log level usage rules (INFO/WARN/ERROR/CRITICAL)
- Anti-patterns (print statements, unstructured strings, missing fields)

**Used by:** Phase Builder, Module Builder

---

### Skill 10 — Testing Patterns (Additional)

**File:** `.github/skills/testing/SKILL.md`

**Purpose:** Enforce test design rules specific to this project.

**Provides:**

- Fixture generation from DTO contracts
- FFmpeg mocking patterns (subprocess mock)
- No-GPU, no-network, no-real-video constraints
- Integration test patterns for multi-stage validation
- Idempotency test patterns (run twice, compare output)

**Used by:** Phase Builder, Module Builder, Integration Agent

---

## 4b. Technical Skills

Technical skills encode library-specific API patterns, command templates, configuration, and gotchas for the external dependencies used across the pipeline. While architectural skills (Section 4) enforce _how modules communicate_, technical skills encode _how modules do their work_.

---

### Skill 11 — FFmpeg / FFprobe

**File:** `.github/skills/ffmpeg/SKILL.md`

**Purpose:** All video/audio processing via subprocess — probing, extraction, compositing, rendering, and subtitle burn-in.

**Provides:**

- FFprobe metadata extraction command + JSON parsing
- Audio extraction (WAV 16kHz mono for Whisper)
- Frame extraction at configurable FPS
- Composite layout filter chains (9:16 vertical, 65/35 split)
- Final render command (H.264, CRF 20, AAC 128k, 30fps)
- Subprocess execution pattern (timeout, capture, atomic writes)
- Retry policy (1 retry on timeout with lower quality)
- Output specification table (codec, resolution, bitrate, etc.)

**Used by:** Module Builder (ingestion, compositor, renderer), Phase Builder (Phases 1, 5, 6)

---

### Skill 12 — PySceneDetect

**File:** `.github/skills/pyscenedetect/SKILL.md`

**Purpose:** Scene boundary detection using content-aware thresholding.

**Provides:**

- `ContentDetector` / `AdaptiveDetector` configuration
- Threshold tuning (default 27.0, from config)
- Post-processing: merge micro-scenes (< 3s), split macro-scenes (> 20s)
- Timing format conversion (FrameTimecode → milliseconds)
- Scene ID generation formula
- DTO conversion pattern (SceneSegment, SceneList)
- Caching/resume via database lookup

**Used by:** Module Builder (scene_splitter), Phase Builder (Phase 1)

---

### Skill 13 — faster-whisper (CTranslate2)

**File:** `.github/skills/faster-whisper/SKILL.md`

**Purpose:** Speech-to-text transcription with word-level timestamps.

**Provides:**

- Model loading (size selection, CPU/GPU, int8 quantization)
- Transcription API (`word_timestamps=True` mandatory)
- Word-level timestamp extraction + DTO conversion
- Confidence handling (segment-level and word-level)
- Empty transcript handling (valid state, scoring defaults to 0)
- Audio input requirements (16kHz mono WAV via FFmpeg)
- Model size vs speed vs accuracy tradeoff table

**Used by:** Module Builder (transcription), Phase Builder (Phase 2)

---

### Skill 14 — MediaPipe Face Detection

**File:** `.github/skills/mediapipe/SKILL.md`

**Purpose:** Face detection with 2fps sampling, EMA smoothing, and visibility computation.

**Provides:**

- Detector setup (short-range model, 0.7 confidence threshold)
- 2fps sampling strategy with frame skipping
- Normalized coordinate system ([0,1] range, resolution-independent)
- EMA bounding box smoothing (alpha = 0.3)
- Visibility ratio computation (detected frames / total samples)
- Fallback layout decision (gameplay-only when visibility < 10%)
- Gap handling rules (interpolate < 1s, hold 1–5s, fallback > 5s)
- Face detection is OPTIONAL — pipeline must work without it

**Used by:** Module Builder (face_detection), Phase Builder (Phase 2)

---

### Skill 15 — Edge TTS

**File:** `.github/skills/edge-tts/SKILL.md`

**Purpose:** Text-to-speech synthesis with caching for determinism.

**Provides:**

- Async synthesis API (`edge_tts.Communicate`)
- Voice selection table (Christopher/Aria/Guy Neural)
- Word timestamp extraction via `SubMaker`
- Volume normalization (-14 LUFS via FFmpeg loudnorm)
- Caching by text hash (SHA-256, ensures determinism)
- pyttsx3 offline fallback
- Audio duration measurement via ffprobe

**Used by:** Module Builder (tts), Phase Builder (Phase 6)

---

### Skill 16 — Pillow / PIL Thumbnails

**File:** `.github/skills/pillow/SKILL.md`

**Purpose:** Thumbnail generation — frame selection, composition, text overlay.

**Provides:**

- Frame scoring (clarity, color variance, brightness, priority zone)
- Image composition (resize + pad to 1280×720)
- Color/contrast enhancement (+15% saturation, +10% contrast)
- Text overlay with stroke (bold, white text, black 4px outline)
- JPEG output (quality 90, exact 1280×720)
- Full thumbnail generation pipeline

**Used by:** Module Builder (thumbnail), Phase Builder (Phase 7)

---

### Skill 17 — SQLite3

**File:** `.github/skills/sqlite/SKILL.md`

**Purpose:** Database patterns for state management, checkpointing, and idempotency.

**Provides:**

- Connection setup (WAL mode, foreign keys, synchronous=NORMAL)
- 4-table schema reference (videos, scenes, clips, pipeline_runs)
- Parameterized query patterns (mandatory `?` placeholders)
- ON CONFLICT DO NOTHING for idempotency
- State machine transitions (pipeline states, clip states)
- Checkpoint pattern (update + query last_completed_stage)
- Transaction pattern (BEGIN/COMMIT/ROLLBACK)
- Migration execution pattern
- Batch insert performance optimization

**Used by:** Orchestrator, Module Builder (storage), Phase Builder (Phase 0, 8)

---

### Skill 18 — ASS Subtitles

**File:** `.github/skills/ass-subtitle/SKILL.md`

**Purpose:** Advanced SubStation Alpha subtitle generation with karaoke animation.

**Provides:**

- ASS file structure (Script Info, V4+ Styles, Events)
- Style definitions (Transcript and Narration styles)
- ASS color format (`&HAABBGGRR`)
- Word-level karaoke tags (`\kf{duration}`)
- Time format conversion (ms → `H:MM:SS.cc`)
- Safe area positioning (above face region, MarginV=150)
- Two subtitle tracks (Transcript bottom-center, Narration top-center)
- FFmpeg burn-in command (`-vf "ass=file.ass"`)

**Used by:** Module Builder (subtitle), Phase Builder (Phase 6)

---

## 4c. Parallel Development Skills

Parallel development skills support the `scripts/run_parallel.sh` automation system. They encode conflict resolution strategies, post-merge review patterns, documentation synchronization rules, and code quality fix protocols.

---

### Skill 19 — Code Quality Fixer

**File:** `.github/skills/code-quality-fixer/SKILL.md`

**Purpose:** Map quality gate failures to specific fix strategies and relevant skills.

**Provides:**

- 6-check quality gate failure table (import, lint, test, SQL, cross-module, print)
- Fix strategy per failure type with relevant skill reference
- Fix workflow (read failure → lookup strategy → read skill → apply fix → verify)
- Common multi-failure patterns and combined resolution

**Used by:** Code Fixer, Merge Reviewer, Module Builder

---

### Skill 20 — Conflict Resolver

**File:** `.github/skills/conflict-resolver/SKILL.md`

**Purpose:** Architecture-aware Git merge conflict resolution using union strategy.

**Provides:**

- Decision tree for all file types (docs, migrations, `__init__.py`, tests, source)
- 6 resolution patterns (union imports, `__init__.py` exports, both-add-different, both-modify-same, migration conflicts, config YAML)
- Post-resolution validation commands
- Rule: combine ALL phases — later phase is tiebreaker only for truly incompatible changes

**Used by:** Conflict Resolver Agent

---

### Skill 21 — Architecture Reader

**File:** `.github/skills/architecture-reader/SKILL.md`

**Purpose:** Parse and internalize architecture from source-of-truth documents.

**Provides:**

- Source-of-truth document table (7 documents with section counts and purposes)
- Architecture quick reference (pipeline stages, communication rules, DB adapter, identity formulas, output constraints, forbidden tech)
- Processing logic (6-step document loading + cross-reference flow)
- Validation checklist for proposed changes

**Used by:** Code Fixer, Conflict Resolver, Merge Reviewer, Phase Builder

---

### Skill 22 — Doc Standardization

**File:** `.github/skills/doc-standardization/SKILL.md`

**Purpose:** Documentation placement and structural compliance rules.

**Provides:**

- Core doc files table (4 operational documents)
- Architecture reference files table (6 architecture documents)
- Root directory rules (`README.md` only at root, all else in `docs/`)
- Compliance check commands
- Documentation update rules and reference docs table format

**Used by:** Merge Reviewer, Phase Builder

---

### Skill 23 — Docs Sync

**File:** `.github/skills/docs-sync/SKILL.md`

**Purpose:** Synchronize documentation with implementation state after parallel phase merges.

**Provides:**

- 4 files to update: `progress_report.md`, `README.md`, `implementation_roadmap.md`, `config/`
- Per-phase section format template (status, completed tasks, files created, exit criteria)
- How-to-generate content per file
- Verification checklist (completeness, no stubs, config consistency)

**Used by:** Merge Reviewer

---

### Skill 24 — Merge Reviewer

**File:** `.github/skills/merge-reviewer/SKILL.md`

**Purpose:** Post-merge review patterns for parallel phase development.

**Provides:**

- 6-section review checklist (phase completeness, architecture compliance, integration testing, priority rules, fix-and-commit protocol, output format)
- Per-phase verification table (migrations, modules, contracts, tests, config)
- Architecture compliance verification commands
- Priority rules for review focus (latest phase is primary, earlier phases are secondary)

**Used by:** Merge Reviewer Agent, Integration Agent

---

### Skill 25 — Refactor With Architecture

**File:** `.github/skills/refactor-with-architecture/SKILL.md`

**Purpose:** Refactoring patterns that respect all architectural constraints.

**Provides:**

- Allowed vs forbidden refactoring tables with constraint reasons
- 7-point validation checklist (tests, boundaries, DTOs, adapter, determinism, imports, config)
- Destination rules table (where to move extracted code)
- Pre-flight and post-flight validation commands

**Used by:** Refactor Agent, Code Fixer Agent

---

### Skill 26 — Repo Structure Analysis

**File:** `.github/skills/repo-structure-analysis/SKILL.md`

**Purpose:** Audit repository layout against canonical target structure.

**Provides:**

- Full canonical target directory tree (16 module directories, all top-level directories)
- 5-step audit workflow (list root → scan modules → check contracts → check configs → classify violations)
- Classification rules table (wrong location, missing directory, stray doc, auto-generated artifact)
- Root allowed files list (`run_pipeline.py`, `README.md`, `.gitignore`, `pyproject.toml`)

**Used by:** Refactor Agent, Merge Reviewer, Phase Builder

---

## 5. Skill Composition Model

### Composition Rules

1. **Agents declare which skills they use** — Listed in agent `.agent.md` body
2. **Skills are loaded on-demand** — Agent reads skill SKILL.md only when task requires it
3. **Skills are stateless** — No memory between invocations, no file writes
4. **Skills are composable** — An agent may load 2–5 skills per task
5. **Skills never call other skills** — Flat composition, no skill chains

### Composition Matrix — Architectural Skills

| Agent             | DTO | Pipeline | Determinism | Idempotency | Modularity | Failure | Token Opt | Config | Logging | Testing |
| ----------------- | --- | -------- | ----------- | ----------- | ---------- | ------- | --------- | ------ | ------- | ------- |
| Phase Builder     | ✅  | ✅       | ✅          | ✅          | ✅         | ✅      | ✅        | ✅     | ✅      | ✅      |
| Orchestrator      | ✅  | ✅       | —           | ✅          | —          | ✅      | ✅        | —      | —       | —       |
| DTO Guardian      | ✅  | —        | ✅          | —           | ✅         | —       | —         | —      | —       | —       |
| Module Builder    | ✅  | —        | ✅          | —           | ✅         | —       | —         | ✅     | ✅      | ✅      |
| Integration       | ✅  | ✅       | —           | ✅          | —          | ✅      | —         | —      | —       | ✅      |
| Refactor          | —   | —        | ✅          | —           | ✅         | —       | —         | —      | —       | ✅      |
| Code Fixer        | ✅  | —        | ✅          | —           | ✅         | —       | —         | ✅     | ✅      | ✅      |
| Conflict Resolver | —   | —        | —           | —           | ✅         | —       | —         | —      | —       | —       |
| Merge Reviewer    | ✅  | ✅       | ✅          | ✅          | ✅         | —       | —         | ✅     | ✅      | ✅      |

### Composition Matrix — Parallel Development Skills

| Agent             | Code Quality Fixer | Conflict Resolver | Architecture Reader | Doc Standardization | Docs Sync | Merge Reviewer | Refactor w/ Arch | Repo Structure |
| ----------------- | ------------------ | ----------------- | ------------------- | ------------------- | --------- | -------------- | ---------------- | -------------- |
| Phase Builder     | —                  | —                 | ✅                  | ✅                  | —         | —              | —                | ✅             |
| Orchestrator      | —                  | —                 | ✅                  | —                   | —         | —              | —                | —              |
| DTO Guardian      | —                  | —                 | —                   | —                   | —         | —              | —                | —              |
| Module Builder    | ✅                 | —                 | —                   | —                   | —         | —              | —                | —              |
| Integration       | —                  | —                 | ✅                  | —                   | —         | ✅             | —                | —              |
| Refactor          | —                  | —                 | ✅                  | —                   | —         | —              | ✅               | ✅             |
| Code Fixer        | ✅                 | —                 | ✅                  | —                   | —         | —              | ✅               | —              |
| Conflict Resolver | —                  | ✅                | ✅                  | —                   | —         | —              | —                | —              |
| Merge Reviewer    | ✅                 | —                 | ✅                  | ✅                  | ✅        | ✅             | —                | ✅             |

### Composition Matrix — Technical Skills

Technical skills are loaded by agents based on which **module** they are building. The matrix maps modules to required technical skills.

| Module Target  | FFmpeg | PySceneDetect | faster-whisper | MediaPipe | Edge TTS | Pillow | SQLite | ASS Subtitle |
| -------------- | ------ | ------------- | -------------- | --------- | -------- | ------ | ------ | ------------ |
| ingestion      | ✅     | —             | —              | —         | —        | —      | —      | —            |
| scene_splitter | —      | ✅            | —              | —         | —        | —      | —      | —            |
| transcription  | ✅     | —             | ✅             | —         | —        | —      | —      | —            |
| face_detection | ✅     | —             | —              | ✅        | —        | —      | —      | —            |
| scoring        | ✅     | —             | —              | —         | —        | —      | —      | —            |
| clip_builder   | —      | —             | —              | —         | —        | —      | —      | —            |
| hook_generator | —      | —             | —              | —         | —        | —      | —      | —            |
| tts            | ✅     | —             | —              | —         | ✅       | —      | —      | —            |
| subtitle       | —      | —             | —              | —         | —        | —      | —      | ✅           |
| compositor     | ✅     | —             | —              | —         | —        | —      | —      | —            |
| renderer       | ✅     | —             | —              | —         | —        | —      | —      | ✅           |
| thumbnail      | ✅     | —             | —              | —         | —        | ✅     | —      | —            |
| metadata       | —      | —             | —              | —         | —        | —      | —      | —            |
| storage        | —      | —             | —              | —         | —        | —      | ✅     | —            |
| scheduler      | —      | —             | —              | —         | —        | —      | ✅     | —            |
| publisher      | —      | —             | —              | —         | —        | —      | ✅     | —            |
| orchestrator   | —      | —             | —              | —         | —        | —      | ✅     | —            |

---

## 6. Execution Flow

### Example: Phase Builder implementing Phase 3 (Scoring Engine)

```
1. Load skills: DTO Interpretation + Pipeline Reasoning + Determinism Enforcement
2. From DTO skill: Extract ScoredScene, ScoredSceneList contracts
3. From Pipeline skill: Confirm scoring is stage 5, after face_detection, before clip_builder
4. From Determinism skill: Verify scoring formula uses no randomness, configurable weights
5. Read Phase 3 section from implementation_roadmap.md
6. Implement scoring module in modules/scoring/
7. Validate against loaded skills before committing
```

### Example: DTO Guardian validating a PR

```
1. Load skills: DTO Interpretation + Module Boundary
2. From DTO skill: Load full contract registry (22 DTOs)
3. Scan changed files for DTO usage
4. Validate: no raw dicts, no mutable DTOs, no removed fields
5. Verify: all imports from contracts/ only
6. Report: pass/fail with specific violations
```

### Example: Integration Agent connecting Phase 4 → Phase 6

```
1. Load skills: Pipeline Reasoning + DTO Interpretation + Idempotency + Failure Handling
2. From Pipeline skill: Confirm clip_builder → hook_generator is correct sequence
3. From DTO skill: Verify ClipList output matches hook_generator input
4. From Idempotency skill: Verify clip_id generation is deterministic
5. From Failure skill: Verify per-clip retry logic is in place
6. Write integration test exercising the boundary
```

### Example: Conflict Resolver merging Phase 2 and Phase 3

```
1. Load skills: Conflict Resolver + Modularity + Architecture Reader
2. Run: git diff --name-only --diff-filter=U → list conflicted files
3. For each conflict: identify file type → apply decision tree from Conflict Resolver skill
4. __init__.py conflicts: union of all exports from both phases
5. Source conflicts: combine both additions; later phase tiebreaker only if incompatible
6. Run quality gate checks post-resolution
7. Commit: "fix: resolve merge conflicts for Phase 2 + Phase 3"
```

### Example: Merge Reviewer verifying Phases 2, 3, 4 integration

```
1. Load skills: Merge Reviewer + Architecture Reader + Docs Sync + Code Quality Fixer
2. Read implementation_roadmap.md → extract Phase 2, 3, 4 task checklists
3. Per-phase audit: verify files, migrations, modules, contracts, tests exist
4. Code quality: check frozen DTOs, determinism, logging, DB adapter usage
5. Run full quality gate — fix any failures via Code Quality Fixer skill
6. Sync docs: update progress_report.md, README.md, implementation_roadmap.md
7. Generate: per-phase completion summary
```

---

## 7. Token Optimization Strategy

### Problem

The full documentation set is ~25,000 tokens:

- `architecture.md`: ~8,000 tokens
- `implementation_roadmap.md`: ~10,000 tokens
- `dto_contracts.md`: ~5,000 tokens
- `orchestrator_spec.md`: ~3,000 tokens

Reading all four for every agent invocation wastes 80%+ of those tokens on irrelevant sections.

### Solution: Skill-Based Progressive Loading

```
Level 1 — Skill Discovery (~100 tokens)
  Agent reads skill name + description
  Decides which skills are relevant to current task

Level 2 — Skill Loading (~300–500 tokens per skill)
  Agent reads SKILL.md body
  Gets focused, pre-digested rules for the domain

Level 3 — Doc Deep-Dive (only if needed)
  Agent reads specific doc section via skill reference
  Example: "See docs/dto_contracts.md Section 4 for full ScoredScene definition"
```

### Rules for Agents

1. **Load skills first, docs second** — Skills contain 90% of what you need
2. **Never read full docs** — Read only the section referenced by the skill
3. **Cache within session** — If you loaded a skill, don't reload it
4. **Use subagents for exploration** — Delegate doc reads to `Explore` subagent to keep main context clean
5. **Compress when passing context** — Pass DTO names and constraints, not full definitions

---

## 8. Integration with Copilot

### Relationship to `.github/copilot-instructions.md`

The `copilot-instructions.md` file is the **always-on** baseline that applies to every Copilot interaction in this workspace. It contains:

- Hard architectural constraints
- Forbidden technologies
- Repository structure
- Development rules
- Migration naming

**Agents and skills build ON TOP of this baseline.** They do not duplicate it — they extend it with task-specific enforcement.

```
copilot-instructions.md    → Always loaded, every interaction
  └── phase-builder.agent  → Loaded when implementing a phase
       ├── dto skill       → Loaded on-demand for DTO work
       ├── pipeline skill  → Loaded on-demand for stage ordering
       └── testing skill   → Loaded on-demand for test generation
```

### Relationship to `phase-builder.agent.md`

The Phase Builder is the **primary execution agent**. Other agents are specialized validators and builders that either:

- **Support** the Phase Builder (DTO Guardian validates its output)
- **Complement** the Phase Builder (Module Builder for single-module work)
- **Follow** the Phase Builder (Integration Agent wires modules together)

### How Skills Integrate

Skills are stored in `.github/skills/{name}/SKILL.md` and are auto-discovered by Copilot via their `description` field. When an agent needs domain knowledge, it references the skill by name, and Copilot loads the SKILL.md on demand.

---

## 9. Anti-Patterns

### Agent Anti-Patterns

| Anti-Pattern            | Problem                                                 | Fix                                                        |
| ----------------------- | ------------------------------------------------------- | ---------------------------------------------------------- |
| **Swiss-army agent**    | One agent does everything; loses focus, burns context   | Split into focused agents (build vs validate vs integrate) |
| **Agent duplication**   | Two agents enforce the same rule differently            | Extract shared logic into a skill                          |
| **Doc-reading agent**   | Agent spends 50% of context re-reading architecture.md  | Use skills for pre-digested knowledge                      |
| **Over-agentization**   | Creating an agent for every small task                  | Only create agents for recurring, multi-step workflows     |
| **Circular delegation** | Agent A delegates to Agent B, which delegates back to A | Define clear ownership boundaries                          |

### Skill Anti-Patterns

| Anti-Pattern           | Problem                                         | Fix                                                   |
| ---------------------- | ----------------------------------------------- | ----------------------------------------------------- |
| **Kitchen-sink skill** | One skill tries to cover everything             | Split by domain (DTO, pipeline, determinism)          |
| **Stale skill**        | Skill references constraints that changed       | Skills must reference source docs, not duplicate them |
| **Overlapping skills** | Two skills both define DTO validation rules     | One canonical skill per domain                        |
| **Executable skill**   | Skill contains code that runs or modifies files | Skills are pure knowledge — agents execute            |
| **Mandatory loading**  | All skills loaded for every task                | Load only skills relevant to the current task         |

### Coupling Anti-Patterns

| Anti-Pattern        | Problem                                               | Fix                                  |
| ------------------- | ----------------------------------------------------- | ------------------------------------ |
| **Hidden coupling** | Agent reads another module's internal files           | Enforce module boundary skill        |
| **DTO bypass**      | Module passes raw dict instead of frozen dataclass    | DTO Guardian catches this            |
| **Import leak**     | Module A imports Module B's internal function         | Modularity skill flags this          |
| **Config scatter**  | Thresholds hardcoded in module instead of config.yaml | Config validation skill detects this |

---

## 10. Parallel Development Integration

This agent/skill system is designed to support **parallel development** — multiple phases implemented simultaneously by independent AI agents. See `docs/PARALLEL_DEV.md` for the full orchestration guide.

### How Agents Participate

| Agent               | Parallel Dev Role                                              |
| ------------------- | -------------------------------------------------------------- |
| `phase-builder`     | Primary agent — one instance per phase or per group            |
| `integration`       | Post-merge verification on the integration branch              |
| `module-builder`    | Quality gate remediation when violations are detected          |
| `dto-guardian`      | Validates DTO contracts before cross-branch merges             |
| `refactor`          | Code cleanup after quality gate failures                       |
| `orchestrator`      | Wires newly-merged modules — runs LAST after all phases merge  |
| `code-fixer`        | Fixes quality gate and integration check failures autonomously |
| `conflict-resolver` | Resolves Git merge conflicts using union strategy              |
| `merge-reviewer`    | Post-merge integration review — verifies all phases complete   |

### How Skills Enable Safe Parallelism

**Architectural skills:**

- **`dto` skill** — Guarantees all parallel agents produce compatible DTO definitions
- **`modularity` skill** — Enforces file ownership boundaries so parallel agents cannot collide
- **`pipeline` skill** — Ensures agents respect the 16-stage ordering constraint
- **`idempotency` skill** — Ensures content-addressable IDs are consistent across agents
- **`testing` skill** — Each agent produces tests that work without other modules present

**Parallel development skills:**

- **`code-quality-fixer` skill** — Maps quality gate failures to fix strategies with relevant skill references
- **`conflict-resolver` skill** — Decision tree for resolving merge conflicts by file type (union strategy)
- **`architecture-reader` skill** — Quick reference to architecture docs for pre-merge/post-merge validation
- **`doc-standardization` skill** — Prevents documentation sprawl by enforcing canonical placement rules
- **`docs-sync` skill** — Synchronizes `progress_report.md`, `README.md`, and `implementation_roadmap.md` after merges
- **`merge-reviewer` skill** — 6-section review checklist for post-merge integration validation
- **`refactor-with-architecture` skill** — Allowed/forbidden refactoring tables to prevent architectural violations
- **`repo-structure-analysis` skill** — Audits repository layout against canonical target structure

### Execution Script

```bash
# See scripts/run_parallel.sh for automated orchestration
./scripts/run_parallel.sh start --mode=3 2 3 4 7 8
```

The script handles worktree creation, agent spawning, model routing, quality gates, branch merging, and remediation. Three modes balance speed, cost, and merge complexity.
