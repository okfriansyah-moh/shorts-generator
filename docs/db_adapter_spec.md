# Shorts Factory — Database Adapter Specification

> This document defines the database abstraction layer that isolates all pipeline modules
> from the underlying database engine. It is a **binding specification** — all database
> access in the system MUST conform to these rules.

---

## 1. Design Philosophy

### Why Abstraction Is Required

The Shorts Factory pipeline has 16 modules that produce and consume data persisted in a relational database. Without an abstraction layer, every module would import `sqlite3` directly, scatter SQL across the codebase, and couple business logic to a specific database engine. This creates three problems:

1. **Portability lock-in** — Switching from SQLite to PostgreSQL would require rewriting every module
2. **SQL duplication** — Identical queries repeated across modules with no single source of truth
3. **Contract violation** — Modules communicating through raw database rows instead of DTOs

The adapter solves all three by placing a single boundary between the application and the database.

### Why SQLite Now

- **Zero infrastructure** — No server, no config, no network. A single file.
- **Zero cost** — Aligns with the $0 operational cost target
- **Sufficient throughput** — The pipeline is single-process, batch-oriented, and write-infrequent. SQLite handles this easily.
- **Stdlib only** — `sqlite3` ships with Python. No external dependency.

### Why PostgreSQL May Be Needed Later

- **Multi-machine execution** — If the pipeline scales to multiple workers, SQLite's single-writer model becomes a bottleneck
- **Concurrent publishing** — The scheduler/publisher stages could benefit from row-level locking
- **Operational tooling** — PostgreSQL provides better monitoring, backup, and recovery tooling at scale

### Core Principle

> **Modules must not know the database.**
>
> No module under `modules/` may import `sqlite3`, `psycopg2`, or any database driver.
> No module may execute SQL. No module may receive raw database rows.
> All database interaction flows through the adapter, which accepts and returns
> frozen dataclass DTOs from `contracts/`.

---

## 2. Database Architecture Overview

### Layered Access Model

```text
┌─────────────────────────────────────────────────────────┐
│  Pipeline Modules (16 stages)                           │
│  ingestion, scene_splitter, transcription, ...          │
│                                                         │
│  ❌ No database imports                                 │
│  ❌ No SQL execution                                   │
│  ✅ Produce/consume frozen DTOs only                   │
└───────────────────┬─────────────────────────────────────┘
                    │ DTOs
                    ▼
┌─────────────────────────────────────────────────────────┐
│  Orchestrator (orchestrator/)                           │
│                                                         │
│  ✅ Calls modules with DTOs                            │
│  ✅ Calls db_adapter with DTOs                         │
│  ✅ Owns checkpoint logic                              │
│  ✅ Only component that touches the adapter            │
└───────────────────┬─────────────────────────────────────┘
                    │ DTOs → SQL
                    ▼
┌─────────────────────────────────────────────────────────┐
│  DB Adapter (database/adapter.py)                       │
│                                                         │
│  ✅ Single entry point for all database I/O            │
│  ✅ Translates DTOs ↔ SQL                              │
│  ✅ Engine-agnostic interface                          │
│  ✅ Enforces idempotency and state transitions         │
└───────────────────┬─────────────────────────────────────┘
                    │ SQL
                    ▼
┌─────────────────────────────────────────────────────────┐
│  Database Engine                                        │
│                                                         │
│  Current:  SQLite  (database/engines/sqlite_engine.py)  │
│  Future:   Postgres (database/engines/postgres_engine.py│)
└─────────────────────────────────────────────────────────┘
```

### Directory Structure

```
database/
├── adapter.py              # Public interface — the ONLY import external code uses
├── engines/
│   ├── __init__.py
│   ├── base.py             # Abstract base class (DatabaseEngine protocol)
│   ├── sqlite_engine.py    # SQLite implementation
│   └── postgres_engine.py  # Future PostgreSQL implementation (not yet created)
├── migrations/
│   ├── 20260324000001_create_videos_table.sql
│   ├── 20260324000002_create_scenes_table.sql
│   ├── 20260324000003_create_clips_table.sql
│   └── 20260324000004_create_pipeline_runs_table.sql
└── __init__.py
```

### Access Rules

| Component         | May import `database.adapter`? | May import `database.engines.*`? | May import `sqlite3` directly? |
| ----------------- | ------------------------------ | -------------------------------- | ------------------------------ |
| `orchestrator/`   | ✅ Yes                         | ❌ No                            | ❌ No                          |
| `modules/*`       | ❌ No                          | ❌ No                            | ❌ No                          |
| `run_pipeline.py` | ✅ Yes (initialization only)   | ❌ No                            | ❌ No                          |
| `database/`       | N/A (is the adapter)           | ✅ Yes (internal)                | ✅ Yes (engine impl only)      |
| `tests/`          | ✅ Yes                         | ✅ Yes (for engine-level tests)  | ✅ Yes (for test fixtures)     |

---

## 3. DB Adapter Interface

The adapter exposes a flat set of functions. Each function has a clearly defined input DTO, output DTO, and set of constraints. The adapter is stateful only in that it holds a database connection; all operations are otherwise pure transformations of DTOs into SQL and SQL results back into DTOs.

### 3.1 Video Operations

#### `insert_video(video: IngestionResult) → None`

