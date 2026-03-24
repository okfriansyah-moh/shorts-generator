# Parallel Development Guide — `run_parallel.sh`

> Operator guide for running multiple implementation phases simultaneously using
> autonomous AI agents. Supports 3 execution modes that balance speed, cost, and
> merge complexity.

---

## 1. Overview

The Shorts Factory pipeline has 11 phases (0–10). Most phases own isolated modules
under `modules/` and communicate only through frozen DTOs in `contracts/`. This
isolation enables parallel development — multiple phases implemented at the same time
by independent AI agents.

However, parallelism has tradeoffs:

| Dimension      | More Parallelism              | Less Parallelism        |
| -------------- | ----------------------------- | ----------------------- |
| **Speed**      | Faster wall-clock time        | Slower (sequential)     |
| **Token cost** | Higher (each agent re-reads)  | Lower (shared context)  |
| **Merge risk** | More conflicts at integration | Fewer conflicts         |
| **Debugging**  | Harder (concurrent sessions)  | Easier (single session) |

Three execution modes let the operator choose the right balance for the situation.

---

## 2. Mode Definitions

### Mode 1 — Full Parallel (Maximum Speed)

Each phase runs in a **separate Git worktree** with a **dedicated Copilot CLI agent**.
All phases execute simultaneously.

**How it works:**

```text
main
 ├─ track/phase-2   ← worktree 1, checkpoint + agent pipeline (bounded retries)
 ├─ track/phase-3   ← worktree 2, checkpoint + agent pipeline (bounded retries)
 └─ track/phase-4   ← worktree 3, checkpoint + agent pipeline (bounded retries)
```

1. Creates a branch per phase from `main`
2. Creates a Git worktree per branch (sibling directories)
3. Generates a `PHASE_TASK.md` instruction file in each worktree
4. Creates **checkpoint** (`git tag checkpoint-phase-N-pre`) in each worktree
5. Runs the **agent pipeline** per worktree with **bounded retries** (see Section 5A):
   - `phase-builder` — implements the phase (up to 5 retries)
   - `dto-guardian` — validates DTO contracts (up to 5 retries)
   - `integration` — validates module wiring (up to 5 retries)
   - `refactor` — fixes quality gate failures (up to 3 retries)
   - If any stage exceeds retry limit → rollback to checkpoint
6. Resource control: max `MAX_PARALLEL_AGENTS` (default 5) concurrent pipelines
7. Waits for all agent pipelines to finish
8. Merges all branches into an integration branch (bounded merge retries)
9. Runs global validation + creates PR

**Pros:**

- Fastest total wall-clock time (all phases run at once)
- Complete isolation — agents cannot interfere with each other
- Each agent gets the full context window for its phase

**Cons:**

- Highest token cost (each agent independently reads docs + skills)
- Highest merge complexity (conflicts between concurrent changes)
- Requires more disk space (one full repo copy per worktree)

**When to use:**

- Deadline pressure — need maximum throughput
- All phases in the batch are independent (no shared file ownership)
- Running phases from the same track level (e.g., Phase 2 modules: transcription + face_detection)

---

### Mode 2 — Token-Optimized (Serial Grouping)

Multiple phases run **sequentially in a single Copilot CLI session**. No worktrees
are created. Context is shared across phases.

**How it works:**

```text
main
 └─ track/group-2-3-4  ← single branch, checkpoint + agent pipeline (bounded retries)
     Phase 2 → commit → Phase 3 → commit → Phase 4 → commit
     dto-guardian → integration → refactor (bounded retries) → global validation
```

1. Creates a single branch from `main`
2. Generates a single `PHASE_TASK.md` with all phases listed in order
3. Creates **checkpoint** (`git tag checkpoint-group-2-3-4-pre`)
4. Runs the **agent pipeline** with **bounded retries** (see Section 5A):
   - `phase-builder` — implements all phases sequentially (up to 5 retries)
   - `dto-guardian` — validates DTO contracts (up to 5 retries)
   - `integration` — validates module wiring (up to 5 retries)
   - `refactor` — fixes quality gate failures (up to 3 retries)
   - If any stage exceeds retry limit → rollback to checkpoint
5. Each phase is committed before starting the next
6. Runs global validation + creates PR

**Pros:**

- Lowest token cost (~60–70% savings vs Mode 1)
- Context carries forward — the agent retains knowledge of earlier phases
- No merge conflicts (single branch, single session)
- Simplest to debug (one session transcript)

**Cons:**

- Slowest total wall-clock time (sequential execution)
- If the agent fails mid-session, earlier phases are still committed
- Context window may fill if too many phases are grouped (max 3 recommended)

**Grouping rules:**

- Maximum **3 phases per session** (beyond this, context window saturates)
- Phases must be in dependency order (earlier phases first)
- DTO-producing phases go before DTO-consuming phases

**Example groupings:**

