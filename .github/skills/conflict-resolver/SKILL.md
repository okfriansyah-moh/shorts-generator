---
name: conflict-resolver
description: "Resolve Git merge conflicts in Shorts Factory with architecture awareness. Combines code from ALL phases (union strategy), enforces module boundaries, and handles migration/test/documentation conflicts. Later phase used as tiebreaker only for truly incompatible changes."
argument-hint: "Resolve merge conflicts for Phase X (combine all phases)"
---

# Conflict Resolver Skill

## Purpose

Resolve Git merge conflicts that arise when merging parallel phase branches into an integration branch. Conflicts are resolved by **combining code from ALL phases** — a union strategy where every phase's work is preserved. The later phase is only used as a tiebreaker for truly incompatible same-function modifications that cannot coexist.

## When to Use

This skill is invoked by `scripts/run_parallel.sh` when `git merge` produces conflicts. The agent combines both sides of every conflict to preserve all code.

## Conflict Resolution Decision Tree

```
Is the file PHASE_TASK.md or .phase-complete?
  └── YES → Delete it (auto-generated, not part of system)

Is the file a documentation file? (README.md, docs/*.md)
  └── YES → Merge both sides' content (combine sections from both phases)

Is the file a migration (.sql) in database/migrations/?
  └── YES (same filename on both sides) →
       The calling script (run_parallel.sh) resolves this AUTOMATICALLY:
         - HEAD copy keeps its original filename
         - MERGE_HEAD copy is renamed: seq += 100 (e.g., 000001 → 000101)
         - Both files are git-added; no content conflict marker ever appears
       Agent action: verify both .sql files are staged; do NOT re-resolve.

Is the file an __init__.py?
  └── YES → Union of all exports from both sides

Is the file a test file?
  └── YES → Keep ALL test functions from both sides (tests are additive)

Is it a source code conflict?
  ├── Both sides ADD different code (new functions/classes)?
  │   └── Keep both additions
  ├── Both sides MODIFY the same function?
  │   └── Combine if possible; later phase as tiebreaker only if incompatible
  └── One adds, one modifies?
      └── Merge: keep the addition AND the modification
```

## Resolution Patterns

### Pattern 1: Union of Imports

```python
# HEAD (Phase 2)
from contracts.transcript import TranscriptResult

# INCOMING (Phase 3)
from contracts.scoring import ScoredSceneList

# RESOLVED — union of both
from contracts.scoring import ScoredSceneList
from contracts.transcript import TranscriptResult
```

### Pattern 2: Union of `__init__.py` Exports

```python
# HEAD
from .transcriber import transcribe

# INCOMING
from .scorer import score_scenes

# RESOLVED
from .scorer import score_scenes
from .transcriber import transcribe
```

### Pattern 3: Both Add Different Functions

```python
# HEAD adds function A
def extract_transcript(audio_path: str) -> TranscriptResult:
    ...

# INCOMING adds function B
def detect_faces(video_path: str) -> FaceDetectionResult:
    ...

# RESOLVED — keep both
def extract_transcript(audio_path: str) -> TranscriptResult:
    ...

def detect_faces(video_path: str) -> FaceDetectionResult:
    ...
```

### Pattern 4: Both Modify Same Function

```python
# HEAD modifies process()
def process(scene: SceneInfo) -> ProcessedScene:
    # Phase 2 logic
    ...

# INCOMING modifies process()
def process(scene: SceneInfo) -> ProcessedScene:
    # Phase 3 logic (later phase)
    ...

# RESOLVED — combine if possible; later phase as tiebreaker if truly incompatible
def process(scene: SceneInfo) -> ProcessedScene:
    # Phase 3 logic (later phase, used as tiebreaker)
    ...
```

### Pattern 5: Database Migration Conflicts

Migration files under `database/migrations/` follow `YYYYMMDD000NNN_description.sql` naming.

If both branches create migrations for the same date:

- Keep both files with different sequence numbers
- HEAD keeps original: `20260325000001_add_tts_cache.sql`
- Incoming gets bumped: `20260325000002_create_scheduler_queue.sql`
- Both are idempotent (`CREATE TABLE IF NOT EXISTS`) — safe to run both

### Pattern 6: Config YAML Conflicts

```yaml
# HEAD adds
tts:
  voice: "en-US-GuyNeural"

# INCOMING adds
subtitle:
  font_size: 48

# RESOLVED — merge both sections
tts:
  voice: "en-US-GuyNeural"
subtitle:
  font_size: 48
```

## Post-Resolution Validation

After resolving all conflicts, verify:

1. **No conflict markers remain:**

   ```bash
   grep -rn '<<<<<<<\|=======\|>>>>>>>' modules/ contracts/ tests/ --include='*.py'
   ```

2. **No cross-module imports introduced:**

   ```bash
   grep -rn 'from modules\.' modules/ --include='*.py' | grep -v __init__
   ```

3. **Module `__init__.py` uses relative imports (NOT absolute):**

   ```bash
   # This MUST find nothing — absolute imports are forbidden in __init__.py
   grep -rn 'from modules\.' modules/ --include='__init__.py'
   ```

4. **No raw SQL in modules:**

   ```bash
   grep -rn 'import sqlite3\|import psycopg2' modules/ --include='*.py'
   ```

5. **Package loads:**
   ```bash
   python -c "import sys; sys.path.insert(0, '.'); import importlib"
   ```

## Phase Isolation (STRICT)

- **NEVER modify `database/`** — Phase 0 only.
- **NEVER modify `docs/`** — read-only for all phases.
- **NEVER modify `core/`** — Phase 0 only.
- `contracts/` — additive only. New files OK, no field changes on existing DTOs.
- Module `__init__.py` MUST use relative imports: `from .X import Y`, NOT `from modules.X.Y import Y`.
- Violation of these rules triggers automatic pipeline rollback.

## Architectural Invariants

- Modular monolith — single process, single SQLite database
- No cross-module imports between `modules/*` packages
- All DB access through `database/adapter.py`
- All DTOs are frozen dataclasses in `contracts/`
- 16-stage pipeline sequence is immutable
- Content-addressable IDs (SHA256-based)
- Deterministic — same input + same config = identical output
