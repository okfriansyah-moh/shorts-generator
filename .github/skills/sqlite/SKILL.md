---
name: sqlite
description: "SQLite3 patterns for Shorts Factory. Use when implementing database operations, migrations, state machine transitions, checkpoint logic, or query optimization. Covers connection management, WAL mode, parameterized queries, ON CONFLICT DO NOTHING, state transitions, and transaction patterns."
---

# SQLite3 Skill

## Adapter Constraint (MANDATORY)

> **All database access MUST go through `database/adapter.py`.**
>
> - Modules under `modules/` MUST NOT import `sqlite3` or any database driver
> - Modules MUST NOT contain SQL strings or execute queries
> - The adapter accepts and returns frozen DTOs from `contracts/` — no raw rows, no dicts
> - Only the orchestrator calls the adapter — modules never touch the database
> - All SQL MUST use portable syntax (`ON CONFLICT DO NOTHING`, not `INSERT OR IGNORE`)
>
> See `docs/db_adapter_spec.md` for the full adapter interface and migration strategy.

## When to Use

- Implementing the `database/adapter.py` or `database/engines/sqlite_engine.py`
- Writing migrations for new tables in `database/migrations/`
- Implementing state machine transitions (pipeline/clip status)
- Checkpoint and resume logic in the orchestrator
- Testing adapter operations

## Library

- **Package:** `sqlite3` (Python stdlib — no external dependency)
- **Database file:** `output/shorts_factory.db` (from `config.paths.database`)
- **Single writer:** Only the orchestrator writes to the database

## Connection Setup

```python
import sqlite3

def create_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row  # Dict-like access

    # Enable WAL mode (concurrent reads during writes)
    conn.execute("PRAGMA journal_mode=WAL")

    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys=ON")

    # Balance safety/speed
    conn.execute("PRAGMA synchronous=NORMAL")

    return conn
```

**PRAGMAs explained:**

| PRAGMA               | Value                 | Reason                         |
| -------------------- | --------------------- | ------------------------------ |
| `journal_mode=WAL`   | Write-ahead logging   | Concurrent reads, crash safety |
| `foreign_keys=ON`    | Enable FK constraints | Referential integrity          |
| `synchronous=NORMAL` | Moderate fsync        | Balance safety/performance     |

## 4-Table Schema

```sql
-- videos: source video metadata
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    fps REAL NOT NULL,
    has_audio INTEGER NOT NULL DEFAULT 1,
    file_size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- scenes: detected scene boundaries
CREATE TABLE IF NOT EXISTS scenes (
    scene_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    start_time INTEGER NOT NULL,
    end_time INTEGER NOT NULL,
    duration REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- clips: generated short clips
CREATE TABLE IF NOT EXISTS clips (
    clip_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    start_time INTEGER NOT NULL,
    end_time INTEGER NOT NULL,
    duration REAL NOT NULL,
    composite_score REAL,
    status TEXT NOT NULL DEFAULT 'generated',
    scheduled_at TEXT,
    published_at TEXT,
    publish_url TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- pipeline_runs: execution tracking
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(video_id),
    status TEXT NOT NULL DEFAULT 'started',
    last_completed_stage TEXT,
    clips_generated INTEGER DEFAULT 0,
    clips_failed INTEGER DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    error_message TEXT
);
```

## Parameterized Queries (MANDATORY)

```python
# ✅ CORRECT — parameterized (safe from SQL injection)
cursor.execute(
    "SELECT * FROM clips WHERE video_id = ? AND status = ?",
    (video_id, "scheduled")
)

# ❌ FORBIDDEN — string interpolation (SQL injection risk)
cursor.execute(f"SELECT * FROM clips WHERE video_id = '{video_id}'")
```

**Always use `?` placeholders. No exceptions.**

## Portable INSERT (Idempotency)

```python
# ✅ CORRECT — portable ON CONFLICT syntax (works on SQLite AND Postgres)
cursor.execute(
    """INSERT INTO videos
       (video_id, file_path, duration_seconds, width, height, fps, has_audio, file_size_bytes)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT (video_id) DO NOTHING""",
    (video_id, file_path, duration, width, height, fps, has_audio, file_size)
)
conn.commit()

# ❌ FORBIDDEN — SQLite-only syntax (not portable to Postgres)
cursor.execute(
    """INSERT OR IGNORE INTO videos ..."""
)
```