```text
Session A: Phase 0 → Phase 1           (infrastructure → skeleton)
Session B: Phase 2 → Phase 3           (signals → scoring)
Session C: Phase 4 → Phase 5 → Phase 6 (clip builder → compositor → renderer)
Session D: Phase 7 → Phase 8           (metadata/thumbnail → storage/scheduler)
Session E: Phase 9 → Phase 10          (publisher → analytics)
```

**When to use:**

- Cost-sensitive development (limited premium requests)
- Phases have sequential dependencies (each depends on the previous)
- Debugging a specific pipeline section end-to-end

---

### Mode 3 — Hybrid (Balanced)

Groups of phases run **in parallel across groups**, but **sequentially within each
group**. Combines the isolation of Mode 1 with the context sharing of Mode 2.

**How it works:**

```text
main
 ├─ track/group-a  ← worktree 1, checkpoint + agent pipeline (bounded retries)
 └─ track/group-b  ← worktree 2, checkpoint + agent pipeline (bounded retries)
```

1. Groups phases by dependency and file ownership
2. Creates a branch + worktree per group
3. Creates **checkpoint** per group (`git tag checkpoint-group-X-pre`)
4. Each group runs the **agent pipeline** with **bounded retries** (see Section 5A):
   - `phase-builder` — implements phases sequentially within group (up to 5 retries)
   - `dto-guardian` — validates DTO contracts (up to 5 retries)
   - `integration` — validates module wiring (up to 5 retries)
   - `refactor` — fixes quality gate failures (up to 3 retries)
   - If any stage exceeds retry limit → rollback to checkpoint
5. Resource control: max `MAX_PARALLEL_AGENTS` (default 5) concurrent pipelines
6. Groups execute in parallel (independent worktrees)
7. Merges all group branches into integration branch (bounded merge retries)
8. Runs global validation + creates PR

**Pros:**

- Moderate token cost (~40–50% savings vs Mode 1)
- Moderate speed (parallel across groups)
- Lower merge risk (fewer branches, grouped by dependency)
- Within each group, context carries forward

**Cons:**

- Slightly more complex than Mode 2
- Still some merge risk at group boundaries
- Requires understanding of phase dependencies

**Grouping strategy:**

```text
Group A: Phase 0 → Phase 1              (infrastructure chain)
Group B: Phase 2 → Phase 3              (signal extraction → scoring)
Group C: Phase 4 → Phase 5 → Phase 6    (clip building → compositing → rendering)
Group D: Phase 7 → Phase 8              (metadata + thumbnail → storage)
Group E: Phase 9 → Phase 10             (publisher → analytics)
```

Groups A+B can run in parallel (after Phase 1 DTOs exist on `main`).
Groups C+D can run in parallel (after scoring DTOs exist on `main`).

**When to use:**

- Default choice for most development sessions
- Balance between speed and cost
- Phases have natural groupings by pipeline section

---

## 3. Mode Selection Strategy

```text
                        ┌─────────────────────┐
                        │  How many phases?    │
                        └─────────┬───────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼              ▼
               1 phase      2–3 phases      4+ phases
                    │             │              │
                    ▼             ▼              ▼
              Mode 2         Mode 2          ┌──────┐
           (single session)  (single session)│ Are   │
                                             │ they  │
                                             │ indep?│
                                             └──┬───┘
                                           yes  │  no
                                            ┌───┘───┐
                                            ▼       ▼
                                         Mode 1   Mode 3
                                       (full par) (hybrid)
```

| Scenario                                    | Recommended Mode |
| ------------------------------------------- | ---------------- |
| Single phase implementation                 | Mode 2           |
| 2–3 phases with sequential dependency       | Mode 2           |
| 2–3 fully independent phases                | Mode 1           |
| 4+ phases, mix of dependent and independent | Mode 3           |
| Cost-constrained (limited premium requests) | Mode 2           |
| Deadline pressure, all phases independent   | Mode 1           |
| Default / unsure                            | Mode 3           |

---

## 4. Phase Grouping Rules

### Safe Groupings (Low Conflict Risk)

These phases touch **different files** and can safely run in parallel (Mode 1) or be
grouped sequentially (Mode 2/3):

| Group             | Phases | Modules                                       | Why Safe                                 |
| ----------------- | ------ | --------------------------------------------- | ---------------------------------------- |
| Infrastructure    | 0 → 1  | core, database, ingestion, scene_splitter     | Foundation chain — sequential dependency |
| Signal Extraction | 2      | transcription, face_detection                 | Independent inputs, independent modules  |
| Scoring Chain     | 3 → 4  | scoring, clip_builder                         | Sequential dependency, no file overlap   |
| Composition Chain | 5 → 6  | compositor, hook_gen, tts, subtitle, renderer | Sequential + per-clip parallel modules   |
| Output Generation | 7      | thumbnail, metadata                           | Fully independent modules                |
| Storage Chain     | 8 → 9  | storage, scheduler, publisher                 | Sequential dependency                    |
| Analytics         | 10     | analytics                                     | Fully independent, read-only             |

