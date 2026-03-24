---
name: docs-sync
description: "Synchronize documentation with current implementation state after parallel phase merges. Ensures progress_report.md, README.md, implementation_roadmap.md, and config/ accurately reflect every merged phase with detailed per-phase sections."
argument-hint: "Synchronize docs for merged Phases 2, 3, and 4"
---

# Documentation Synchronization Skill

## Purpose

After merging parallel phase branches into an integration branch, ensure that all documentation accurately and comprehensively reflects every merged phase. Each phase must have detailed per-phase sections — not just a mention, but full documentation of what was implemented, what files were created, what tests were added, and what exit criteria were met.

## When to Use

This skill is invoked by `scripts/run_parallel.sh` after the post-merge review agent completes and before the PR is created. It ensures documentation is complete.

## Files to Update

| File                             | What Must Be Updated                                                       |
| -------------------------------- | -------------------------------------------------------------------------- |
| `docs/progress_report.md`        | Per-phase completion sections with tasks, files, tests, exit criteria      |
| `README.md`                      | Repository structure, features list, architecture diagram with new modules |
| `docs/implementation_roadmap.md` | Phase task checkboxes marked `[x]`, exit criteria checked                  |
| `config/`                        | Any new configuration parameters introduced by merged phases               |

## progress_report.md Update Protocol

For **every merged phase**, add a section with this format:

```markdown
## Phase X — [Phase Name]

**Status:** ✅ COMPLETE

### Completed Tasks

- [x] Task description — brief detail of what was done
- [x] ... (one entry per task from docs/implementation_roadmap.md)

### Files Created

| File Path                         | Purpose                  |
| --------------------------------- | ------------------------ |
| modules/[name]/[file].py          | What this file does      |
| modules/[name]/**init**.py        | Package init, public API |
| contracts/[name].py               | DTO definitions          |
| database/migrations/NNNN_desc.sql | Migration for [table]    |
| tests/[name]/test\_[file].py      | Tests for [file]         |

### Exit Criteria

- [x] Exit criterion 1 from roadmap
- [x] Exit criterion 2 from roadmap

### Test Results

- **N tests passing** for this phase's modules
- **0 lint errors** (ruff/flake8 clean)
```

### How to Generate Per-Phase Content

1. Read `docs/implementation_roadmap.md` — extract the **Tasks** section for the phase
2. Scan `modules/` and `tests/` directories to find files created by this phase
3. Cross-reference: for each task in the roadmap, verify a file or test exists
4. Mark completed tasks with `[x]`, note any gaps
5. Run `pytest tests/ -v --tb=no` to get test count

### Current Status Section

```markdown
## Current Status

**Active Phase:** Phase [latest] — [Name]
**Phase Status:** ✅ COMPLETE (Verified & Audited)
**Last Updated:** [today's date]
```

## README.md Update Protocol

### Repository Structure Section

Add new directories/files from merged phases to the tree:

```
shorts-generator/
├── modules/
│   ├── ingestion/         # Phase 1
│   ├── scene_splitter/    # Phase 1
│   ├── transcription/     # Phase 2 (NEW)
│   ├── face_detection/    # Phase 2 (NEW)
│   └── scoring/           # Phase 3 (NEW)
```

### Features Section

Add capabilities that are now implemented (not aspirational). Only list features backed by actual code.

## implementation_roadmap.md Update Protocol

### Task Checkboxes

For each merged phase, convert `[ ]` to `[x]` for completed tasks:

```markdown
### Tasks

- [x] Create transcription module directory structure
- [x] Implement faster-whisper integration
- [x] Add word-level timestamp extraction
- [x] Write unit tests
```

### Exit Criteria Checkboxes

```markdown
### Exit Criteria

- [x] Transcription produces word-level timestamps
- [x] All tests pass without GPU
- [x] DTO output matches contracts/transcript.py
```

## Verification Checklist

After updating all docs, verify:

```bash
# Every merged phase has a section in progress_report.md
for phase in 2 3 4; do
  grep -q "Phase ${phase}" docs/progress_report.md && \
    echo "✅ Phase ${phase} documented" || \
    echo "❌ Phase ${phase} MISSING from progress_report.md"
done

# README.md references all new module directories
for mod in transcription face_detection scoring; do
  grep -q "${mod}" README.md && \
    echo "✅ ${mod} in README" || \
    echo "❌ ${mod} MISSING from README"
done
```
