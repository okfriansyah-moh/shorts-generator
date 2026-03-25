# Shorts Factory

An autonomous, local-only content production pipeline that transforms long-form gameplay recordings into fully packaged YouTube Shorts — ready for scheduled publishing with zero cloud cost.

## What It Does

**Input:** 1 long-form gameplay video (30–120 minutes)

**Output:** 10–15 YouTube Shorts, each including:

- Vertical video (1080×1920, 30–60s, H.264)
- Composite layout — gameplay (top 65%) + face cam (bottom 35%)
- TTS narration + burned-in subtitles (ASS format, word-level karaoke)
- Thumbnail (1280×720, face + text overlay)
- Title, description, and tags
- Scheduled publish queue entry

## Architecture

Modular monolith — single process, single SQLite database, 16 pipeline stages in strict sequential order:

```
ingestion → scene_splitter → transcription → face_detection → scoring →
clip_builder → hook_generator → tts → subtitle → compositor → renderer →
thumbnail → metadata → storage → scheduler → publisher
```

Every stage communicates via frozen dataclass DTOs defined in `contracts/`. The orchestrator is the only component that calls modules, manages execution order, performs checkpointing, and writes to the database.

See [docs/architecture.md](docs/architecture.md) for the full 18-section design document.

## Design Principles

| Principle          | Description                                                       |
| ------------------ | ----------------------------------------------------------------- |
| Deterministic      | Same input + same config = identical output, every time           |
| Idempotent         | Safe to rerun — content-addressable IDs, `ON CONFLICT DO NOTHING` |
| Modular Monolith   | Single process, 16 isolated modules, frozen DTO contracts         |
| Zero Cost          | Local execution only — no paid APIs, no cloud                     |
| Database Authority | SQLite is the single source of truth for all pipeline state       |
| Orchestrator-Only  | Only the orchestrator calls modules and writes to the database    |
| Checkpoint/Resume  | Pipeline resumes from the last successful stage on restart        |

## Pipeline Modules

| #   | Module         | Purpose                                                  |
| --- | -------------- | -------------------------------------------------------- |
| 1   | Ingestion      | Validate video, compute SHA256 fingerprint               |
| 2   | Scene Splitter | Detect scene boundaries (PySceneDetect, 3–20s segments)  |
| 3   | Transcription  | Word-level speech-to-text (faster-whisper, CTranslate2)  |
| 4   | Face Detection | Track face position (MediaPipe, 2fps sampling, optional) |
| 5   | Scoring        | Rule-based scene ranking (keywords, audio, face, motion) |
| 6   | Clip Builder   | Merge top-scored scenes into 30–60s clips                |
| 7   | Hook Generator | Template-based narration scripts                         |
| 8   | TTS            | Speech synthesis (Edge TTS, cached by text hash)         |
| 9   | Subtitle       | Word-level timed subtitles (ASS format, karaoke)         |
| 10  | Compositor     | Face + gameplay 9:16 vertical layout                     |
| 11  | Renderer       | Final MP4 with all layers merged (FFmpeg)                |
| 12  | Thumbnail      | Frame selection + text overlay (Pillow)                  |
| 13  | Metadata       | Title, description, tags generation                      |
| 14  | Storage        | SQLite + filesystem persistence                          |
| 15  | Scheduler      | Daily publish date assignment                            |
| 16  | Publisher      | YouTube upload via API                                   |

## Usage

```bash
python run_pipeline.py input.mp4
```

Output:

```
output/
├── {video_id}/
│   ├── clips/
│   │   ├── {clip_id}/
│   │   │   ├── final.mp4
│   │   │   ├── thumbnail.jpg
│   │   │   ├── subtitles.ass
│   │   │   ├── narration.wav
│   │   │   └── metadata.json
│   │   └── ...
│   └── pipeline.log
└── shorts.db
```

## Performance Targets

For a 1-hour input video on consumer hardware (8-core CPU, 16GB RAM, no GPU):

| Metric         | Target               |
| -------------- | -------------------- |
| Total pipeline | 20–30 min            |
| Peak memory    | ~4GB                 |
| Disk per batch | ~2GB (12 clips)      |
| Ingestion      | < 10 seconds         |
| Scoring        | < 5 seconds          |
| Output         | 10–15 Shorts per run |

## Tech Stack

- **Python 3.10+** — type hints on all public interfaces
- **FFmpeg** — all video/audio processing (subprocess, no Python video libraries)
- **PySceneDetect** — scene boundary detection
- **faster-whisper** — speech transcription (CTranslate2)
- **MediaPipe** — face detection and tracking
- **Edge TTS** — text-to-speech synthesis
- **Pillow** — thumbnail generation
- **SQLite** — pipeline state, clip lifecycle, and queue management

## Project Structure