### Safe Parallel Combinations (Mode 1 / Mode 3)

These groups can run **simultaneously** because they own different files:

```text
✅ Group B (Phase 2–3) ‖ Group D (Phase 7–8)    — no shared files
✅ Group C (Phase 4–6) ‖ Group E (Phase 9–10)   — no shared files
✅ Phase 2 modules internally: transcription ‖ face_detection — independent inputs
✅ Phase 6 modules internally: hook_gen ‖ tts ‖ subtitle      — independent outputs
✅ Phase 7 modules internally: thumbnail ‖ metadata           — independent outputs
```

### Unsafe Groupings (High Conflict Risk)

These combinations should **never** be parallelized:

| Combination                  | Risk                                                                 |
| ---------------------------- | -------------------------------------------------------------------- |
| Phase 0 ‖ anything else      | Phase 0 creates `config/`, `database/`, `core/` — touches everything |
| DTO changes ‖ module changes | Module depends on DTO definition — merge will break                  |
| orchestrator ‖ any module    | Orchestrator wires modules — concurrent changes conflict             |
| Phase 1 ‖ Phase 2            | Phase 2 requires Phase 1's `IngestionResult` DTO to exist            |
| Phase 3 ‖ Phase 4            | Phase 4 requires Phase 3's `ScoredSceneList` to exist                |
| Phase 5 ‖ Phase 6            | Phase 6 renderer integrates Phase 5's `CompositeStream`              |

### File Ownership Matrix

Each phase owns specific files. Parallel phases MUST NOT share file ownership.

| Phase | Owned Directories                                                                   | Owned Contracts                                                                             |
| ----- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| 0     | `core/`, `database/`, `config/`, `run_pipeline.py`                                  | —                                                                                           |
| 1     | `modules/ingestion/`, `modules/scene_splitter/`, `orchestrator/`                    | `contracts/ingestion.py`, `contracts/scenes.py`                                             |
| 2     | `modules/transcription/`, `modules/face_detection/`                                 | `contracts/transcript.py`, `contracts/face.py`, `contracts/audio.py`                        |
| 3     | `modules/scoring/`                                                                  | `contracts/scoring.py`                                                                      |
| 4     | `modules/clip_builder/`                                                             | `contracts/clips.py`                                                                        |
| 5     | `modules/compositor/`                                                               | `contracts/compositor.py`                                                                   |
| 6     | `modules/hook_generator/`, `modules/tts/`, `modules/subtitle/`, `modules/renderer/` | `contracts/hooks.py`, `contracts/tts.py`, `contracts/subtitles.py`, `contracts/renderer.py` |
| 7     | `modules/thumbnail/`, `modules/metadata/`                                           | `contracts/thumbnail.py`, `contracts/metadata.py`                                           |
| 8     | `modules/storage/`, `modules/scheduler/`                                            | `contracts/storage.py`                                                                      |
| 9     | `modules/publisher/`                                                                | —                                                                                           |
| 10    | `modules/analytics/` (if created)                                                   | —                                                                                           |

---

## 5. Token Cost Optimization Strategy

### Mode 2 Savings (Sequential Grouping)

When phases run sequentially in one session, the agent reads docs **once** and
reuses context across all phases in the group:

```text
Mode 1 (3 phases, 3 agents):
  Agent 1: reads architecture.md + roadmap + dto_contracts + skills = ~15K tokens
  Agent 2: reads architecture.md + roadmap + dto_contracts + skills = ~15K tokens
  Agent 3: reads architecture.md + roadmap + dto_contracts + skills = ~15K tokens
  Total context reads: ~45K tokens

Mode 2 (3 phases, 1 agent):
  Agent 1: reads architecture.md + roadmap + dto_contracts + skills = ~15K tokens
  (context persists for phases 2 and 3)
  Total context reads: ~15K tokens
  Savings: ~67%
```

### Skill-First Loading

All agents use the skills system from `.github/skills/` instead of re-reading
full documentation:

| Full Doc                    | Tokens | Equivalent Skill       | Tokens | Savings |
| --------------------------- | ------ | ---------------------- | ------ | ------- |
| `docs/architecture.md`      | ~5000  | pipeline + modularity  | ~600   | 88%     |
| `docs/dto_contracts.md`     | ~6000  | dto skill              | ~400   | 93%     |
| `docs/orchestrator_spec.md` | ~4000  | pipeline + idempotency | ~500   | 88%     |
| `docs/db_adapter_spec.md`   | ~3000  | sqlite skill           | ~400   | 87%     |

### Phase-Specific Skill Loading

Each phase only loads the skills it needs (from `.github/skills/`):

