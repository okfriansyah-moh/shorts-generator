---
name: architecture-reader
description: "Read and enforce the Shorts Factory system architecture from docs/architecture.md, docs/implementation_roadmap.md, docs/orchestrator_spec.md, docs/dto_contracts.md, docs/db_adapter_spec.md, and contracts/. Ensures all generated code is consistent with the 18-section modular monolith architecture, 16-stage pipeline, SQLite state machine, and frozen DTO contracts."
---

## Trigger

Use this skill when:

- You need to understand the system architecture before making changes.
- You are implementing a new module or pipeline stage.
- You need to verify that proposed code aligns with architectural constraints.
- You encounter a question about module placement, DTO flow, or database schema.
- You are reviewing code for architecture compliance.

---

# Skill: Architecture Reader

## Purpose

Parse and internalize the Shorts Factory architecture from the source-of-truth documents. Provide architecture-aware code generation that respects module boundaries, DTO flow, database adapter patterns, and pipeline ordering.

## Source-of-Truth Documents

| Document                          | Sections | Purpose                                                            |
| --------------------------------- | -------- | ------------------------------------------------------------------ |
| `docs/architecture.md`            | 18       | System architecture — pipeline, modules, scoring, data model       |
| `docs/implementation_roadmap.md`  | 11       | Phase definitions, algorithms, exit criteria, priority layers      |
| `docs/orchestrator_spec.md`       | 15       | Execution model, checkpointing, resume, idempotency                |
| `docs/dto_contracts.md`           | 22 DTOs  | All DTO definitions, field types, constraints, dependency matrix   |
| `docs/db_adapter_spec.md`         | 15       | Database adapter interface, SQL compatibility, migration strategy  |
| `docs/PARALLEL_DEV.md`            | 10       | Parallel development modes, phase grouping, token optimization     |
| `.github/copilot-instructions.md` | —        | Hard architectural constraints, forbidden technologies             |
| `contracts/`                      | —        | Frozen dataclass DTO definitions — source of truth for data shapes |

## Processing Logic

```
1. Load docs/architecture.md → extract module list, pipeline stages, data model
2. Load docs/implementation_roadmap.md → extract phase definitions, algorithms, exit criteria
3. Load docs/orchestrator_spec.md → extract execution model, checkpoint rules, state machine
4. Load docs/dto_contracts.md → extract all 22 DTO definitions with fields and constraints
5. Load docs/db_adapter_spec.md → extract adapter interface, 18 functions, SQL portability rules
6. Cross-reference all documents to build unified constraint model
7. Validate any proposed change against the constraint model
```

## Architecture Quick Reference

### Pipeline (16 stages, strict order)

```
ingestion → scene_splitter → transcription → face_detection → scoring →
clip_builder → hook_generator → tts → subtitle → compositor → renderer →
thumbnail → metadata → storage → scheduler → publisher
```

### Module Communication

- Modules communicate ONLY through frozen dataclass DTOs in `contracts/`
- No direct imports between module internals
- Only the orchestrator calls modules — modules never call each other

### Database

- Single SQLite database, WAL mode
- 4 core tables: `videos`, `clips`, `scenes`, `pipeline_runs`
- ALL access through `database/adapter.py` — no raw SQL in modules
- Content-addressable IDs: `video_id = SHA256(first_10MB + file_size)[:16]`

### Identity Scheme

| Entity | ID Formula                                  |
| ------ | ------------------------------------------- |
| Video  | `SHA256(first_10MB + file_size)[:16]`       |
| Scene  | `{video_id}_{start_ms}_{end_ms}`            |
| Clip   | `SHA256(video_id + start_ms + end_ms)[:16]` |

### Output Constraints

- Duration: 30–60 seconds
- Resolution: 1080×1920 (9:16 vertical)
- Layout: gameplay (top 65%) + face cam (bottom 35%)
- Format: H.264 MP4
- Thumbnails: JPEG 1280×720

### Forbidden Technologies

| Category     | Forbidden                                                        |
| ------------ | ---------------------------------------------------------------- |
| Architecture | Microservices, Kafka, RabbitMQ, Kubernetes, Docker orchestration |
| Databases    | MongoDB, Redis, any distributed database                         |
| AI/ML        | OpenAI API, Anthropic API, LangChain, AutoGPT, any paid LLM      |
| Cloud        | AWS, GCP, Azure, any cloud compute or storage                    |
| Runtime      | Agent loops, autonomous planners, event-driven architectures     |

### Database Engine Policy

- **SQLite is the primary runtime database.** All development and testing uses SQLite.
- **PostgreSQL is allowed ONLY as a future alternative via `database/adapter.py`.**
- **Modules MUST remain database-agnostic.** No module may reference any specific database engine.
- Direct use of `psycopg2`, `asyncpg`, or any PostgreSQL driver in `modules/` is forbidden.
- The adapter is the **sole abstraction boundary** — switching engines requires changes only in `database/`.

## Validation Checklist

When reviewing proposed code, verify:

- [ ] Module lives under `modules/{name}/` with `__init__.py`
- [ ] No imports from other modules — only `contracts/` types
- [ ] All DTOs are frozen dataclasses
- [ ] Database access only through `database/adapter.py`
- [ ] No `sqlite3`/`psycopg2` imports in modules
- [ ] No `print()` — uses `logging` module
- [ ] No `random` — deterministic behavior
- [ ] Config values from YAML, not hardcoded
- [ ] Content-addressable IDs (SHA256-based)
- [ ] FFmpeg via `subprocess` — no Python video libraries
- [ ] Tests work without GPU, network, or real video files

## Failure Handling

- If an architecture document is missing, warn and proceed with available docs
- If a proposed change violates multiple rules, report ALL violations
- If documents are contradictory, flag inconsistency and ask which takes precedence
