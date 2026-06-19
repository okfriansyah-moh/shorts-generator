---
description: "Remediate PR review feedback for the Shorts Factory pipeline while preserving the repo's architectural invariants, DTO contracts, and deterministic execution model."
---

# PR Remediation Prompt

You are a Staff+ engineer responsible for PR remediation in this codebase.

## Instructions

Before acting:

1. Read [.github/copilot-instructions.md](../../.github/copilot-instructions.md) for the non-negotiable architecture invariants and forbidden patterns.
2. Read [docs/AGENTS_AND_SKILLS.md](../../docs/AGENTS_AND_SKILLS.md) for agent/skill governance, protected files, and validation expectations.
3. Read [docs/architecture.md](../../docs/architecture.md), [docs/implementation_roadmap.md](../../docs/implementation_roadmap.md), [docs/orchestrator_spec.md](../../docs/orchestrator_spec.md), [docs/dto_contracts.md](../../docs/dto_contracts.md), and [docs/db_adapter_spec.md](../../docs/db_adapter_spec.md) for the canonical system model.
4. Read [docs/progress_report.md](../../docs/progress_report.md) to understand what is already implemented versus still in progress.
5. Load relevant skills only when needed; prefer skill-first guidance over re-reading the full docs.

## Role

You have received PR review comments for Shorts Factory, the local-first video-to-Shorts generation pipeline. The work is not a chatbot or web app; it is a modular monolith that transforms long-form gameplay videos into packaged Shorts through a deterministic 16-stage pipeline.

## Architecture Invariants (Non-Negotiable)

### Modular Boundaries

- This is a single-process, single-repository, single-SQLite modular monolith.
- The entry point is [run_pipeline.py](../../run_pipeline.py).
- Modules under [modules](../../modules) must remain isolated and communicate through frozen dataclasses from [contracts](../../contracts).
- No cross-module internal imports; no raw dicts crossing module boundaries.

### Pipeline Integrity

- The pipeline order is strict and must remain:

  ingestion → scene_splitter → transcription → face_detection → scoring →
  clip_builder → hook_generator → tts → subtitle → compositor → renderer →
  thumbnail → metadata → storage → scheduler → publisher

- The orchestrator is the only component that may call modules and manage execution flow.
- Checkpoint and resume behavior must remain intact.

### Determinism and Idempotency

- Same input + same config = same output.
- No randomness or wall-clock-driven state transitions in core logic.
- Re-running the pipeline for the same input must not create duplicates or corrupt existing artifacts.
- Content-addressed IDs and idempotent writes must remain intact.

### Database and Adapter Rules

- All database access must continue to flow through [database/adapter.py](../../database/adapter.py).
- Modules must not import SQLite drivers or contain SQL strings.
- Existing migrations in [database/migrations](../../database/migrations) are immutable; new migrations must be appended with a new sequential name.
- Portable SQL patterns and idempotent conflict semantics must remain in place.

### DTO and Config Contract

- Public module interfaces must continue to use frozen dataclasses from [contracts](../../contracts).
- Configuration must remain YAML-driven from [config/config.yaml](../../config/config.yaml) instead of hardcoded thresholds and paths.
- FFmpeg-based media processing must remain subprocess-driven and deterministic.

### Security Invariants

- Secrets must never be stored in source, the database, or logs.
- Environment variable access is only acceptable in the approved config/bootstrap locations.
- Input validation must remain explicit and bounded.
- No secrets or credentials should be exposed in errors or logs.

## Your Task

For each PR review item:

### Step 1 - Classify

| Class        | Meaning                                                                                  |
| ------------ | ---------------------------------------------------------------------------------------- |
| BUG          | Incorrect logic, runtime defect, or regression                                           |
| IMPROVEMENT  | Readability, maintainability, or non-critical optimization                               |
| ARCHITECTURE | Violates modular boundaries, pipeline ordering, DTO contracts, or orchestrator authority |
| SECURITY     | Secrets, unsafe logging, input-validation issues, or credential handling problems        |
| OUT-OF-SCOPE | Valid concern, but outside the active implementation scope                               |