| Phase | Required Skills                                              |
| ----- | ------------------------------------------------------------ |
| 0     | config-validation, sqlite, logging, idempotency              |
| 1     | ffmpeg, pyscenedetect, dto, modularity, determinism, testing |
| 2     | faster-whisper, mediapipe, ffmpeg, dto, modularity, testing  |
| 3     | dto, determinism, testing                                    |
| 4     | dto, determinism, testing                                    |
| 5     | ffmpeg, dto, modularity, testing                             |
| 6     | edge-tts, ass-subtitle, ffmpeg, dto, modularity, testing     |
| 7     | pillow, dto, modularity, testing                             |
| 8     | sqlite, idempotency, dto, testing                            |
| 9     | dto, failure, testing                                        |
| 10    | logging, testing                                             |

Core skills loaded by **every** phase: `dto`, `modularity`, `determinism`, `testing`.
Technical skills loaded **only when needed**: `ffmpeg`, `pyscenedetect`, `faster-whisper`, etc.

### Token Optimization Rules

These rules are **mandatory** for all parallel development agents:

1. **Reuse context within session** — Never re-read a document or skill that is already loaded in the current agent session
2. **Skills first, docs second** — Always load skills before falling back to raw documentation. Skills contain 90%+ of needed constraints in ~10% of the tokens
3. **Prefer grouped execution** — When cost is a concern, use Mode 2 or Mode 3 to share context across phases within a group
4. **No full-doc reads** — Agents MUST NOT read `architecture.md`, `dto_contracts.md`, or `orchestrator_spec.md` in full. Load the relevant skill, then deep-dive into specific doc sections only if the skill references them
5. **Progressive loading** — Start with skill discovery (~100 tokens), then skill body (~300-500 tokens), then doc section (~1000 tokens) only if needed
6. **Avoid reloading docs** — If a skill provides the needed constraint, do NOT also load the source document. The skill IS the authoritative compressed reference
7. **Skill injection is automatic** — The `run_parallel.sh` script injects `Use skills: dto, pipeline, modularity, determinism, idempotency, testing` into every Copilot call. Agents should NOT re-declare these or load them manually

---

## 6. Resilience Framework

### Universal Retry Pattern

ALL stages follow the same deterministic execution pattern:

```text
execute → validate → fix → re-validate → bounded retry → success OR rollback
```

Every code path terminates in a defined state — **no infinite loops, no undefined state**.

### Retry Configuration

```bash
MAX_RETRIES_PHASE_BUILDER=5
MAX_RETRIES_DTO=5
MAX_RETRIES_INTEGRATION=5
MAX_RETRIES_MERGE=5
MAX_RETRIES_GLOBAL_VALIDATION=5
MAX_REMEDIATION_RETRIES=3          # quality gate remediation within pipeline
MAX_PARALLEL_AGENTS=5              # resource control
```

All retry limits are bounded. The system is **guaranteed to terminate**.

### Checkpoint & Rollback

Before each phase/group, a Git tag checkpoint is created:

```bash
git tag checkpoint-${phase_label}-pre
```

If any stage exceeds its retry limit:

```bash
git reset --hard checkpoint-${phase_label}-pre
```

On success, the checkpoint is cleaned up:

```bash
git tag -d checkpoint-${phase_label}-pre
```

### Stage-Specific Validation + Fix Logic

#### 3.1 Phase Builder

| Aspect      | Detail                                                              |
| ----------- | ------------------------------------------------------------------- |
| Execute     | Copilot `phase-builder` agent implements phase from `PHASE_TASK.md` |
| Validate    | Module compiles, no syntax errors, imports valid                    |
| Fix         | `refactor` agent fixes compilation issues only                      |
| Max retries | 5                                                                   |
| On failure  | Rollback to checkpoint                                              |

#### 3.2 DTO Guardian (STRICT)

| Aspect      | Detail                                                                             |
| ----------- | ---------------------------------------------------------------------------------- |
| Execute     | Copilot `dto-guardian` agent validates contracts/                                  |
| Validate    | All dataclasses frozen, no missing/extra fields, no mutable defaults, no raw dicts |
| Fix         | `dto-guardian` agent fixes DTO-specific issues only                                |
| Max retries | 5                                                                                  |
| On failure  | Rollback to checkpoint                                                             |

#### 3.3 Integration Agent

| Aspect      | Detail                                                                                            |
| ----------- | ------------------------------------------------------------------------------------------------- |
| Execute     | Copilot `integration` agent validates cross-module wiring                                         |
| Validate    | No cross-module imports, no DB in modules, no adapter imports, no print(), deterministic ordering |
| Fix         | `refactor` agent removes violations                                                               |
| Max retries | 5                                                                                                 |
| On failure  | Rollback to checkpoint                                                                            |

#### 3.4 Merge Stage

| Aspect      | Detail                                                   |
| ----------- | -------------------------------------------------------- |
| Execute     | `git merge` per branch                                   |
| Validate    | No conflict markers, code compiles, no overwritten logic |
| Fix         | `integration` agent resolves conflicts (union strategy)  |
| Max retries | 5                                                        |
| On failure  | `git merge --abort` + skip branch                        |