```
shorts-generator/
├── run_pipeline.py              # Single entry point
├── contracts/                   # Frozen dataclass DTO definitions
│   ├── ingestion.py             # IngestionResult (Phase 1)
│   ├── scene.py                 # SceneSegment, SceneList (Phase 1)
│   ├── transcript.py            # Word, TranscriptSegment, Transcript (Phase 2)
│   ├── face.py                  # FaceBBox, SceneFaceData, FaceDetectionResult (Phase 2)
│   └── audio.py                 # SceneAudioEnergy, AudioEnergyData (Phase 2)
├── modules/
│   ├── ingestion/               # Video validation + SHA-256 fingerprinting [Phase 1]
│   ├── scene_splitter/          # Scene boundary detection [Phase 1]
│   ├── transcription/           # Speech-to-text, word-level timestamps [Phase 2]
│   ├── face_detection/          # Face tracking, EMA smoothing [Phase 2]
│   ├── audio_analysis/          # Per-scene RMS energy extraction [Phase 2]
│   ├── scoring/                 # Rule-based scene ranking [Phase 3+]
│   ├── clip_builder/            # Scene → clip assembly [Phase 4+]
│   ├── hook_generator/          # Narration script templates [Phase 5+]
│   ├── tts/                     # Text-to-speech synthesis [Phase 6+]
│   ├── subtitle/                # ASS subtitle generation [Phase 6+]
│   ├── compositor/              # 9:16 layout composition [Phase 7+]
│   ├── renderer/                # Final MP4 rendering [Phase 7+]
│   ├── thumbnail/               # Thumbnail generation [Phase 8+]
│   ├── metadata/                # Title/description/tags [Phase 8+]
│   ├── storage/                 # Filesystem persistence [Phase 9+]
│   ├── scheduler/               # Publish date assignment [Phase 9+]
│   └── publisher/               # YouTube upload [Phase 9+]
├── core/
│   ├── config.py                # YAML config loader + env overrides
│   ├── logging.py               # Structured JSON logging
│   ├── dependencies.py          # FFmpeg/FFprobe/Python checks
│   └── orchestrator.py          # Pipeline orchestrator (ingestion + scene_splitter wired)
├── database/                    # DB adapter + migrations
│   ├── adapter.py               # Single entry point for all DB access
│   ├── connection.py            # SQLite WAL mode + migration runner
│   └── migrations/              # Timestamped SQL migrations (4 tables)
├── config/
│   └── config.yaml              # All default configuration values
├── scripts/
│   └── run_parallel.sh          # Parallel development orchestrator
├── tests/                       # Unit + integration tests (120 passing)
│   ├── unit/                    # Module unit tests
│   └── integration/             # Pipeline integration tests
├── output/                      # Generated clips (gitignored)
├── docs/                        # Architecture + specifications
│   ├── architecture.md          # 18-section system architecture
│   ├── implementation_roadmap.md # 11-phase roadmap (Phase 0–10)
│   ├── orchestrator_spec.md     # Orchestrator execution model
│   ├── dto_contracts.md         # 22 DTO definitions + validation rules
│   ├── db_adapter_spec.md       # Database abstraction layer spec
│   ├── progress_report.md       # Per-phase implementation status
│   ├── PARALLEL_DEV.md          # Parallel development guide (3 modes)
│   └── AGENTS_AND_SKILLS.md     # Agent/skill system documentation
└── .github/
    ├── copilot-instructions.md  # Hard architectural constraints
    ├── agents/                  # 9 AI agent definitions
    └── skills/                  # 26 domain skill definitions
```

## Implementation Status

| Phase | Name                    | Status       | Key Deliverables                                      |
| ----- | ----------------------- | ------------ | ----------------------------------------------------- |
| 0     | Core Infrastructure     | ✅ Complete  | Config, logging, DB migrations, FFmpeg checks         |
| 1     | Core Pipeline Skeleton  | ✅ Complete  | Ingestion, scene splitting, orchestrator wiring       |
| 2     | Signal Extraction       | ✅ Complete  | Transcription, face detection, audio analysis         |
| 3–10  | Scoring through Publish | ⏳ Pending   | Full pipeline stages                                  |

## Development System

### AI Agent Pipeline

Development is driven by a **4-step agent pipeline** that runs for every phase implementation:

```
┌────────────────┐     ┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│ phase-builder  │ ──→ │ dto-guardian   │ ──→ │ integration   │ ──→ │ refactor      │
│ (implement)    │     │ (validate DTOs)│     │ (validate     │     │ (fix quality  │
│                │     │               │     │  wiring)      │     │  gates)       │
└────────────────┘     └───────────────┘     └───────────────┘     └───────────────┘
```