| Aspect      | Definition                                                 |
| ----------- | ---------------------------------------------------------- |
| Input DTO   | `IngestionResult` from `contracts/ingestion.py`            |
| Output      | `None` (void — idempotent insert)                          |
| SQL         | `INSERT INTO videos ... ON CONFLICT (video_id) DO NOTHING` |
| Idempotency | Duplicate `video_id` silently ignored                      |
| Constraint  | `video_id` must be 16 hex chars, SHA-256 derived           |

#### `get_video(video_id: str) → IngestionResult | None`

| Aspect     | Definition                                                   |
| ---------- | ------------------------------------------------------------ | ----- |
| Input      | `video_id: str` (16 hex chars)                               |
| Output DTO | `IngestionResult                                             | None` |
| SQL        | `SELECT ... FROM videos WHERE video_id = ?`                  |
| Constraint | Returns `None` if not found — no exceptions for missing data |

---

### 3.2 Pipeline Operations

#### `create_pipeline_run(run_id: str, video_id: str) → None`

| Aspect      | Definition                                                      |
| ----------- | --------------------------------------------------------------- |
| Input       | `run_id: str`, `video_id: str`                                  |
| Output      | `None`                                                          |
| SQL         | `INSERT INTO pipeline_runs ... ON CONFLICT (run_id) DO NOTHING` |
| Idempotency | Duplicate `run_id` silently ignored                             |
| Constraint  | `video_id` must reference existing `videos.video_id`            |

#### `update_pipeline_stage(run_id: str, stage: str) → None`

| Aspect     | Definition                                                                                         |
| ---------- | -------------------------------------------------------------------------------------------------- |
| Input      | `run_id: str`, `stage: str` (one of the 16 stage names)                                            |
| Output     | `None`                                                                                             |
| SQL        | `UPDATE pipeline_runs SET last_completed_stage = ?, status = ? WHERE run_id = ?`                   |
| Constraint | `stage` must be a valid pipeline stage name                                                        |
| State rule | Updates `status` based on stage phase: `analyzing` for analysis stages, `building` for clip stages |

#### `mark_pipeline_complete(run_id: str, clips_generated: int, clips_failed: int) → None`

| Aspect     | Definition                                                                                                                               |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Input      | `run_id: str`, `clips_generated: int`, `clips_failed: int`                                                                               |
| Output     | `None`                                                                                                                                   |
| SQL        | `UPDATE pipeline_runs SET status = ?, completed_at = ?, clips_generated = ?, clips_failed = ? WHERE run_id = ?`                          |
| State rule | `status` = `completed` if `clips_failed == 0`, `partial` if `clips_failed > 0 AND clips_failed <= 50%`, `failed` if `clips_failed > 50%` |
| Constraint | Only valid from `status IN ('started', 'analyzing', 'building')`                                                                         |

#### `mark_pipeline_failed(run_id: str, error_message: str) → None`

| Aspect     | Definition                                                                                         |
| ---------- | -------------------------------------------------------------------------------------------------- |
| Input      | `run_id: str`, `error_message: str`                                                                |
| Output     | `None`                                                                                             |
| SQL        | `UPDATE pipeline_runs SET status = 'failed', error_message = ?, completed_at = ? WHERE run_id = ?` |
| Constraint | Terminal state — no further transitions allowed after `failed`                                     |

#### `fetch_pipeline_state(video_id: str) → PipelineRunState | None`

| Aspect  | Definition                                                                                                                               |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| Input   | `video_id: str`                                                                                                                          |
| Output  | `PipelineRunState                                                                                                                        | None` (adapter-internal DTO or reuses existing) |
| SQL     | `SELECT ... FROM pipeline_runs WHERE video_id = ? ORDER BY started_at DESC LIMIT 1`                                                      |
| Returns | Most recent pipeline run for this video, or `None` if never processed                                                                    |
| Fields  | `run_id`, `video_id`, `status`, `last_completed_stage`, `clips_generated`, `clips_failed`, `started_at`, `completed_at`, `error_message` |

---

### 3.3 Scene Operations

#### `insert_scenes(scenes: SceneList) → None`

| Aspect      | Definition                                                       |
| ----------- | ---------------------------------------------------------------- |
| Input DTO   | `SceneList` from `contracts/scenes.py`                           |
| Output      | `None`                                                           |
| SQL         | Batch `INSERT INTO scenes ... ON CONFLICT (scene_id) DO NOTHING` |
| Idempotency | Duplicate `scene_id` silently ignored                            |
| Constraint  | All scenes must reference a valid `video_id`                     |
| Transaction | Entire batch in a single transaction — all-or-nothing            |

#### `get_scenes_by_video(video_id: str) → SceneList | None`

| Aspect     | Definition                                                           |
| ---------- | -------------------------------------------------------------------- | ----- |
| Input      | `video_id: str`                                                      |
| Output DTO | `SceneList                                                           | None` |
| SQL        | `SELECT ... FROM scenes WHERE video_id = ? ORDER BY start_time ASC`  |
| Returns    | Reconstructed `SceneList` DTO with all scenes sorted by `start_time` |
| Constraint | Returns `None` if no scenes found for this video                     |

---

### 3.4 Clip Operations

#### `insert_clip(clip: ClipDefinition, composite_score: float) → None`

| Aspect      | Definition                                                           |
| ----------- | -------------------------------------------------------------------- |
| Input DTO   | `ClipDefinition` from `contracts/clips.py`, `composite_score: float` |
| Output      | `None`                                                               |
| SQL         | `INSERT INTO clips ... ON CONFLICT (clip_id) DO NOTHING`             |
| Idempotency | Duplicate `clip_id` silently ignored                                 |
| Default     | Initial `status = 'generated'`                                       |