#### 3.5 Global Validation (CRITICAL)

Runs **after ALL phases are merged**. This is the final gate.

| Aspect     | Detail                                                                                      |
| ---------- | ------------------------------------------------------------------------------------------- |
| Checks     | All 10 quality gates + DTO flow across all modules + orchestrator authority (comprehensive) |
| Fix        | `refactor` agent (up to 5 remediation attempts)                                             |
| On failure | System enters defined `remediation_failed` state — operator intervention required           |

### Per-Mode Recovery

| Mode   | Failure Scope          | Recovery Action                                                  |
| ------ | ---------------------- | ---------------------------------------------------------------- |
| Mode 1 | Single agent fails     | Rollback to checkpoint. Other agents continue.                   |
| Mode 1 | Merge conflict         | Integration agent with bounded retry (up to 5 attempts).         |
| Mode 2 | Agent fails mid-group  | Rollback to checkpoint. Earlier commits preserved.               |
| Mode 2 | Context window full    | Split remaining phases into new session.                         |
| Mode 3 | Single group fails     | Rollback to checkpoint. Other groups continue.                   |
| Mode 3 | Merge conflict         | Same as Mode 1 — integration agent with bounded retry.           |
| All    | Global validation fail | Refactor agent (up to 5 attempts). Then: defined `failed` state. |

### Quality Gate Checks (All Modes)

After all phases complete and merge, the global validation runs:

1. **Import check** — `python -c 'import modules'` succeeds
2. **Lint check** — No lint errors in modified files
3. **Test check** — `pytest tests/ --tb=short -q` passes
4. **SQL check** — No `sqlite3` imports in `modules/` directories
5. **Cross-module check** — No `from modules.X` imports in other modules
6. **Print check** — No `print()` statements in `modules/`
7. **DTO validation** — All dataclasses in `contracts/` are frozen; no raw dicts returned
8. **Orchestrator integrity** — No `from database` or `import adapter` in `modules/`
9. **Protected files** — Warns if `contracts/`, `database/`, or `docs/` were modified
10. **Deterministic ordering** — No unordered dict/set iteration without `sorted()`

Gates 1–8 are **blocking** (cause failure). Gate 9 is **advisory**. Gate 10 is **advisory**.

### Merge Conflict Strategy

Conflicts are resolved by spawning the **integration agent** with bounded retries (not `git checkout --theirs`):

```text
Merge conflict detected
  → spawn integration agent with conflict-resolver skill (attempt 1/5)
  → combine code from BOTH sides (union strategy)
  → validate: no conflict markers, code compiles
  → IF valid → commit resolved merge
  → IF invalid → retry (up to MAX_RETRIES_MERGE)
  → IF max retries → git merge --abort + skip branch
```

**NEVER use `git checkout --theirs`** — this discards one side's work entirely.

### Deterministic Termination Guarantee

The system **guarantees** that every execution path terminates:

```text
Every stage ends in:
  ✅ success → proceed to next stage
  ❌ bounded failure → rollback to checkpoint → defined failed state

No infinite retry loops.
No undefined state.
No silent failure.
```

Terminal states:

- `passed` — all stages + global validation succeeded
- `partial_failure` — some phases failed, rolled back; others succeeded
- `merge_failed` — merge could not be resolved after max retries
- `remediation_failed` — global validation could not be fixed after max retries

### Retry Logging

Every retry attempt is logged to `agent-chain.log`:

```text
[phase-builder] attempt 1/5 started — phase=phase-2
[phase-builder] attempt 1/5 failed — phase=phase-2
[phase-builder] attempt 2/5 started — phase=phase-2
[phase-builder] attempt 2/5 passed — phase=phase-2
[dto-guardian] attempt 1/5 started — phase=phase-2
[dto-guardian] attempt 1/5 passed — phase=phase-2
[integration] attempt 1/5 started — phase=phase-2
[integration] attempt 1/5 failed — phase=phase-2
[integration] attempt 1/5 failed → retrying after fix
[integration] attempt 2/5 started — phase=phase-2
[integration] attempt 2/5 passed — phase=phase-2
[merge] resolved successfully for track/phase-2 on attempt 1
[global-validation] passed
```

On failure:

```text
[stage] attempt 5/5 FAILED — phase=phase-2
[stage] failed after 5 retries → rollback triggered
[rollback] phase=phase-2 — reset to checkpoint-phase-2-pre — stage exceeded 5 retries
```

---

## 7. Recommended Default Mode

**Default: Mode 3 (Hybrid)**

```bash
./run_parallel.sh start 2 3 4 7     # Mode 3 is the default
```

Mode 3 is the default because it provides the best balance:

- Groups dependent phases together (avoids DTO-before-module race conditions)
- Runs independent groups in parallel (faster than pure sequential)
- Shares context within groups (cheaper than full parallel)
- Produces fewer merge conflicts than Mode 1

