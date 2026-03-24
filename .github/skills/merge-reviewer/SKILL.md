---
name: merge-reviewer
description: "Post-merge integration review patterns for Shorts Factory parallel development. Verifies all phases are fully implemented, checks architectural compliance, validates DTO contracts, and ensures tests pass after branch merge."
argument-hint: "Review integration of Phases 2, 3, and 4"
---

# Merge Reviewer Skill

## Purpose

Post-merge integration review for parallel phase development. Verifies that all phases merged into an integration branch are fully implemented, architecturally compliant, and test-passing.

## When to Use

This skill is invoked automatically by `scripts/run_parallel.sh` after merging all phase branches into the integration branch. It can also be invoked manually for PR review.

## Review Checklist

### 1. Phase Completeness (per phase)

For each phase, extract the task checklist from `docs/implementation_roadmap.md` and verify:

| Check        | What to Verify                                                                         |
| ------------ | -------------------------------------------------------------------------------------- |
| Migrations   | SQL files exist in `database/migrations/` with `YYYYMMDD000NNN_description.sql` naming |
| Module files | Module package exists in correct `modules/` subdirectory with `__init__.py`            |
| Contracts    | DTO definitions exist in `contracts/` as frozen dataclasses                            |
| Tests        | Test files exist in `tests/` mirroring the module structure                            |
| Config       | Any new parameters added to `config/` YAML files                                       |

### 2. Architecture Compliance

| Rule                    | Verification Command                                                                 |
| ----------------------- | ------------------------------------------------------------------------------------ |
| No cross-module imports | `grep -rn "from modules\." modules/ --include='*.py' \| grep -v __init__`            |
| No raw SQL in modules   | `grep -rn "import sqlite3\|import psycopg2" modules/ --include='*.py'`               |
| Structured logging      | `grep -rn "print(" modules/ --include='*.py' \| grep -v __pycache__` → must be empty |
| Frozen DTOs             | All classes in `contracts/` use `@dataclass(frozen=True)`                            |
| Content-addressable IDs | Video, scene, and clip IDs use SHA256-based formulas                                 |
| Determinism             | No `random`, no `time.time()` for IDs, no network-dependent behavior                 |
| DB adapter only         | Modules never import `sqlite3` — all DB access through `database/adapter.py`         |
| Type hints              | All public function signatures have type annotations                                 |

### 3. Integration Testing

```bash
# 1. Package import check
python -c "import sys; sys.path.insert(0, '.'); import importlib"

# 2. Run all tests
pytest tests/ --tb=short -q

# 3. Check for lint errors
ruff check . --quiet || flake8 . --count --select=E9,F63,F7,F82
```

### 4. Priority — Latest Phase Gets Deepest Review

When reviewing Phases 2, 3, and 4:

- **Phase 4** (latest) → full line-by-line code review of every new file
- **Phase 3** → structural review + spot-check key logic
- **Phase 2** → existence check + integration test coverage

The latest phase is the "anchor" — it depends on all earlier phases and represents the most complex integration surface.

### 5. Fix-and-Commit Protocol

If issues are found:

1. Fix the issue in-place (edit the file)
2. Re-run tests to confirm the fix doesn't regress
3. Commit: `git add -A && git commit -m "fix: post-merge review — [description]"`

Never just report issues — always fix them.

### 6. Output Format

```markdown
## Merge Review Summary

### Phases Reviewed

- Phase X: [PASS/FAIL] — [details]

### Files Verified: N

### Tests: N passed, N failed

### Issues Found: N → Fixed: N

### Architecture Compliance: PASS/FAIL
```

## Architectural Invariants

- Modular monolith — single process, single repo, single SQLite database
- No cross-module imports between `modules/*` packages
- All DB access through `database/adapter.py`
- Frozen dataclass DTOs in `contracts/`
- 16-stage pipeline sequence is immutable
- Content-addressable IDs (SHA256-based)
- Deterministic — same input + same config = identical output
- FFmpeg via subprocess — no Python video libraries
- Config via YAML — no hardcoded thresholds
- Logging via `logging` module — no `print()`