Running twice → second INSERT is silently ignored via `ON CONFLICT DO NOTHING`. No duplicates.

## State Machine Transitions

### Pipeline States

```
started → analyzing → building → completed
                                → partial (some clips failed)
                                → failed (>50% clips failed or fatal error)
```

### Clip States

```
generated → queued → scheduled → published
                               → failed
```

### Safe State Transition Pattern

```python
def transition_clip_status(conn, clip_id: str, new_status: str, valid_from: tuple[str, ...]):
    """Transition clip status only from valid source states."""
    cursor = conn.execute(
        """UPDATE clips SET status = ?, updated_at = datetime('now')
           WHERE clip_id = ? AND status IN ({})""".format(
            ",".join("?" * len(valid_from))
        ),
        (new_status, clip_id, *valid_from)
    )
    conn.commit()

    if cursor.rowcount == 0:
        raise RuntimeError(f"Invalid state transition for {clip_id} → {new_status}")

# Usage:
transition_clip_status(conn, clip_id, "scheduled", ("generated", "queued"))
transition_clip_status(conn, clip_id, "published", ("scheduled",))
```

## Checkpoint Pattern

```python
def update_checkpoint(conn, run_id: str, stage: str):
    """Record last completed stage for resume."""
    conn.execute(
        """UPDATE pipeline_runs
           SET last_completed_stage = ?, updated_at = datetime('now')
           WHERE run_id = ?""",
        (stage, run_id)
    )
    conn.commit()

def get_resume_stage(conn, video_id: str) -> str | None:
    """Get last completed stage for resume."""
    row = conn.execute(
        """SELECT last_completed_stage FROM pipeline_runs
           WHERE video_id = ? AND status NOT IN ('completed', 'failed')
           ORDER BY started_at DESC LIMIT 1""",
        (video_id,)
    ).fetchone()

    return row["last_completed_stage"] if row else None
```

## Transaction Pattern

```python
def batch_insert_scenes(conn, scenes: list[dict]):
    """Atomic batch insert with rollback on error."""
    try:
        conn.execute("BEGIN")
        conn.executemany(
            """INSERT INTO scenes
               (scene_id, video_id, start_time, end_time, duration)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (scene_id) DO NOTHING""",
            [(s["scene_id"], s["video_id"], s["start_time"], s["end_time"], s["duration"])
             for s in scenes]
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
```

## Migration Execution

```python
import os

def run_migrations(conn, migrations_dir: str = "database/migrations"):
    """Execute all migrations in order. Fatal on failure."""
    migration_files = sorted(
        f for f in os.listdir(migrations_dir) if f.endswith(".sql")
    )

    for filename in migration_files:
        filepath = os.path.join(migrations_dir, filename)
        with open(filepath) as f:
            sql = f.read()

        try:
            conn.executescript(sql)
        except Exception as e:
            raise RuntimeError(f"Migration failed: {filename}: {e}")
```

**Migration naming:** `YYYYMMDD000NNN_description.sql`
**Idempotent:** All use `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`

## Performance Patterns

```python
# Batch insert (10–100× faster than individual inserts)
conn.executemany("INSERT INTO scenes (...) VALUES (?) ON CONFLICT DO NOTHING", scene_data)

# Index for common queries
conn.execute("CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_scenes_video ON scenes(video_id)")

# Count before fetching (avoid loading 200 scenes if not needed)
count = conn.execute("SELECT COUNT(*) FROM scenes WHERE video_id = ?", (vid,)).fetchone()[0]
```

## Anti-Patterns

```python
# ❌ String formatting in SQL
conn.execute(f"INSERT INTO clips VALUES ('{clip_id}')")

# ❌ Plain INSERT (fails on duplicate, non-idempotent)
conn.execute("INSERT INTO videos ...")

# ❌ No commit (changes are lost)
conn.execute("INSERT INTO videos (...) VALUES (?) ON CONFLICT DO NOTHING")
# Missing: conn.commit()

# ❌ No foreign keys enabled
conn = sqlite3.connect(db_path)  # FK constraints disabled by default

# ❌ Using ORM (forbidden — stdlib sqlite3 only)
from sqlalchemy import create_engine  # FORBIDDEN

# ✅ Parameterized, ON CONFLICT DO NOTHING, WAL mode, commit, FK enabled
```