Override when needed:

```bash
./run_parallel.sh start --mode=1 2 3 4   # Full parallel — deadline pressure
./run_parallel.sh start --mode=2 2 3 4   # Token-optimized — cost-sensitive
```

---

## 8. Agent Pipeline & Skills

### Agent Execution Pipeline (Mandatory — Bounded Retries)

Every phase or group execution follows the same pipeline with **bounded retries per stage**.
This is enforced by `run_parallel.sh` — all 3 modes use the same pipeline.

```text
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ checkpoint   │     │ checkpoint   │     │ checkpoint   │     │ checkpoint   │
│ (git tag)   │ →→→ │             │ →→→ │             │ →→→ │             │
└──────┬──────┘     └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
       ▼                   ▼                   ▼                   ▼
┌──────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│phase-builder │   │ dto-guardian   │   │ integration   │   │ refactor      │
│ execute      │   │ execute       │   │ execute       │   │ (fix quality  │
│ validate     │   │ validate      │   │ validate      │   │  gates)       │
│ fix (if fail)│   │ fix (if fail) │   │ fix (if fail) │   │              │
│ retry ≤5     │   │ retry ≤5      │   │ retry ≤5      │   │ retry ≤3     │
└──────────────┘   └───────────────┘   └───────────────┘   └───────────────┘
      Step 1             Step 2              Step 3           Step 4 (if needed)
       │ fail after max    │ fail after max    │ fail after max   │ fail after max
       ▼                   ▼                   ▼                  ▼
  ┌──────────┐       ┌──────────┐       ┌──────────┐       ┌──────────┐
  │ ROLLBACK │       │ ROLLBACK │       │ ROLLBACK │       │ ROLLBACK │
  │ to tag   │       │ to tag   │       │ to tag   │       │ to tag   │
  └──────────┘       └──────────┘       └──────────┘       └──────────┘
```

| Step | Agent         | Purpose                                                    | Max Retries | On Max Retry    |
| ---- | ------------- | ---------------------------------------------------------- | ----------- | --------------- |
| 1    | phase-builder | Implement phase: compile, no syntax errors, imports valid  | 5           | Rollback to tag |
| 2    | dto-guardian  | Validate DTOs: frozen, correct fields, no mutable defaults | 5           | Rollback to tag |
| 3    | integration   | Validate wiring: no cross-module imports, no DB in modules | 5           | Rollback to tag |
| 4    | refactor      | Fix quality gate failures                                  | 3           | Rollback to tag |

### Agent Execution Logging (with retry awareness)

Every agent step is logged to `${LOG_DIR}/agent-chain.log` with attempt tracking:

```text
[2026-03-24T10:00:00Z] [phase-builder] attempt 1/5 started — phase=phase-2
[2026-03-24T10:15:00Z] [phase-builder] attempt 1/5 passed — phase=phase-2
[2026-03-24T10:15:01Z] [dto-guardian] attempt 1/5 started — phase=phase-2
[2026-03-24T10:16:00Z] [dto-guardian] attempt 1/5 passed — phase=phase-2
[2026-03-24T10:16:01Z] [integration] attempt 1/5 started — phase=phase-2
[2026-03-24T10:17:00Z] [integration] attempt 1/5 FAILED — phase=phase-2
[2026-03-24T10:17:01Z] [integration] attempt 2/5 started — phase=phase-2
[2026-03-24T10:18:00Z] [integration] attempt 2/5 passed — phase=phase-2
```

Rollback events are also logged:

```text
[2026-03-24T10:20:00Z] [rollback] phase=phase-3 — reset to checkpoint-phase-3-pre — stage exceeded 5 retries
```

Per-agent logs are written to individual files (per attempt):

- `${LOG_DIR}/<phase>-phase-builder-<attempt>.log`
- `${LOG_DIR}/<phase>-dto-guardian-<attempt>.log`
- `${LOG_DIR}/<phase>-integration-<attempt>.log`
- `${LOG_DIR}/<phase>-phase-builder-fix-<attempt>.log` (if fix triggered)
- `${LOG_DIR}/<phase>-refactor-<attempt>.log` (if quality gate remediation triggered)

### Agents

| Process                   | Agent            | File                                     |
| ------------------------- | ---------------- | ---------------------------------------- |
| Phase implementation      | `phase-builder`  | `.github/agents/phase-builder.agent.md`  |
| DTO contract validation   | `dto-guardian`   | `.github/agents/dto-guardian.agent.md`   |
| Module wiring validation  | `integration`    | `.github/agents/integration.agent.md`    |
| Quality gate remediation  | `refactor`       | `.github/agents/refactor.agent.md`       |
| Merge conflict resolution | `integration`    | `.github/agents/integration.agent.md`    |
| Orchestrator wiring       | `orchestrator`   | `.github/agents/orchestrator.agent.md`   |
| Post-merge review         | `merge-reviewer` | `.github/agents/merge-reviewer.agent.md` |

