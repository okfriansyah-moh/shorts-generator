# Shorts Factory — Copilot Instructions

> These instructions enforce the architectural constraints defined in the project documentation.
> Violations are not acceptable and must not be introduced, even partially.

---

## Reference Documents

| Document                         | Purpose                                                                                                         |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `docs/architecture.md`           | Master reference — 18-section system architecture, module breakdown, pipeline flow, scoring design, data model  |
| `docs/implementation_roadmap.md` | 11-phase implementation roadmap (Phase 0–10) with schemas, algorithms, exit criteria, priority layers           |
| `docs/orchestrator_spec.md`      | 15-section orchestrator specification — execution model, checkpointing, resume, idempotency, failure handling   |
| `docs/dto_contracts.md`          | 22 DTO definitions with all fields/types/constraints, cross-module dependency matrix, validation rules          |
| `docs/db_adapter_spec.md`        | Database abstraction layer — adapter interface, SQL compatibility, migration strategy, engine portability       |
| `docs/startup_guide.md`          | Production deployment playbook — system requirements, environment setup, dependencies, launch checklist         |
| `docs/progress_report.md`        | Current implementation status — completed work, test results, remaining items, phase-by-phase progress tracking |
| `docs/PARALLEL_DEV.md`           | Parallel development orchestration guide — 3-mode execution system, phase grouping, token optimization          |
| `docs/AGENTS_AND_SKILLS.md`      | Agent/skill system — 9 agents, 26 skills, composition matrices, token optimization, parallel dev integration    |
| `contracts/`                     | Frozen dataclass DTO definitions — all modules MUST use these, not upstream sources or raw dicts                |
| `config/`                        | YAML configuration files — all thresholds, paths, and tunable parameters live here                              |

When generating code, refer to these documents for exact schemas, DTO definitions, interfaces, and algorithms. Do not invent new structures that contradict them.

---

## Architecture Invariants

### Modular Monolith

- Single process, single repo, single SQLite database
- Entry point: `run_pipeline.py`
- No microservices, no inter-process communication, no network calls between modules

### Module Communication

- Modules communicate **only** through frozen dataclass DTOs defined in `contracts/`
- No direct imports between module internals — only public contracts
- No raw dicts, no untyped data crossing module boundaries
- See `docs/dto_contracts.md` for all 22 DTO definitions and validation rules

### Pipeline Architecture

16 stages in **strict sequential order** — never reorder, skip, or parallelize stages:

```
ingestion → scene_splitter → transcription → face_detection → scoring →
clip_builder → hook_generator → tts → subtitle → compositor → renderer →
thumbnail → metadata → storage → scheduler → publisher
```

### Determinism

- Same input + same config = identical output. Always.
- No `random`, no non-deterministic model inference, no network-dependent behavior
- All scoring is rule-based and template-driven — no LLMs, no ML models for decisions
- TTS output is cached by input text hash to ensure reproducibility

### Idempotency

- Running the pipeline twice on the same input produces no duplicates and no corruption
- All IDs are content-addressable:
  - `video_id = SHA256(first_10MB + file_size)[:16]`
  - `scene_id = {video_id}_{start_ms}_{end_ms}`
  - `clip_id = SHA256(video_id + start_ms + end_ms)[:16]`
- All SQL uses portable `INSERT ... ON CONFLICT DO NOTHING` semantics

### State Authority

- **The database is the single source of truth** for all pipeline state
- 4 tables: `videos`, `clips`, `scenes`, `pipeline_runs`
- Pipeline run states: `started → analyzing → building → completed | partial | failed`
- Clip states: `generated → queued → scheduled → published | failed`
- No in-memory-only state that isn't backed by the database

### Database Adapter

- **All database access goes through `database/adapter.py`** — the single entry point
- Modules under `modules/` **MUST NOT** import `sqlite3`, `psycopg2`, or any database driver
- Modules **MUST NOT** contain SQL strings or execute queries
- The adapter accepts and returns frozen dataclass DTOs — no raw rows, no dicts
- Only the orchestrator calls the adapter — modules never touch the database
- All SQL uses portable syntax (`ON CONFLICT DO NOTHING`, not `INSERT OR IGNORE`)
- See `docs/db_adapter_spec.md` for the full adapter interface and migration strategy

### Orchestrator Rules

- The orchestrator is the **only** component that calls modules — modules never call each other
- Checkpoint after every stage completion (write to database)
- Resume from last successful checkpoint on restart
- See `docs/orchestrator_spec.md` for the full execution model

### Orchestrator Authority Rule

The orchestrator is the **ONLY** component that:

- Calls modules (modules never call each other)
- Manages execution order (the 16-stage pipeline sequence)
- Performs checkpointing (writes `last_completed_stage` after each stage)
- Writes to the database (via `database/adapter.py`)
- Routes DTOs between modules (passes output of stage N as input to stage N+1)
- Handles failures (decides retry, skip, or abort)