#### `insert_clips_batch(clips: ClipList, scores: dict[str, float]) → None`

| Aspect      | Definition                                                                   |
| ----------- | ---------------------------------------------------------------------------- |
| Input DTO   | `ClipList` from `contracts/clips.py`; `scores`: `{clip_id: composite_score}` |
| Output      | `None`                                                                       |
| SQL         | Batch insert in single transaction                                           |
| Idempotency | Duplicates silently ignored per clip                                         |
| Transaction | All clips in one transaction — all-or-nothing                                |

#### `update_clip_status(clip_id: str, new_status: str, valid_from: tuple[str, ...]) → bool`

| Aspect     | Definition                                                                                |
| ---------- | ----------------------------------------------------------------------------------------- |
| Input      | `clip_id: str`, `new_status: str`, `valid_from: tuple[str, ...]`                          |
| Output     | `bool` — `True` if transition succeeded, `False` if current state was not in `valid_from` |
| SQL        | `UPDATE clips SET status = ? WHERE clip_id = ? AND status IN (...)`                       |
| Constraint | Enforces valid state machine transitions only                                             |
| State flow | `generated → queued → scheduled → published \| failed`                                    |

#### `mark_clip_failed(clip_id: str, error_message: str) → None`

| Aspect     | Definition                                                                |
| ---------- | ------------------------------------------------------------------------- |
| Input      | `clip_id: str`, `error_message: str`                                      |
| Output     | `None`                                                                    |
| SQL        | `UPDATE clips SET status = 'failed', error_message = ? WHERE clip_id = ?` |
| Constraint | Allowed from any non-terminal state                                       |

#### `get_pending_clips(video_id: str) → list[StorageRecord]`

| Aspect     | Definition                                                                           |
| ---------- | ------------------------------------------------------------------------------------ |
| Input      | `video_id: str`                                                                      |
| Output DTO | `list[StorageRecord]` from `contracts/storage.py`                                    |
| SQL        | `SELECT ... FROM clips WHERE video_id = ? AND status NOT IN ('published', 'failed')` |
| Ordering   | Sorted by `composite_score DESC`                                                     |

#### `fetch_clips_by_state(video_id: str, status: str) → list[StorageRecord]`

| Aspect     | Definition                                                                         |
| ---------- | ---------------------------------------------------------------------------------- |
| Input      | `video_id: str`, `status: str`                                                     |
| Output DTO | `list[StorageRecord]`                                                              |
| SQL        | `SELECT ... FROM clips WHERE video_id = ? AND status = ?`                          |
| Constraint | `status` must be one of: `generated`, `queued`, `scheduled`, `published`, `failed` |

---

### 3.5 Query Operations

#### `count_clips_by_status(video_id: str) → dict[str, int]`

| Aspect   | Definition                                                              |
| -------- | ----------------------------------------------------------------------- |
| Input    | `video_id: str`                                                         |
| Output   | `dict[str, int]` — e.g., `{"generated": 5, "failed": 2}`                |
| SQL      | `SELECT status, COUNT(*) FROM clips WHERE video_id = ? GROUP BY status` |
| Use case | Determining `partial` vs `completed` vs `failed` pipeline status        |

#### `video_exists(video_id: str) → bool`

| Aspect   | Definition                                        |
| -------- | ------------------------------------------------- |
| Input    | `video_id: str`                                   |
| Output   | `bool`                                            |
| SQL      | `SELECT 1 FROM videos WHERE video_id = ? LIMIT 1` |
| Use case | Pre-flight check and idempotency guard            |

#### `get_clip(clip_id: str) → StorageRecord | None`

| Aspect     | Definition                                |
| ---------- | ----------------------------------------- | ----- |
| Input      | `clip_id: str`                            |
| Output DTO | `StorageRecord                            | None` |
| SQL        | `SELECT ... FROM clips WHERE clip_id = ?` |

---

## 4. Strict Rules

### Rule 1 — No Raw SQL in Modules

Modules under `modules/` **MUST NOT:**

- Import `sqlite3`, `psycopg2`, `asyncpg`, or any database driver
- Contain SQL strings (no `SELECT`, `INSERT`, `UPDATE`, `DELETE` literals)
- Execute database queries of any kind
- Receive raw database rows (`sqlite3.Row`, tuples, dicts from queries)

**Enforcement:** Any `import sqlite3` or SQL string literal found in `modules/` is a build-blocking violation.

### Rule 2 — Adapter Is the ONLY Database Entry Point

All database access flows through exactly one module:

```text
database/adapter.py
```

The adapter is called exclusively by the orchestrator. No other component may instantiate a database connection or execute queries.

**Call chain (exhaustive):**

```text
run_pipeline.py → orchestrator → database.adapter → database.engines.sqlite_engine
```

No alternative paths exist. No shortcuts.

### Rule 3 — DTO-Only Interaction

The adapter:

- **Accepts** frozen dataclass DTOs from `contracts/` as input parameters
- **Returns** frozen dataclass DTOs from `contracts/` as output values
- **Never** returns raw `sqlite3.Row`, `dict`, `tuple`, or `list[tuple]`
- **Never** accepts untyped `dict` for data to insert

The adapter internally converts DTOs to SQL parameters and SQL rows back to DTOs. This conversion logic lives entirely within `database/adapter.py` and `database/engines/`.