### Skill Enforcement (STRICT — Mandatory)

All agents MUST use skills as their **PRIMARY knowledge source**. Skills are auto-discovered from `.github/skills/*/SKILL.md`.

**Injected into every Copilot call:**

```text
Use skills: dto, pipeline, modularity, determinism, idempotency, testing

MANDATORY:
- Use ONLY skills as primary knowledge source
- DO NOT read full documentation unless skills are insufficient
- If reading docs, explain why skills are insufficient
```

Plus phase-specific skills (see Section 5 skill table).

**Rules:**

1. Agents MUST NOT read `architecture.md`, `dto_contracts.md`, or `orchestrator_spec.md` in full
2. Load the relevant skill first, then deep-dive into specific doc sections only if the skill references them
3. If an agent reads a full doc, it must explain in its output WHY skills were insufficient
4. Skills are automatically injected — agents should NOT re-declare or reload them
5. Reuse session context — never re-read a document or skill already loaded

**Architectural skills** (loaded by every agent):

- `dto` — DTO registry, validation rules, anti-patterns
- `pipeline` — 16-stage sequence, DTO flow map, parallelism matrix
- `determinism` — No-randomness enforcement
- `idempotency` — Content-addressable IDs, ON CONFLICT DO NOTHING
- `modularity` — Module boundary enforcement, import rules
- `failure` — Retry policies, abort thresholds
- `config-validation` — YAML config rules
- `logging` — Structured logging requirements
- `testing` — Test patterns, fixtures, mocking

**Technical skills** (loaded per phase, on demand):

- `ffmpeg` — FFmpeg/FFprobe subprocess patterns
- `pyscenedetect` — Scene detection configuration
- `faster-whisper` — Transcription with word timestamps
- `mediapipe` — Face detection with 2fps sampling
- `edge-tts` — TTS synthesis and caching
- `pillow` — Thumbnail composition
- `sqlite` — Database adapter patterns, WAL mode, state machines
- `ass-subtitle` — ASS subtitle format, karaoke animation
- `code-quality-fixer` — Quality gate fix strategies
- `conflict-resolver` — Architecture-aware merge conflict resolution

### Protected Files (STRICT — Enforced by Pipeline)

Agents MUST NOT modify these paths unless the phase specification explicitly requires it.
Violations trigger **pipeline rollback**.

| Path          | Protection Level | Violation Behavior                              | Agents Allowed to Modify                        |
| ------------- | ---------------- | ----------------------------------------------- | ----------------------------------------------- |
| `contracts/*` | Additive only    | Modifying existing DTO fields → **ROLLBACK**    | `phase-builder` (new DTOs only), `dto-guardian` |
| `database/*`  | Restricted       | Any modification outside Phase 0 → **ROLLBACK** | Phase 0 only, `orchestrator` agent              |
| `docs/*`      | Read-only        | Any modification → **ROLLBACK**                 | None (docs are reference material)              |

The `validate_protected_files()` function in `run_parallel.sh` enforces this policy after every agent pipeline.
Violations are **blocking** — the pipeline rolls back to the checkpoint.

```bash
copilot \
  -p "Read PHASE_TASK.md and implement Phase X. Use skills: dto, pipeline, modularity, determinism, idempotency, testing, <phase-specific-skills>."  \
  --agent=phase-builder                              \
  --model=<per-process model>                        \
  --share="${LOG_DIR}/phase-${PHASE}-session.md"
```

Auto-loaded by Copilot CLI (no flags needed):

- `.github/copilot-instructions.md` — Hard architectural constraints
- `.github/agents/*.agent.md` — Agent definitions
- `.github/skills/*/SKILL.md` — Domain skills

### Model Routing Strategy

| Process                           | Model           | Rationale                          |
| --------------------------------- | --------------- | ---------------------------------- |
| Phase implementation (heaviest)   | `claude-opus-4` | Most complex phase gets best model |
| Phase implementation (all others) | rotation pool   | Round-robin across providers       |
| Post-merge review                 | rotation pool   | Code review, moderate complexity   |
| Quality gate remediation          | rotation pool   | Fix-and-retry, lightweight         |

**Rotation pool** (round-robin): `claude-sonnet-4` → `gpt-4.1` → `claude-sonnet-4`

---

## 9. Full Workflow Examples

### Example A — Implementing Phase 2 + Phase 3 (Mode 2)

```bash
./run_parallel.sh start --mode=2 2 3
```

What happens:

1. Creates branch `track/group-2-3` from `main`
2. Generates `PHASE_TASK.md` with Phase 2 then Phase 3
3. Spawns single agent: implements Phase 2, commits, then Phase 3, commits
4. Runs quality gates
5. Creates PR

