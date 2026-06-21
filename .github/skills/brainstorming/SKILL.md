---
name: brainstorming
description: >
  Pre-implementation design skill for Shorts Factory. Use this BEFORE any creative
  work — adding pipeline stages, creating modules, modifying DTOs, changing
  architecture, or building new features. Explores intent, constraints, and design
  before any code is written.
---

# Brainstorming: Ideas Into Designs

<HARD-GATE>
Do NOT write any code, scaffold any module, or take any implementation action until
you have presented a design and the user has approved it. This applies to EVERY
request regardless of perceived simplicity.
</HARD-GATE>

---

## When to Use

**Always before:**

- Adding a new pipeline stage or module to `modules/`
- Defining or modifying a DTO in `contracts/`
- Changing orchestrator logic in `core/`
- Adding a new publisher platform
- Modifying the scoring, compositor, or clip-building logic
- Adding a new CLI flag or config key in `config/config.yaml`

**Anti-pattern:** "This is too simple to need a design."
Even a one-field DTO change can cascade across producers, consumers, the DB schema, and the orchestrator. Always check.

---

## Process

### Step 1 — Explore project context

Before asking anything, read:
- `docs/architecture.md` for the full pipeline and module inventory
- `contracts/` to see the current DTO registry
- `core/orchestrator.py` to understand execution order and checkpointing
- Any existing module most similar to what's being built

### Step 2 — Ask clarifying questions (one at a time)

Understand: purpose, which pipeline stage it sits in, what DTOs it consumes and produces, and the success criterion. Prefer multiple-choice questions.

### Step 3 — Propose 2-3 approaches

Present options with trade-offs. Lead with your recommended approach and explain why. Apply YAGNI ruthlessly — remove features that aren't needed for the current task.

### Step 4 — Present design in sections

Scale to complexity. Cover:
- Module responsibility (one sentence)
- Where it sits in the 18-stage pipeline (before/after which stage)
- DTO inputs (name, source module)
- DTO outputs (name, fields, types, constraints)
- Database interactions — orchestrator only; module returns DTOs, never writes
- FFmpeg/ML dependencies (if any)
- Error handling and idempotency strategy
- Testability (pure function? deterministic?)

Ask "looks right so far?" after each section.

### Step 5 — Write design document

Save to `docs/specs/YYYY-MM-DD-<topic>-design.md`. Include the pipeline position diagram.

### Step 6 — Spec self-review

Check the written spec for:
- Placeholders or incomplete sections (TBD, TODO)
- Internal contradictions
- DTO field types that aren't JSON-serializable (no `datetime`, `Path`, `bytes`, `set`)
- Any module that touches the DB directly (must go through orchestrator)
- Scope creep — split if needed

### Step 7 — User reviews written spec

> "Spec written at `<path>`. Review it and let me know if anything needs to change before we write the implementation plan."

Wait for approval. Do not proceed until the user confirms.

### Step 8 — Hand off to implementation

Once approved, summarize the confirmed design and signal readiness for the next step.

---

## Process Flow

```
Explore context (docs/architecture.md, contracts/, orchestrator.py)
  ↓ Ask clarifying questions (one at a time)
    ↓ Propose 2-3 approaches with trade-offs
      ↓ Present design sections → user approves each
        ↓ Write spec to docs/specs/ → self-review → user review gate
          ↓ Hand off to implementation
```

---

## Key Principles

- **One question at a time** — don't overwhelm
- **YAGNI ruthlessly** — cut anything not needed right now
- **Explore alternatives** — always propose 2-3 approaches
- **Incremental validation** — get approval section by section
- **Pipeline position matters** — every new module must slot cleanly between two existing stages without breaking the sequential order

---

## Shorts Factory Constraints (every design must respect)

### Architecture Invariants
- **Orchestrator authority**: modules are pure functions that return DTOs; only `core/orchestrator.py` reads/writes SQLite
- **DTO contracts**: all inter-module communication goes through frozen `@dataclass` DTOs defined in `contracts/`
- **No cross-module imports**: modules import from `contracts/` only, never from each other
- **Determinism**: same input file + same `config.yaml` = identical output, every run
- **Idempotency**: safe to rerun — content-addressable IDs (`SHA256[:16]`) prevent duplicates

### Tech Stack
- **Language**: Python 3.10–3.12, type hints on all public interfaces
- **Video processing**: FFmpeg subprocess calls only — no moviepy, no cv2 for video
- **ML**: faster-whisper (transcription), MediaPipe (face detection), PySceneDetect (scenes)
- **Audio synthesis**: Edge TTS only (free, local, no API key)
- **Image processing**: Pillow only
- **Database**: SQLite3, WAL mode, accessed only via `database/adapter.py`
- **No paid APIs** unless the user explicitly introduces one

### DTO Rules
- `@dataclass(frozen=True)` — immutable after creation
- No methods, properties, or `__post_init__` logic
- All fields typed (PEP 484)
- JSON-serializable types only: `str`, `int`, `float`, `bool`, `None`, `list`, `tuple`, nested frozen DTOs
- Forbidden: `datetime`, `Path`, `bytes`, `set`, `complex`, class instances

### Pipeline Stage Order (18 stages)
```
1.  ingestion          → IngestionResult
2.  scene_splitter     → SceneList
3.  transcription      → Transcript
4.  face_detection     → FaceDetectionResult
5.  audio_analysis     → AudioEnergyData
6.  scoring            → ScoredSceneList
7.  clip_builder       → ClipList
──── per-clip loop ────
8.  hook_generator     → HookResult
9.  tts                → TTSResult
10. subtitle           → SubtitleResult
11. compositor         → CompositeStream
12. renderer           → RenderedClip
13. thumbnail          → ThumbnailResult
14. metadata           → MetadataResult
──── batch-level ──────
15. storage            → StorageRecord
16. scheduler          → (assigns publish dates)
17. publisher          → (multi-platform async upload)
18. (reporting/cleanup)
```
Any new module must identify exactly where it inserts and what it receives/returns.

### Config Keys
All tunable parameters live in `config/config.yaml`. Per-account overrides go in `config/accounts/<name>/account.yaml` (deep-merged at runtime). Never hardcode values that belong in config.

---

## Design Checklist

- [ ] Read `docs/architecture.md` and relevant `contracts/` files
- [ ] Asked clarifying questions (one at a time)
- [ ] Identified pipeline position (before/after which stage)
- [ ] Proposed 2-3 approaches with trade-offs
- [ ] DTO inputs and outputs fully defined (field names, types, constraints)
- [ ] Database interaction confirmed to go through orchestrator only
- [ ] No cross-module imports introduced
- [ ] Determinism and idempotency addressed
- [ ] Tech stack constraints respected (FFmpeg, Pillow, Edge TTS, no paid APIs)
- [ ] Design presented in sections, user approved each section
- [ ] Spec written to `docs/specs/YYYY-MM-DD-<topic>-design.md`
- [ ] Spec self-review passed (no TODOs, no contradictions, no scope creep)
- [ ] User reviewed and approved spec
- [ ] Ready to hand off to implementation
