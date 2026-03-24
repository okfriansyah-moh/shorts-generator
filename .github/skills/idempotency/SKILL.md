---
name: idempotency
description: "Idempotency enforcement for Shorts Factory. Use when implementing database writes, file operations, or pipeline resume logic. Ensures running the pipeline twice on the same input produces no duplicates and no corruption."
---

# Idempotency Skill

## When to Use

- Implementing database writes (INSERT, UPDATE) in `database/adapter.py`
- Writing files to the output directory
- Implementing pipeline resume logic in the orchestrator
- Reviewing checkpoint behavior

> **All database operations MUST go through `database/adapter.py`.** Modules under `modules/` never touch the database. See `docs/db_adapter_spec.md`.

## Core Invariant

> Running the pipeline twice on the same input produces no duplicates, no corruption, and completes in < 5 seconds on the second run (cache check only).

## ID Computation (Content-Addressable)

```python
import hashlib

def compute_video_id(file_path: str) -> str:
    """Read first 10MB + file size → SHA-256 → first 16 hex chars."""
    file_size = os.path.getsize(file_path)
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        hasher.update(f.read(10 * 1024 * 1024))  # First 10MB
    hasher.update(str(file_size).encode())
    return hasher.hexdigest()[:16]

def compute_scene_id(video_id: str, start_ms: int, end_ms: int) -> str:
    """Deterministic scene ID from video + time boundaries."""
    return f"{video_id}_{start_ms}_{end_ms}"

def compute_clip_id(video_id: str, start_ms: int, end_ms: int) -> str:
    """SHA-256 of video_id + boundaries → first 16 hex chars."""
    raw = f"{video_id}{start_ms}{end_ms}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

## Database Patterns

### ON CONFLICT DO NOTHING (Portable)

```sql
-- ✅ CORRECT — idempotent insert, portable to Postgres
INSERT INTO videos (video_id, file_path, duration_seconds, ...)
VALUES (?, ?, ?, ...)
ON CONFLICT (video_id) DO NOTHING;

-- ❌ FORBIDDEN — SQLite-only syntax, not portable
INSERT OR IGNORE INTO videos (video_id, file_path, duration_seconds, ...)
VALUES (?, ?, ?, ...);

-- ❌ FORBIDDEN — non-idempotent insert
INSERT INTO videos (video_id, file_path, duration_seconds, ...)
VALUES (?, ?, ?, ...);
-- This will fail on duplicate video_id
```

### Parameterized SQL Only

```python
# ✅ CORRECT
cursor.execute(
    "INSERT INTO videos (video_id) VALUES (?) ON CONFLICT (video_id) DO NOTHING",
    (video_id,)
)

# ❌ FORBIDDEN — SQL injection risk + non-deterministic
cursor.execute(f"INSERT INTO videos (video_id) VALUES ('{video_id}')")
```

### Upsert Pattern (when update is needed)

```sql
-- ✅ CORRECT — idempotent upsert, portable syntax
INSERT INTO clips (clip_id, video_id, status)
VALUES (?, ?, 'generated')
ON CONFLICT (clip_id) DO UPDATE SET
    updated_at = CURRENT_TIMESTAMP
WHERE clips.status = 'generated';
```

## File Write Patterns

### Atomic Write (Write-Then-Rename)

```python
# ✅ CORRECT — atomic, crash-safe
tmp_path = f"{output_path}.tmp"
with open(tmp_path, 'wb') as f:
    f.write(data)
os.rename(tmp_path, output_path)  # Atomic on same filesystem

# ❌ FORBIDDEN — partial write on crash
with open(output_path, 'wb') as f:
    f.write(data)  # If crash during write, file is corrupted
```

### Skip-If-Exists

```python
# ✅ CORRECT — idempotent file operation
if os.path.exists(output_path):
    logger.info("File already exists, skipping", extra={"path": output_path})
    return output_path
# ... generate file ...
```

## Pipeline Resume Rules

### Pipeline-Level Resume

```python
# Query existing run
run = db.query("SELECT * FROM pipeline_runs WHERE video_id = ? AND status != 'completed'", (video_id,))

if run and run.status == 'completed':
    logger.info("Already processed, exiting")
    return  # Idempotent: no work on re-run

if run:
    resume_stage = STAGE_ORDER.index(run.last_completed_stage) + 1
else:
    resume_stage = 0  # Fresh run
```

### Clip-Level Resume

```python
# Skip already-processed clips
existing_clips = db.query("SELECT clip_id FROM clips WHERE video_id = ? AND status >= 'queued'", (video_id,))
existing_ids = {row.clip_id for row in existing_clips}

for clip in clip_list.clips:
    if clip.clip_id in existing_ids:
        logger.info("Clip already processed, skipping", extra={"clip_id": clip.clip_id})
        continue
    process_clip(clip)
```

## Checklist

Before committing database or file operations, verify:

- [ ] All INSERTs use `ON CONFLICT DO NOTHING` or `ON CONFLICT DO UPDATE` (not `INSERT OR IGNORE`)
- [ ] All SQL uses parameterized queries (no string interpolation)
- [ ] All SQL uses portable syntax per `docs/db_adapter_spec.md`
- [ ] All database access goes through `database/adapter.py`
- [ ] All file writes use atomic write-then-rename pattern
- [ ] Pipeline checks for existing completed run before starting
- [ ] Per-clip processing skips already-completed clips
- [ ] IDs are content-addressable (SHA-256 based)
- [ ] `os.makedirs(path, exist_ok=True)` for directory creation