Modules MUST:

- Be **pure functions** — accept DTOs, return DTOs, no side effects on shared state
- **Not call the database** — no imports from `database/`, no SQL, no adapter calls
- **Not call other modules** — no imports from `modules.*` (only `contracts/`)
- **Not manage their own state** — all state lives in the database, managed by the orchestrator
- **Not perform checkpointing** — only the orchestrator decides when to persist progress

### Clip Constraints

- Duration: 30–60 seconds
- Resolution: 1080×1920 (9:16 vertical)
- Layout: gameplay (top 65%) + face cam (bottom 35%)
- Output: H.264 MP4
- Thumbnails: JPEG 1280×720
- Batch target: 10–15 clips per video

### Face Detection

- Face detection is **optional** — pipeline must work without it
- If face data is unavailable, compositor uses gameplay-only layout
- MediaPipe at 2fps sampling rate

---

## Forbidden Technologies

Never introduce any of these — they violate core design principles:

| Category     | Forbidden                                                           |
| ------------ | ------------------------------------------------------------------- |
| Architecture | Microservices, Kafka, RabbitMQ, Kubernetes, Docker orchestration    |
| Databases    | MongoDB, Redis, any distributed database                            |
| AI/ML        | OpenAI API, Anthropic API, LangChain, AutoGPT, CrewAI, any paid LLM |
| Cloud        | AWS, GCP, Azure, any cloud compute or storage                       |
| Runtime      | Agent loops, autonomous planners, event-driven architectures        |

### Database Engine Policy

- **SQLite is the primary runtime database.** All development and testing uses SQLite.
- **PostgreSQL is allowed ONLY as a future alternative via `database/adapter.py`.** See `docs/db_adapter_spec.md` Section 10.
- **Modules MUST remain database-agnostic.** No module may reference any specific database engine.
- Direct use of `psycopg2`, `asyncpg`, or any PostgreSQL driver in `modules/` is forbidden.
- The adapter is the **sole abstraction boundary** — switching engines requires changes only in `database/`.

---

## Repository Structure

```
shorts-generator/
├── run_pipeline.py          # Single entry point
├── contracts/               # DTO definitions (frozen dataclasses)
├── modules/
│   ├── ingestion/
│   ├── scene_splitter/
│   ├── transcription/
│   ├── face_detection/
│   ├── scoring/
│   ├── clip_builder/
│   ├── hook_generator/
│   ├── tts/
│   ├── subtitle/
│   ├── compositor/
│   ├── renderer/
│   ├── thumbnail/
│   ├── metadata/
│   ├── storage/
│   ├── scheduler/
│   └── publisher/
├── orchestrator/            # Pipeline orchestration + checkpointing
├── database/                # DB adapter + engine implementations + migrations
├── config/                  # YAML configuration
├── tests/                   # Unit + integration tests
├── output/                  # Generated clips (gitignored)
└── docs/                    # Architecture + specs
```

---

## Development Rules

1. **Python 3.10+** — Use type hints on all public interfaces
2. **Frozen dataclasses** for all DTOs — no mutable state crossing module boundaries
3. **Each module** gets its own package under `modules/` with `__init__.py` exposing only the public contract
4. **No module may import another module's internals** — only `contracts/` types
5. **FFmpeg** via subprocess for all video/audio processing — no Python video libraries
6. **Database access** through `database/adapter.py` only — no raw SQL in modules, no ORM, no SQLAlchemy
7. **Tests** must be runnable without GPU, without network, and without real video files
8. **Config** via YAML files — no hardcoded paths, thresholds, or magic numbers
9. **Logging** via stdlib `logging` — structured, leveled, no print statements
10. **Module `__init__.py` MUST use relative imports** — `from .X import Y`, NOT `from modules.X.Y import Y`

---

## Phase Isolation Rules (Parallel Development)

During parallel development, each phase owns specific directories. **Agents MUST NOT modify files outside their phase's ownership.**

### Phase-to-Directory Ownership Matrix

| Phase   | Owned Directories                                                                    | May Add to `contracts/` | May Modify `database/` | May Modify `docs/` |
| ------- | ------------------------------------------------------------------------------------ | ----------------------- | ---------------------- | ------------------- |
| Phase 0 | `core/`, `database/`, `config/`, `run_pipeline.py`                                   | Yes (additive)          | **Yes**                | No                  |
| Phase 1 | `modules/ingestion/`, `modules/scene_splitter/`, `tests/unit/test_ingestion.py`, `tests/unit/test_scene_splitter.py` | Yes (additive) | **No** | No |
| Phase 2 | `modules/transcription/`, `modules/face_detection/`, `modules/audio_analysis/`, corresponding tests | Yes (additive) | **No** | No |
| Phase 3 | `modules/scoring/`, `tests/unit/test_scoring.py`                                     | Yes (additive)          | **No**                 | No                  |
| Phase 4 | `modules/clip_builder/`, `tests/unit/test_clip_builder.py`                           | Yes (additive)          | **No**                 | No                  |
| Phase 5 | `modules/compositor/`, `tests/unit/test_compositor.py`                               | Yes (additive)          | **No**                 | No                  |
| Phase 6 | `modules/hook_generator/`, `modules/tts/`, `modules/subtitle/`, `modules/renderer/`, corresponding tests | Yes (additive) | **No** | No |
| Phase 7 | `modules/thumbnail/`, `modules/metadata/`, corresponding tests                       | Yes (additive)          | **No**                 | No                  |
| Phase 8 | `modules/storage/`, `modules/scheduler/`, corresponding tests                        | Yes (additive)          | **No**                 | No                  |
| Phase 9 | `modules/publisher/`, `tests/unit/test_publisher.py`                                 | Yes (additive)          | **No**                 | No                  |