| Agent               | Purpose                                                 |
| ------------------- | ------------------------------------------------------- |
| `phase-builder`     | Implement phase modules, tests, and contracts           |
| `dto-guardian`      | Validate frozen DTOs match `contracts/` definitions     |
| `integration`       | Validate module wiring, no cross-module imports         |
| `refactor`          | Fix quality gate failures without changing architecture |
| `module-builder`    | Build a single module (not a full phase)                |
| `orchestrator`      | Build/review the pipeline orchestrator                  |
| `conflict-resolver` | Architecture-aware merge conflict resolution            |
| `merge-reviewer`    | Post-merge integration review                           |
| `code-fixer`        | Automated quality gate remediation                      |

### 26 Domain Skills

Skills provide compressed, tested instructions for specific domains. Agents load skills instead of reading full documentation (~90% token savings):

**Architectural** (loaded by every agent): `dto`, `pipeline`, `modularity`, `determinism`, `idempotency`, `testing`, `failure`, `config-validation`, `logging`

**Technical** (loaded per phase): `ffmpeg`, `pyscenedetect`, `faster-whisper`, `mediapipe`, `edge-tts`, `pillow`, `sqlite`, `ass-subtitle`

**Development** (loaded on demand): `code-quality-fixer`, `conflict-resolver`, `merge-reviewer`, `docs-sync`, `doc-standardization`, `repo-structure-analysis`, `refactor-with-architecture`, `token-optimization`, `architecture-reader`

### Parallel Development

Three execution modes for running multiple phases simultaneously:

```bash
# Mode 1 — Full Parallel (max speed, one agent pipeline per phase)
./scripts/run_parallel.sh start --mode=1 2 7

# Mode 2 — Token-Optimized (single session, sequential phases)
./scripts/run_parallel.sh start --mode=2 2 3 4

# Mode 3 — Hybrid (default: parallel across groups, sequential within)
./scripts/run_parallel.sh start 2 3 4 7 8
```

Each mode uses the same agent pipeline (phase-builder → dto-guardian → integration → refactor) and enforces:

- **9 quality gates**: imports, lint, tests, raw SQL, cross-module, print statements, DTO validation, orchestrator integrity, and protected file checks
- **Skill-based execution**: agents use `.github/skills/` instead of reading full docs
- **Architecture-aware merging**: integration agent resolves conflicts (no `git checkout --theirs`)
- **Agent execution logging**: every step logged with timestamps to `agent-chain.log`

See [docs/PARALLEL_DEV.md](docs/PARALLEL_DEV.md) for the full guide.

### Quality Gates

Every phase runs through these automated checks:

| Gate                   | Validates                                            | Blocking |
| ---------------------- | ---------------------------------------------------- | -------- |
| Import check           | All modules importable                               | Yes      |
| Lint check             | No lint errors (ruff/flake8)                         | Yes      |
| Test check             | `pytest tests/` passes                               | Yes      |
| SQL check              | No `sqlite3`/`psycopg2` imports in `modules/`        | Yes      |
| Cross-module check     | No `from modules.X` in other modules                 | Yes      |
| Print check            | No `print()` in `modules/`                           | Yes      |
| DTO validation         | All DTOs frozen, no raw dicts crossing boundaries    | Yes      |
| Orchestrator integrity | No database access outside orchestrator              | Yes      |
| Protected files        | Warns if `contracts/`, `database/`, `docs/` modified | Advisory |

## Key Invariants

- **Determinism**: same input + same config = identical output (no `random`, no LLMs for decisions)
- **Idempotency**: content-addressable IDs (`video_id = SHA256(first_10MB + file_size)[:16]`), all SQL uses `ON CONFLICT DO NOTHING`
- **Module isolation**: modules communicate only through frozen DTOs in `contracts/`, no cross-module imports
- **Orchestrator authority**: only the orchestrator calls modules, writes to the database, and handles checkpointing
- **Database adapter**: all database access through `database/adapter.py` — modules never touch the database directly

## Documentation

| Document                                                    | Purpose                                          |
| ----------------------------------------------------------- | ------------------------------------------------ |
| [architecture.md](docs/architecture.md)                     | 18-section system architecture                   |
| [implementation_roadmap.md](docs/implementation_roadmap.md) | 11-phase roadmap (Phase 0–10) with exit criteria |
| [orchestrator_spec.md](docs/orchestrator_spec.md)           | Orchestrator execution model + checkpointing     |
| [dto_contracts.md](docs/dto_contracts.md)                   | 22 DTO definitions with validation rules         |
| [db_adapter_spec.md](docs/db_adapter_spec.md)               | Database abstraction layer + migration strategy  |
| [PARALLEL_DEV.md](docs/PARALLEL_DEV.md)                     | 3-mode parallel development guide                |
| [AGENTS_AND_SKILLS.md](docs/AGENTS_AND_SKILLS.md)           | 9 agents, 26 skills, composition matrices        |

## Non-Goals

- No microservices or distributed systems
- No paid APIs (OpenAI, Anthropic, cloud services)
- No real-time or streaming processing
- No web UI, dashboard, or mobile interface
- No multi-language support
- No cloud deployment or scaling

## License

MIT