### Rule 4 — No Database Knowledge Leaks

Modules must not:

- Know the table names
- Know the column names
- Know the database file path
- Know whether SQLite or PostgreSQL is in use
- Know the connection string or any engine configuration

All of this is private to `database/`.

---

## 5. SQL Compatibility Rules

All SQL in the adapter MUST work identically on both SQLite and PostgreSQL. This ensures the engine can be swapped without rewriting queries.

### Allowed SQL Syntax

| Pattern                             | Example                                                                 | Notes                                   |
| ----------------------------------- | ----------------------------------------------------------------------- | --------------------------------------- |
| `INSERT ... ON CONFLICT DO NOTHING` | `INSERT INTO videos (...) VALUES (?) ON CONFLICT (video_id) DO NOTHING` | Replaces SQLite-only `INSERT OR IGNORE` |
| `INSERT ... ON CONFLICT DO UPDATE`  | `... ON CONFLICT (clip_id) DO UPDATE SET updated_at = ?`                | Standard upsert                         |
| Explicit column lists               | `INSERT INTO clips (clip_id, video_id, ...) VALUES (?, ?, ...)`         | Always list columns                     |
| Standard `JOIN` / `LEFT JOIN`       | `SELECT ... FROM clips JOIN videos ON ...`                              | ANSI SQL joins only                     |
| Parameterized queries               | `WHERE video_id = ?` (SQLite) / `WHERE video_id = $1` (Postgres)        | Engine handles placeholder style        |
| `ORDER BY`, `LIMIT`, `GROUP BY`     | Standard SQL clauses                                                    | Universally supported                   |
| `CURRENT_TIMESTAMP`                 | `DEFAULT CURRENT_TIMESTAMP`                                             | Works on both engines                   |
| `COALESCE`, `CASE WHEN`             | Standard SQL functions                                                  | Universally supported                   |

### Forbidden SQL Syntax

| Forbidden Pattern           | Reason                                                  | Alternative                                 |
| --------------------------- | ------------------------------------------------------- | ------------------------------------------- | ------------------------- | ------------------------------------ |
| `INSERT OR IGNORE`          | SQLite-only syntax                                      | `INSERT ... ON CONFLICT DO NOTHING`         |
| `INSERT OR REPLACE`         | SQLite-only, also has subtle semantics differences      | `INSERT ... ON CONFLICT DO UPDATE`          |
| Implicit column typing      | SQLite allows any type in any column; Postgres does not | Always use explicit types                   |
| `datetime('now')`           | SQLite function                                         | Use `CURRENT_TIMESTAMP`                     |
| `AUTOINCREMENT`             | Different syntax per engine                             | Use application-generated IDs (SHA-256)     |
| `PRAGMA` statements         | SQLite-only                                             | Move to engine-specific initialization      |
| `->`, `->>` JSON operators  | Postgres-only advanced JSON                             | No JSON columns (see Section 6)             |
| `SERIAL`, `BIGSERIAL`       | Postgres-only auto-increment types                      | Use `TEXT` primary keys (content-addressed) |
| `RETURNING` clause          | Not supported in all SQLite versions                    | Use separate `SELECT` after write           |
| `::type` cast syntax        | Postgres-only                                           | Use `CAST(x AS type)`                       |
| String concatenation with ` |                                                         | `                                           | Works on both but fragile | Compute in Python, pass as parameter |

### Canonical Data Types

All schema definitions use this restricted type set that maps cleanly to both SQLite and PostgreSQL:

| Canonical Type | SQLite Mapping  | PostgreSQL Mapping | Usage                                                                                                                                                        |
| -------------- | --------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `TEXT`         | TEXT            | TEXT / VARCHAR     | All string fields: IDs, paths, messages, status                                                                                                              |
| `INTEGER`      | INTEGER         | INTEGER / BIGINT   | Counts, timestamps in ms, byte sizes                                                                                                                         |
| `REAL`         | REAL            | DOUBLE PRECISION   | Scores, durations in seconds, FPS                                                                                                                            |
| `BOOLEAN`      | INTEGER (0/1)   | BOOLEAN            | Flags (`has_audio`). Adapter normalizes to Python `bool`                                                                                                     |
| `TIMESTAMP`    | TEXT (ISO 8601) | TIMESTAMP          | `created_at`, `updated_at`, `scheduled_at`. Stored as ISO 8601 text in SQLite, native timestamp in Postgres. Adapter normalizes to ISO 8601 strings in DTOs. |

### Placeholder Normalization

The adapter engine handles placeholder differences transparently:

| Engine     | Placeholder Style | Example               |
| ---------- | ----------------- | --------------------- |
| SQLite     | `?`               | `WHERE video_id = ?`  |
| PostgreSQL | `$1`, `$2`, ...   | `WHERE video_id = $1` |

The adapter writes queries with a canonical placeholder (e.g., `?`), and the engine translates before execution. Module code never sees placeholders.

---

## 6. Schema Design Rules

### Allowed

- `CREATE TABLE IF NOT EXISTS` — idempotent table creation
- `CREATE INDEX IF NOT EXISTS` — idempotent index creation
- `PRIMARY KEY` constraints
- `FOREIGN KEY` references with `REFERENCES` clause
- `NOT NULL` constraints
- `DEFAULT` values (constants and `CURRENT_TIMESTAMP` only)
- `UNIQUE` constraints
- `CHECK` constraints (simple expressions only)

### Forbidden