### Phase Isolation Enforcement

- **`database/`** — Only Phase 0 may modify. All other phases treat as read-only.
- **`docs/`** — Read-only for all phases. Documentation sync happens post-merge only.
- **`contracts/`** — Any phase may ADD new DTO files. No phase may modify existing DTO fields.
- **`core/`** — Only Phase 0 may modify. Other phases treat as read-only.
- **Other phase modules** — Never touch modules owned by another phase.
- **Violation of these rules triggers automatic rollback** in the parallel development pipeline.

---

## Performance Targets

For a 1-hour input video on consumer hardware (8-core CPU, 16GB RAM, no GPU):

- **Total pipeline:** 20–30 minutes
- **Peak memory:** ~4GB
- **Disk per batch:** ~2GB (12 clips)
- **Ingestion:** < 10 seconds
- **Scoring:** < 5 seconds

---

## Database Migration Naming

All migration scripts live in `database/migrations/` (or `db/migrations/`) and follow strict timestamp-prefixed naming:

```
YYYYMMDD000NNN_description.sql
```

- `YYYYMMDD` is the date the migration was created
- `000NNN` is a zero-padded 6-digit sequential number for ordering within the same date (starting at `000001`)
- Description uses `snake_case` and starts with the verb: `create_`, `add_`, `alter_`, `drop_`

**Rules:**

- Every migration must use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` for idempotency
- Migration failures are **fatal** — the process must not start with an inconsistent database
- Running migrations multiple times must produce identical state
- Never modify an existing migration file — always create a new numbered migration
- Migrations are sorted lexicographically — the timestamp prefix guarantees correct ordering

**Existing migrations:**

```
20260324000001_create_videos_table.sql
20260324000002_create_scenes_table.sql
20260324000003_create_clips_table.sql
20260324000004_create_pipeline_runs_table.sql
```

**Examples for future migrations:**

```
20260325000001_add_tts_cache_table.sql
20260326000001_add_publish_error_column.sql
20260326000002_create_scheduler_queue_table.sql
```

---

## File Duplication Prevention

**MUST NOT:**

- Create duplicate files with similar names (e.g., `utils.py` and `helpers.py` with overlapping functions)
- Create new utility modules when existing ones already cover the functionality
- Duplicate DTO definitions — all DTOs live in `contracts/` and are defined exactly once
- Copy SQL schemas between migration files — reference the existing table, don't redefine it
- Duplicate configuration defaults — all defaults live in `config.yaml`, not scattered in code
- Create wrapper modules that simply re-export another module's functions

**MUST:**

- Check existing files before creating new ones — use the project structure as the source of truth
- Reuse existing utility functions from `contracts/`, `core/`, and shared helpers
- Place new code in the correct existing module rather than creating a parallel file
- When adding a new module, verify no existing module already handles that responsibility
- Keep one canonical location for each piece of logic — no copies, no forks, no alternatives

---

## Protected Files

These files and directories define system contracts and MUST NOT be modified without explicit instruction:

| Path                      | Reason                                                    |
| ------------------------- | --------------------------------------------------------- |
| `contracts/*`             | Frozen DTO definitions — additive-only versioning         |
| `database/adapter.py`     | Single database entry point — all modules depend on it    |
| `database/migrations/*`   | Immutable migration history — never edit, only append     |
| `docs/dto_contracts.md`   | DTO specification — must match `contracts/` exactly       |
| `docs/db_adapter_spec.md` | Adapter interface spec — must match `database/adapter.py` |

**Rules:**

- Adding new DTOs to `contracts/` is allowed (additive)
- Removing or renaming existing DTO fields is **never** allowed
- Adding new adapter functions to `database/adapter.py` is allowed
- All adapter functions MUST accept and return frozen dataclass DTOs — no raw rows, no dicts

---

## Non-Goals

Do not implement or suggest any of the following:

- Web UI, dashboard, or mobile interface
- Real-time or streaming processing
- Multi-language support
- Content moderation AI
- Cloud deployment or scaling
- Database migration to PostgreSQL outside the adapter pattern (see `docs/db_adapter_spec.md`)
