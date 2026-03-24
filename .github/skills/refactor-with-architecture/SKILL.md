---
name: refactor-with-architecture
description: "Refactor code in Shorts Factory without violating architectural constraints. Ensures refactoring preserves modular monolith boundaries, DTO contracts, database adapter patterns, pipeline ordering, determinism guarantees, and idempotency. Validates that refactored code passes the same tests and maintains all invariants."
---

## Trigger

Use this skill when:

- You are restructuring code within or across files.
- You are extracting shared logic into `core/` or utility helpers.
- You are simplifying module implementations.
- You are moving code between files within a module.
- You need to refactor without breaking architectural constraints.

---

# Skill: Refactor With Architecture

## Purpose

Perform code refactoring that respects all architectural constraints. Every refactoring must preserve module boundaries, DTO contracts, database adapter usage, determinism, and pipeline ordering.

## Refactoring Rules

```
ALLOWED REFACTORING                          │ CONSTRAINT
─────────────────────────────────────────────┼──────────────────────────────────────────
Extract shared logic to core/ utility        │ Must not contain module-specific logic
Move code within the same module             │ Internal restructuring always allowed
Simplify processing functions                │ Must preserve determinism (same output)
Consolidate duplicate FFmpeg invocations     │ Helper stays in module or goes to core/
Rename internal functions/variables          │ Must update all references within module
Improve type hints                           │ No logic change
Fix logging format                           │ Must include all required fields

FORBIDDEN REFACTORING                        │ REASON
─────────────────────────────────────────────┼──────────────────────────────────────────
Move domain logic to core/                   │ Domain logic stays in modules/
Import from one module into another          │ Cross-module imports prohibited
Bypass database adapter                      │ All DB access through adapter.py
Remove idempotency checks for simplicity     │ Idempotency is non-negotiable
Change DTO field names or types              │ Breaks contracts — use dto-guardian
Merge two modules into one                   │ Violates single-responsibility per module
Change public API signature                  │ Breaks orchestrator wiring
Introduce randomness                         │ Determinism is non-negotiable
```

## Validation Checklist

After refactoring, verify:

- [ ] No new cross-module imports (`grep -rn "from modules\." modules/`)
- [ ] DTO contracts unchanged (all fields, types, constraints identical)
- [ ] Database access only through `database/adapter.py`
- [ ] No `sqlite3`/`psycopg2` imports in modules
- [ ] No `print()` statements — structured logging only
- [ ] No `random` — deterministic behavior preserved
- [ ] All tests still pass (`pytest tests/ --tb=short -q`)
- [ ] Same input → same output for every refactored function
- [ ] Pipeline 16-stage ordering unchanged
- [ ] Config values from YAML, not hardcoded

## Destination Rules

| Code Type        | Correct Location              | Never Put In             |
| ---------------- | ----------------------------- | ------------------------ |
| Module logic     | `modules/{name}/`             | `core/`, `contracts/`    |
| Shared utilities | `core/` (if truly shared)     | `modules/`               |
| DTO definitions  | `contracts/`                  | `modules/`, `core/`      |
| Database queries | `database/adapter.py`         | `modules/`               |
| Config loading   | `config/`                     | `modules/`, `core/`      |
| FFmpeg helpers   | Within the module, or `core/` | `contracts/`             |
| Test fixtures    | `tests/`                      | `modules/`, `contracts/` |

## Dependencies

```
modules/                        — All source code
contracts/                      — Frozen DTO definitions
database/adapter.py             — Database access layer
tests/                          — All test files
.github/copilot-instructions.md — Hard architectural constraints
docs/architecture.md            — Module responsibilities and boundaries
```