| Feature           | Reason                                                     |
| ----------------- | ---------------------------------------------------------- |
| Triggers          | Hidden side effects, engine-specific syntax, hard to debug |
| Stored procedures | Engine-specific, violates "all logic in Python" principle  |
| Views             | Add query complexity without portability benefit           |
| JSON columns      | Postgres `jsonb` operators don't exist in SQLite           |
| Generated columns | Engine-specific syntax differences                         |
| Partial indexes   | SQLite support is limited and syntax differs from Postgres |
| `ENUM` types      | Postgres-only; use `TEXT` with `CHECK` constraint instead  |
| Sequences         | Postgres-only; all IDs are application-generated           |

### Schema Change Rules

- **All schema changes go through migration files** in `database/migrations/`
- **Migration naming:** `YYYYMMDD000NNN_description.sql`
- **Idempotent:** Every statement uses `IF NOT EXISTS` / `IF EXISTS`
- **Append-only:** Never modify an existing migration — always create a new file
- **Fatal on failure:** If any migration fails, the process must not start

---

## 7. Idempotency Enforcement

### Content-Addressable IDs

Every entity uses a deterministic, content-derived primary key:

| Entity | ID Formula                                  | Example                          |
| ------ | ------------------------------------------- | -------------------------------- |
| Video  | `SHA256(first_10MB + file_size)[:16]`       | `a3f8c91b2d4e7f06`               |
| Scene  | `{video_id}_{start_ms}_{end_ms}`            | `a3f8c91b2d4e7f06_15000_22500`   |
| Clip   | `SHA256(video_id + start_ms + end_ms)[:16]` | `7b2e4f19a8c3d501`               |
| Run    | `{video_id}_{timestamp_ms}`                 | `a3f8c91b2d4e7f06_1711296000000` |

Same input always produces the same ID. No UUIDs. No auto-increment.

### Insert-on-Conflict Behavior

Every `INSERT` in the adapter uses `ON CONFLICT DO NOTHING` or `ON CONFLICT DO UPDATE` with an explicit conflict target:

```sql
-- Idempotent video insert
INSERT INTO videos (video_id, file_path, duration_seconds, width, height, fps, has_audio, file_size_bytes)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (video_id) DO NOTHING;

-- Idempotent clip insert
INSERT INTO clips (clip_id, video_id, start_time, end_time, duration, composite_score, status)
VALUES (?, ?, ?, ?, ?, ?, 'generated')
ON CONFLICT (clip_id) DO NOTHING;

-- Idempotent scene batch insert
INSERT INTO scenes (scene_id, video_id, start_time, end_time, duration)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (scene_id) DO NOTHING;
```

### Deterministic ID Guarantee

The adapter MUST NOT generate IDs. IDs are computed by the modules (or orchestrator) using the deterministic formulas above and passed to the adapter as part of the DTO. The adapter simply persists them.

### Re-run Behavior

Running the pipeline twice on the same input:

1. `insert_video()` → `ON CONFLICT DO NOTHING` → no duplicate
2. `create_pipeline_run()` → detects existing completed run → orchestrator exits early
3. `insert_scenes()` → `ON CONFLICT DO NOTHING` → no duplicates
4. `insert_clip()` → `ON CONFLICT DO NOTHING` → no duplicates
5. Second run completes in < 5 seconds (DB lookups only)

---

## 8. Transaction Model

### Explicit Transactions

All write operations in the adapter use explicit transactions:

```text
BEGIN TRANSACTION
  ... one or more INSERT/UPDATE statements ...
COMMIT
```

- **No implicit commits** — The adapter controls transaction boundaries, not the database driver's autocommit mode
- **No autocommit mode** — The connection is configured with autocommit disabled
- **Rollback on failure** — If any statement in a transaction fails, the entire transaction is rolled back

### Atomic Writes Per Stage

Each pipeline stage produces exactly one atomic database write:

| Stage            | Atomic Write                                     |
| ---------------- | ------------------------------------------------ |
| ingestion        | `INSERT video` + `INSERT pipeline_run` in one TX |
| scene_splitter   | `INSERT scenes` (batch) in one TX                |
| transcription    | No DB write (DTO passed in-memory to next stage) |
| face_detection   | No DB write (DTO passed in-memory to next stage) |
| scoring          | No DB write (DTO passed in-memory to next stage) |
| clip_builder     | `INSERT clips` (batch) in one TX                 |
| Per-clip stages  | `UPDATE clip status` after each clip completes   |
| Final checkpoint | `UPDATE pipeline_runs` with final status         |

### Checkpoint Transaction Pattern

After every stage completes:

```text
BEGIN TRANSACTION
  UPDATE pipeline_runs SET last_completed_stage = '{stage}' WHERE run_id = '{run_id}'
COMMIT
```

This is the LAST operation of every stage. If the process crashes before this commit, the stage is re-executed on resume.

### Batch Insert Transaction Pattern

For stages that insert multiple rows (scenes, clips):

```text
BEGIN TRANSACTION
  INSERT INTO scenes (...) VALUES (...) ON CONFLICT DO NOTHING;  -- row 1
  INSERT INTO scenes (...) VALUES (...) ON CONFLICT DO NOTHING;  -- row 2
  INSERT INTO scenes (...) VALUES (...) ON CONFLICT DO NOTHING;  -- row N
COMMIT
```

All rows succeed or none do. No partial batch inserts.

---

## 9. State Authority Rules

### Database Is the Source of Truth

