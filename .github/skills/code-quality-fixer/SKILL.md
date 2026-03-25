---
name: code-quality-fixer
description: "Fix quality gate and integration verification failures in Shorts Factory. Maps each failure type to a specific fix strategy and relevant skill. Handles test failures, lint errors, cross-module imports, raw SQL in modules, print statements, and documentation gaps."
argument-hint: "Fix quality gate failures: [paste failure output]"
---

# Code Quality Fixer Skill

## Purpose

Systematically fix code quality and integration verification failures reported by the automated quality gate in `scripts/run_parallel.sh`. Each failure type has a specific fix strategy and a relevant skill to consult.

## When to Use

This skill is invoked by `scripts/run_parallel.sh` when:

- The 6-check quality gate fails on a phase branch
- The integration verification fails after merge
- A developer needs to manually fix quality issues

## Quality Gate Failures (6 Checks)

| Check # | Check              | Failure Symptom                                           | Fix Strategy                                                                                           | Relevant Skill |
| ------- | ------------------ | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | -------------- |
| 1       | Import check       | `python -c 'import modules'` fails                        | Fix syntax errors, missing `__init__.py`, broken import chains                                         | `modularity`   |
| 2       | Lint check         | `ruff check` or `flake8` reports violations               | Run `ruff check --fix` first, manually fix remaining                                                   | `logging`      |
| 3       | Test check         | `pytest tests/` reports failures                          | Fix source code or test expectations; re-run until 0 failures                                          | `testing`      |
| 4       | SQL check          | `import sqlite3` or `import psycopg2` found in `modules/` | Remove raw DB import; use `database/adapter.py` functions instead                                      | `sqlite`       |
| 5       | Cross-module check | `from modules.X` found in module Y                        | Remove import; pass data via DTOs through orchestrator, or move shared code to `contracts/` or `core/` | `modularity`   |
| 6       | Print check        | `print()` found in `modules/`                             | Replace with `logging.getLogger(__name__).info()` or appropriate log level                             | `logging`      |

## Fix Workflow

```
1. Read the failure output carefully
2. Identify which check(s) failed
3. For EACH failure:
   a. Look up the fix strategy in the table above
   b. Read the relevant skill file (.github/skills/{skill}/SKILL.md)
   c. Apply the fix
   d. Re-run the specific check command to verify
4. Run the full quality gate: ./scripts/run_parallel.sh gates
5. If all checks pass → commit: "fix: resolve quality gate failures for Phase X"
```

## Common Multi-Failure Patterns

### Pattern: New module added but not wired correctly

**Symptoms**: Check 1 (import error) + Check 4 (raw SQL)
**Root cause**: Module uses `sqlite3` directly instead of `database/adapter.py`
**Fix**: Read `sqlite` skill, replace raw SQL with adapter calls

### Pattern: Cross-module data sharing

**Symptoms**: Check 5 (cross-module import)
**Root cause**: Modules sharing data through direct imports instead of DTOs
**Fix**: Read `modularity` skill, remove cross-import, pass data via frozen DTOs through orchestrator

### Pattern: Unstructured output

**Symptoms**: Check 2 (lint) + Check 6 (print)
**Root cause**: Module uses print() instead of structured logging
**Fix**: Read `logging` skill, replace all `print()` with structured logger calls

## Verification Commands

```bash
# Full quality gate (same as ./scripts/run_parallel.sh gates)
python -c "import sys; sys.path.insert(0, '.'); import importlib" && \
ruff check . --quiet && \
pytest tests/ --tb=short -q && \
! grep -rn "import sqlite3\|import psycopg2" modules/ && \
! grep -rn "from modules\." modules/ --include='*.py' | grep -v __init__ && \
! grep -rn "^\s*print(" modules/ --include='*.py' | grep -v "# noqa"
```

## Hard Constraints

- **Never bypass architecture** — fixes must comply with all invariants in copilot-instructions.md
- **NEVER modify `database/*`** — Phase 0 only. Report database issues, don't fix them.
- **NEVER modify `docs/*`** — Read-only for all phases. Report documentation issues, don't fix them.
- **NEVER modify `core/*`** — Phase 0 only. Report core issues, don't fix them.
- **Module `__init__.py`** MUST use relative imports: `from .X import Y`, NOT `from modules.X.Y import Y`
- **Only fix files within `modules/` and `contracts/` (additive only for contracts)**
- **Never delete tests** — fix source to make tests pass, or fix test if test is wrong
- **Never use `print()`** — always `logging` module
- **Never add cross-module imports** — only `contracts/` types cross boundaries
- **Never use raw SQL in modules** — all DB access through `database/adapter.py`
- **Never modify frozen DTOs** — changes to `contracts/` require `dto-guardian` review
- **Never modify existing migration files** — always create new ones