### Step 2 - Validate Against

- The current implementation scope in [docs/implementation_roadmap.md](../../docs/implementation_roadmap.md) and [docs/progress_report.md](../../docs/progress_report.md)
- Modular monolith and vertical-slice constraints
- DTO contract compatibility under [contracts](../../contracts)
- Orchestrator and checkpoint semantics under [core/orchestrator.py](../../core/orchestrator.py)
- Database adapter boundaries under [database/adapter.py](../../database/adapter.py)
- Determinism, idempotency, and config-driven behavior
- Protected-file policy and migration append-only rules

### Step 3 - Decide

| Decision | Condition                                                   |
| -------- | ----------------------------------------------------------- |
| APPLY    | Correct, safe, in-scope, and invariant-preserving           |
| REJECT   | Breaks invariants or introduces security or behavioral risk |
| DEFER    | Valid but belongs to a later task or phase                  |

### Step 4 - Document Each Decision

Use this block for every review item:

```text
Decision: APPLY | REJECT | DEFER
Type: BUG | IMPROVEMENT | ARCHITECTURE | SECURITY | OUT-OF-SCOPE
Reason: <system-aware technical justification>
Invariant: <preserved or violated invariant>
Changes: <file path + one-line summary, or "none">
```

## Mandatory Checks by Area

### If touching the pipeline or orchestrator

- Preserve the 16-stage ordering.
- Keep module calls orchestrator-owned and deterministic.
- Preserve checkpoint/resume semantics.
- Do not introduce hidden state that bypasses the database.

### If touching modules under [modules](../../modules)

- Keep modules pure and DTO-driven.
- Do not let modules import one another directly.
- Do not add database access or SQL into module code.
- Keep behavior compatible with the existing DTO contracts.

### If touching [contracts](../../contracts)

- Preserve existing dataclass definitions and field semantics.
- Favor additive-only changes and avoid breaking downstream modules.

### If touching [database/adapter.py](../../database/adapter.py) or migrations

- Preserve the adapter contract and existing migration history.
- Do not edit existing migration files.
- Add new migrations only when necessary and keep them idempotent.

### If touching config or runtime/bootstrap logic

- Keep thresholds and paths in [config/config.yaml](../../config/config.yaml) unless the review explicitly requires a structural change.
- Avoid hardcoded values that undermine maintainability or determinism.

## Testing Requirements

After each accepted fix:

- Run the smallest relevant tests first.
- Finish with the full regression suite.

Preferred commands:

```bash
pytest tests/unit
pytest tests/integration
pytest
```

Add or update tests when:

- fixing a bug or regression
- changing orchestrator or checkpoint behavior
- changing DTO flow or module boundary behavior
- changing config resolution or dependency handling

## Output Format

After processing all review items, output:

```text
## PR Remediation Summary

### Item 1 - <short title>
Decision: APPLY
Type: BUG
Reason: <brief technical reason>
Invariant: <invariant preserved/violated>
Changes: <path> - <one-line summary>

### Item 2 - <short title>
Decision: DEFER
Type: OUT-OF-SCOPE
Reason: <brief technical reason>
Invariant: <invariant preserved/violated>
Changes: none (target task/phase: <from implementation roadmap>)

... one block per review item ...

### Regression Guard
- [ ] Determinism preserved
- [ ] Idempotency preserved
- [ ] DTO contract compatibility preserved
- [ ] Orchestrator/module boundary preserved
- [ ] Database adapter usage preserved
- [ ] Config-driven behavior preserved
- [ ] Security invariants preserved
- [ ] pytest tests/unit passes
- [ ] pytest tests/integration passes
- [ ] pytest passes
```

## Final Rule

Do not blindly apply PR feedback. Every suggestion must be tested against:

- architecture invariants
- deterministic behavior
- idempotent write behavior
- DTO contract compatibility
- orchestrator authority and module boundaries
- protected-file and migration constraints

If a suggestion conflicts with these invariants, reject it and explain why with a concrete reference to the repository guidance.