```text
Timeline: ████████████████████░░░░░░░░░░░░░░░░░░░
          Phase 2              Phase 3
          (transcription +     (scoring)
           face_detection)
Total: ~40 min, 1 agent session, ~15K context tokens
```

### Example B — Implementing Phase 2 + Phase 7 (Mode 1)

```bash
./run_parallel.sh start --mode=1 2 7
```

What happens:

1. Creates two branches: `track/phase-2`, `track/phase-7`
2. Creates two worktrees as sibling directories
3. Spawns two agents in parallel
4. Both finish independently
5. Merges both into integration branch
6. Runs quality gates
7. Creates PR

```text
Timeline: ████████████████████░░░░░░░░░░░░░░░░░░░
          Phase 2 (transcription + face_detection)
          ████████████████░░░░░░░░░░░░░░░░░░░░░░░
          Phase 7 (thumbnail + metadata)
Total: ~25 min, 2 agent sessions, ~30K context tokens
```

### Example C — Implementing Phase 2–4 + Phase 7–8 (Mode 3)

```bash
./run_parallel.sh start --mode=3 2 3 4 7 8
```

What happens:

1. Auto-groups: Group A = [2, 3, 4], Group B = [7, 8]
2. Creates two branches: `track/group-2-3-4`, `track/group-7-8`
3. Group A agent: Phase 2 → commit → Phase 3 → commit → Phase 4 → commit
4. Group B agent: Phase 7 → commit → Phase 8 → commit
5. Both groups run in parallel
6. Merges both group branches
7. Runs quality gates
8. Creates PR

```text
Timeline: ████████████████████████████████████████
          Group A: Phase 2 → Phase 3 → Phase 4
          ████████████████████░░░░░░░░░░░░░░░░░░░
          Group B: Phase 7 → Phase 8
Total: ~35 min, 2 agent sessions, ~30K context tokens
```

---

## 10. Quick Reference

### Commands

```bash
# Mode 1 — Full parallel (max speed, higher cost)
./run_parallel.sh start --mode=1 2 3 4

# Mode 2 — Token-optimized (single session, lowest cost)
./run_parallel.sh start --mode=2 2 3 4

# Mode 3 — Hybrid (default, balanced)
./run_parallel.sh start 2 3 4 7 8

# Check status
./run_parallel.sh status

# Merge branches + global validation
./run_parallel.sh merge

# Clean up worktrees and branches
./run_parallel.sh cleanup
```

### Retry Configuration

| Constant                        | Default | Controls                                      |
| ------------------------------- | ------- | --------------------------------------------- |
| `MAX_RETRIES_PHASE_BUILDER`     | 5       | Phase builder execute/validate/fix cycles     |
| `MAX_RETRIES_DTO`               | 5       | DTO guardian execute/validate/fix cycles      |
| `MAX_RETRIES_INTEGRATION`       | 5       | Integration check execute/validate/fix cycles |
| `MAX_RETRIES_MERGE`             | 5       | Per-branch merge conflict resolution          |
| `MAX_RETRIES_GLOBAL_VALIDATION` | 5       | Global validation remediation attempts        |
| `MAX_REMEDIATION_RETRIES`       | 3       | Quality gate remediation per phase            |
| `MAX_PARALLEL_AGENTS`           | 5       | Concurrent agent processes (Mode 1 & 3)       |

### Terminal States

| State                | Meaning                                      |
| -------------------- | -------------------------------------------- |
| `passed`             | All phases succeeded, all validations passed |
| `partial_failure`    | Some phases succeeded, others rolled back    |
| `merge_failed`       | Branch merge failed after all retries        |
| `remediation_failed` | Global validation failed after all retries   |

### Checkpoint Tags

```bash
# Created before each agent pipeline
checkpoint-phase-N-pre

# Cleaned up on success, preserved on failure for debugging
git tag -l 'checkpoint-*'
```

---

## Appendix — Phase Dependency Graph

```text
Phase 0 (core infrastructure)
    │
    ▼
Phase 1 (ingestion + scene_splitter)
    │
    ├────────────────────┐
    ▼                    ▼
Phase 2                Phase 2
(transcription)        (face_detection)
    │                    │
    └─────────┬──────────┘
              ▼
        Phase 3 (scoring)
              │
              ▼
        Phase 4 (clip_builder)
              │
              ▼
        Phase 5 (compositor)
              │
    ┌─────────┼────────────┐
    ▼         ▼            ▼
Phase 6     Phase 6      Phase 6
(hook_gen)  (tts)        (subtitle)
    │         │            │
    └─────────┼────────────┘
              ▼
        Phase 6 (renderer)
              │
    ┌─────────┼────────────┐
    ▼         ▼            ▼
Phase 7     Phase 7      Phase 8
(thumbnail) (metadata)   (storage)
                           │
                           ▼
                     Phase 8 (scheduler)
                           │
                           ▼
                     Phase 9 (publisher)
                           │
                           ▼
                     Phase 10 (analytics)
```