The database answers all authoritative questions about system state:

| Question                             | Authoritative Source                                  |
| ------------------------------------ | ----------------------------------------------------- |
| Has this video been processed?       | `SELECT status FROM pipeline_runs WHERE video_id = ?` |
| What stage did the pipeline reach?   | `pipeline_runs.last_completed_stage`                  |
| How many clips were generated?       | `SELECT COUNT(*) FROM clips WHERE video_id = ?`       |
| What is this clip's status?          | `clips.status`                                        |
| Which clips are ready to publish?    | `clips WHERE status = 'scheduled'`                    |
| Did this scene already get inserted? | `SELECT 1 FROM scenes WHERE scene_id = ?`             |

### Filesystem Is Derived

Files on disk (video clips, thumbnails, metadata files) are **outputs**, not state. The database decides what exists and what needs processing. If a file exists on disk but no database row references it, the file is an orphan and should be cleaned up.

**Rule:** Never make processing decisions based on filesystem checks alone. Always consult the database first.

### Consistency Invariants

| Invariant                                             | Enforcement                                        |
| ----------------------------------------------------- | -------------------------------------------------- |
| DB says clip exists → file MUST exist on disk         | Adapter + storage module guarantee this            |
| DB says clip status = `published` → file was uploaded | Publisher confirms upload before status transition |
| File exists on disk → DB MAY or MAY NOT reference it  | Orphan cleanup handles this case                   |
| DB says pipeline = `completed` → all clips processed  | Orchestrator verifies before marking complete      |

---

## 10. Migration Strategy

### Phase 1 — SQLite (Current)

**Status:** Active. This is the production configuration.

- Engine: `sqlite3` (Python stdlib)
- Database file: `output/shorts_factory.db` (path from `config.paths.database`)
- Connection configuration:
  - `PRAGMA journal_mode=WAL` — Write-ahead logging for crash safety
  - `PRAGMA foreign_keys=ON` — Referential integrity enforcement
  - `PRAGMA synchronous=NORMAL` — Balance safety and performance
- Single writer, concurrent readers (WAL mode)
- Migrations run on process startup

### Phase 2 — Dual Compatibility Preparation

**Status:** In progress (via this specification).

All SQL in the adapter already uses portable syntax:

- `ON CONFLICT DO NOTHING` instead of `INSERT OR IGNORE`
- `CURRENT_TIMESTAMP` instead of `datetime('now')`
- Explicit column lists on all `INSERT` statements
- Parameterized queries with engine-handled placeholder translation
- No PRAGMA statements in queries (moved to engine initialization)
- No SQLite-specific functions in SQL strings
- Canonical type set (`TEXT`, `INTEGER`, `REAL`, `BOOLEAN`, `TIMESTAMP`)

### Phase 3 — PostgreSQL Switch

When the switch is needed, these are the **only** steps required:

#### Step 1 — Implement PostgreSQL Engine

Create `database/engines/postgres_engine.py` implementing the same `DatabaseEngine` protocol as `sqlite_engine.py`. This file:

- Uses `psycopg2` (or `asyncpg`) for connections
- Translates `?` placeholders to `$1, $2, ...` (or uses the driver's native parameterization)
- Handles `BOOLEAN` natively (no 0/1 mapping)
- Handles `TIMESTAMP` natively (no ISO 8601 string conversion)
- Applies PostgreSQL-specific connection settings (connection pooling, statement timeout)

#### Step 2 — Update Configuration

Add to `config.yaml`:

```yaml
database:
  engine: postgres # Was: sqlite
  postgres:
    host: localhost
    port: 5432
    database: shorts_factory
    user: shorts_factory
    password_env: SHORTS_DB_PASSWORD # Read from environment variable
```

#### Step 3 — Run Schema Migration

Apply the same migration files from `database/migrations/` to the PostgreSQL instance. Because all migrations use portable SQL (`CREATE TABLE IF NOT EXISTS`, canonical types), they run unmodified.

#### Step 4 — Data Migration (Optional)

If existing SQLite data must be preserved:

1. Export all tables from SQLite as CSV or SQL `INSERT` statements
2. Import into PostgreSQL
3. Validate row counts match
4. Validate all foreign key constraints hold

#### Step 5 — Validate Pipeline

Run the full pipeline on a test video and verify:

- All 16 stages complete successfully
- Checkpoint and resume work correctly
- Idempotency holds (second run produces no changes)
- All DTOs are correctly serialized and deserialized

### Migration Guarantee

> **No module code changes.** The switch from SQLite to PostgreSQL requires changes ONLY in:
>
> 1. `database/engines/postgres_engine.py` (new file)
> 2. `config.yaml` (engine selection)
> 3. `database/adapter.py` (engine selection logic — one `if/else`)
>
> Zero changes in `modules/`, `contracts/`, `orchestrator/`, or `run_pipeline.py`.

---

## 11. Adapter Implementation Strategy

### Interface-Based Design

The adapter uses a protocol (abstract interface) that both engines implement:

```text
DatabaseEngine (Protocol)
├── connect()
├── close()
├── execute(query, params) → cursor
├── executemany(query, params_list) → cursor
├── begin_transaction()
├── commit()
├── rollback()
├── run_migrations(migrations_dir)
└── engine-specific initialization (PRAGMAs, connection settings)
```

### Engine Selection

On startup, the adapter reads `config.database.engine` and instantiates the appropriate engine:

```text
config.database.engine = "sqlite"  →  SQLiteEngine(config.database.sqlite.path)
config.database.engine = "postgres" →  PostgresEngine(config.database.postgres)
```

The adapter holds a reference to the engine and delegates all SQL execution to it. The adapter itself contains no engine-specific code — only DTO↔SQL translation logic.

### Engine-Specific Responsibilities

| Responsibility        | SQLiteEngine              | PostgresEngine                    |
| --------------------- | ------------------------- | --------------------------------- |
| Connection            | `sqlite3.connect(path)`   | `psycopg2.connect(...)` or pool   |
| Placeholder style     | `?`                       | `%s` (psycopg2) or `$1` (asyncpg) |
| WAL mode              | `PRAGMA journal_mode=WAL` | N/A (WAL is default in Postgres)  |
| Foreign keys          | `PRAGMA foreign_keys=ON`  | Always enforced                   |
| Boolean mapping       | `0`/`1` ↔ `True`/`False`  | Native `BOOLEAN`                  |
| Timestamp mapping     | ISO 8601 `TEXT`           | Native `TIMESTAMP`                |
| Transaction isolation | Default (DEFERRED)        | `READ COMMITTED`                  |
| Connection pooling    | N/A (single connection)   | Connection pool (min 1, max 5)    |

---

## 12. Testing Strategy

### Unit Tests — Adapter Layer

Test every adapter function with a real SQLite database (in-memory or temp file):

- **Insert and retrieve** — Write a DTO, read it back, verify equality
- **Idempotency** — Insert the same DTO twice, verify only one row exists
- **State transitions** — Verify valid transitions succeed and invalid ones are rejected
- **Batch operations** — Insert multiple scenes/clips, verify all persisted atomically
- **Missing data** — Query for non-existent `video_id`, verify `None` returned

### Integration Tests — Pipeline Through Adapter

- Wire the orchestrator to the adapter with a real SQLite database
- Process a test video (fixture) through the full pipeline
- Verify all 4 tables contain expected rows
- Verify checkpoint/resume by simulating crash after each stage

### Idempotency Tests

- Run the pipeline twice on identical input
- Assert: zero new rows on second run
- Assert: zero new files on second run
- Assert: second run completes in < 5 seconds (DB lookups only)

### SQL Compatibility Validation

- Parse all SQL strings in the adapter
- Verify no forbidden syntax patterns (see Section 5)
- Verify all queries use parameterized placeholders
- Optionally: run the same test suite against a PostgreSQL instance to validate portability

### Adapter Isolation Tests

- Scan all files in `modules/` for forbidden imports (`sqlite3`, `psycopg2`)
- Scan all files in `modules/` for SQL string literals (`SELECT`, `INSERT`, `UPDATE`, `DELETE`)
- Verify no module directly instantiates a database connection

---

## 13. Failure Modes

### Database Connection Failure

| Scenario              | Detection                   | Response                                |
| --------------------- | --------------------------- | --------------------------------------- |
| SQLite file not found | `sqlite3.OperationalError`  | Create database and run migrations      |
| SQLite file is locked | `sqlite3.OperationalError`  | Retry with exponential backoff (max 3×) |
| SQLite disk full      | `sqlite3.OperationalError`  | Abort pipeline with `failed` status     |
| Postgres unreachable  | `psycopg2.OperationalError` | Retry connection (max 3×), then abort   |
| Postgres auth failure | `psycopg2.OperationalError` | Abort immediately — configuration error |

### Transaction Failure

| Scenario                 | Detection                                      | Response                           |
| ------------------------ | ---------------------------------------------- | ---------------------------------- |
| Constraint violation     | `IntegrityError`                               | Rollback transaction, log error    |
| Deadlock (Postgres only) | `psycopg2.extensions.TransactionRollbackError` | Retry transaction once, then abort |
| Statement timeout        | Engine-specific timeout error                  | Rollback, log, abort current stage |

### Constraint Violation Handling

| Violation                | Cause                             | Adapter Behavior                            |
| ------------------------ | --------------------------------- | ------------------------------------------- |
| Duplicate PK             | Re-run on same input              | `ON CONFLICT DO NOTHING` — silently ignored |
| FK reference missing     | Bug — clip inserted without video | Raise `DatabaseIntegrityError`, abort stage |
| NOT NULL violation       | Bug — required field is None      | Raise `DatabaseIntegrityError`, abort stage |
| CHECK constraint failure | Bug — value out of range          | Raise `DatabaseIntegrityError`, abort stage |

### Recovery Rules

1. **Connection failures** — Retry with backoff, max 3 attempts
2. **Transaction failures** — Rollback immediately, retry once, then propagate error
3. **Constraint violations from idempotency** — Expected, silently ignored via `ON CONFLICT`
4. **Constraint violations from bugs** — Fatal, propagate error to orchestrator
5. **Migration failures** — Fatal, process must not start

---

## 14. Anti-Patterns

### FORBIDDEN — SQL Inside Modules

```python
# ❌ NEVER — module executing SQL directly
# modules/scoring/scoring.py
import sqlite3

def process(scenes, transcript, config):
    conn = sqlite3.connect("output/shorts_factory.db")
    conn.execute("INSERT INTO scores ...")  # VIOLATION
```

### FORBIDDEN — Using an ORM

```python
# ❌ NEVER — ORM adds hidden complexity and engine-specific behavior
from sqlalchemy import create_engine, Column, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()
class Video(Base):  # VIOLATION — no ORM models
    __tablename__ = 'videos'
```

### FORBIDDEN — Engine-Specific Logic in Business Layer

```python
# ❌ NEVER — orchestrator contains SQLite-specific code
# orchestrator/pipeline.py
import sqlite3
conn = sqlite3.connect("output/shorts_factory.db")
conn.execute("PRAGMA journal_mode=WAL")  # VIOLATION — engine-specific
```

### FORBIDDEN — Bypassing the Adapter

```python
# ❌ NEVER — direct database access from anywhere except the adapter
# orchestrator/pipeline.py
from database.engines.sqlite_engine import SQLiteEngine
engine = SQLiteEngine("output/shorts_factory.db")
engine.execute("SELECT * FROM clips")  # VIOLATION — bypass adapter
```

### FORBIDDEN — Returning Raw Rows

```python
# ❌ NEVER — adapter returning untyped data
def get_clips(video_id: str) -> list[dict]:  # VIOLATION — returns dicts
    rows = cursor.fetchall()
    return [dict(row) for row in rows]
```

### CORRECT — All Patterns

```python
# ✅ Adapter accepts and returns DTOs
from contracts.clips import ClipDefinition, ClipList
from contracts.storage import StorageRecord

def insert_clip(clip: ClipDefinition, composite_score: float) -> None:
    self._engine.execute(
        "INSERT INTO clips (...) VALUES (?, ?, ...) ON CONFLICT (clip_id) DO NOTHING",
        (clip.clip_id, clip.video_id, ...)
    )

def get_pending_clips(video_id: str) -> list[StorageRecord]:
    rows = self._engine.execute("SELECT ... FROM clips WHERE ...").fetchall()
    return [self._row_to_storage_record(row) for row in rows]  # Converts to DTO
```

---

## 15. Final Guarantees

This specification guarantees the following properties. Any violation is a blocking defect.

### G1 — Engine Portability

Switching from SQLite to PostgreSQL requires:

- **1 new file:** `database/engines/postgres_engine.py`
- **1 config change:** `config.database.engine: postgres`
- **0 module changes**
- **0 DTO changes**
- **0 orchestrator changes**
- **0 test logic changes** (only connection setup in test fixtures)

**Migration safety rule:** The adapter is the sole abstraction boundary between the application and the database engine. Switching engines requires changes ONLY in `database/`. If any change is needed in `modules/`, `contracts/`, `orchestrator/`, or `run_pipeline.py`, the adapter abstraction has been violated and must be fixed before the switch proceeds.

### G2 — Determinism Preserved

The adapter does not introduce non-determinism. All IDs are content-addressed. All queries are parameterized. No random values, no auto-increment, no server-generated UUIDs.

### G3 — Idempotency Preserved

Every write operation uses `ON CONFLICT` clauses. Re-running the pipeline produces zero new rows, zero new files, and completes in < 5 seconds on the second pass.

### G4 — State Authority Preserved

The database remains the single source of truth. No module makes decisions based on filesystem state alone. All state queries flow through the adapter's typed interface.

### G5 — Module Isolation Preserved

No module under `modules/` has any awareness of the database layer. The adapter is invisible to modules — they produce DTOs, the orchestrator persists them.

### G6 — No ORM, No Magic

The adapter uses raw SQL with parameterized queries. No ORM, no query builder, no auto-generated schemas. Every query is explicit, readable, and auditable.

---

## Appendix A — Complete Adapter Function Index

| Function                 | Input                       | Output                     | Idempotent | Transaction |
| ------------------------ | --------------------------- | -------------------------- | ---------- | ----------- |
| `insert_video`           | `IngestionResult`           | `None`                     | ✅         | ✅          |
| `get_video`              | `video_id: str`             | `IngestionResult \| None`  | N/A        | Read        |
| `video_exists`           | `video_id: str`             | `bool`                     | N/A        | Read        |
| `create_pipeline_run`    | `run_id, video_id`          | `None`                     | ✅         | ✅          |
| `update_pipeline_stage`  | `run_id, stage`             | `None`                     | ✅         | ✅          |
| `mark_pipeline_complete` | `run_id, generated, failed` | `None`                     | ✅         | ✅          |
| `mark_pipeline_failed`   | `run_id, error_message`     | `None`                     | ✅         | ✅          |
| `fetch_pipeline_state`   | `video_id`                  | `PipelineRunState \| None` | N/A        | Read        |
| `insert_scenes`          | `SceneList`                 | `None`                     | ✅         | ✅ (batch)  |
| `get_scenes_by_video`    | `video_id`                  | `SceneList \| None`        | N/A        | Read        |
| `insert_clip`            | `ClipDefinition, score`     | `None`                     | ✅         | ✅          |
| `insert_clips_batch`     | `ClipList, scores`          | `None`                     | ✅         | ✅ (batch)  |
| `update_clip_status`     | `clip_id, new, valid_from`  | `bool`                     | ✅         | ✅          |
| `mark_clip_failed`       | `clip_id, error_message`    | `None`                     | ✅         | ✅          |
| `get_pending_clips`      | `video_id`                  | `list[StorageRecord]`      | N/A        | Read        |
| `fetch_clips_by_state`   | `video_id, status`          | `list[StorageRecord]`      | N/A        | Read        |
| `count_clips_by_status`  | `video_id`                  | `dict[str, int]`           | N/A        | Read        |
| `get_clip`               | `clip_id`                   | `StorageRecord \| None`    | N/A        | Read        |
