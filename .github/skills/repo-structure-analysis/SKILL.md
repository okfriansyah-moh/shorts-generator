---
name: repo-structure-analysis
description: "Audit the Shorts Factory repository layout against the canonical target structure. Identifies files in wrong locations, missing directories, stray docs at root, and auto-generated artifacts that should be gitignored."
---

# Repo Structure Analysis Skill

## Trigger

Use this skill when:

- Auditing the current repository layout against the canonical target structure
- Identifying files that are in the wrong location
- Planning a structural refactor before moving files
- Verifying compliance after a merge or major change

---

## Canonical Target Structure

```
/                          ← Root: run_pipeline.py, README.md, .gitignore, pyproject.toml ONLY
├── modules/               ← All pipeline module packages (16 stages)
│   ├── ingestion/
│   ├── scene_splitter/
│   ├── transcription/
│   ├── face_detection/
│   ├── scoring/
│   ├── clip_builder/
│   ├── hook_generator/
│   ├── tts/
│   ├── subtitle/
│   ├── compositor/
│   ├── renderer/
│   ├── thumbnail/
│   ├── metadata/
│   ├── storage/
│   ├── scheduler/
│   └── publisher/
├── contracts/             ← Frozen dataclass DTO definitions (IMMUTABLE structure)
├── orchestrator/          ← Pipeline orchestration + checkpointing
├── database/              ← DB adapter + engine implementations + migrations
│   └── migrations/        ← SQL migration files (YYYYMMDD000NNN format)
├── config/                ← YAML configuration files
├── core/                  ← Shared utilities (if needed)
├── scripts/               ← Shell scripts ONLY
├── tests/                 ← Test suite (mirrors modules/ structure)
├── docs/                  ← All documentation except README.md
├── output/                ← Generated clips (gitignored)
└── .github/               ← Copilot agents, skills, instructions
    ├── agents/
    ├── skills/
    └── copilot-instructions.md
```

---

## Audit Workflow

### 1. Identify root violations

```bash
find . -maxdepth 1 -type f \( -name "*.md" ! -name "README.md" \) -o \
  -maxdepth 1 -type f -name "*.sh" -o \
  -maxdepth 1 -type f -name "*.txt" | sort
# Expected: empty
```

### 2. Verify docs/ contains all reference docs

```bash
ls docs/
# Expected: architecture.md, implementation_roadmap.md, orchestrator_spec.md,
#           dto_contracts.md, db_adapter_spec.md, PARALLEL_DEV.md, AGENTS_AND_SKILLS.md
```

### 3. Verify scripts/ contains all shell scripts

```bash
ls scripts/
# Expected: run_parallel.sh (and any future scripts)
```

### 4. Verify .gitignore covers auto-generated artifacts

```bash
grep -E "PHASE_TASK|phase-complete|\.parallel-dev|output/" .gitignore
# Expected: all patterns present
```

### 5. Count stray references

```bash
# Bare doc references without docs/ prefix
grep -rn "architecture\.md\b" . --include="*.md" --include="*.py" --include="*.sh" \
  | grep -v "docs/architecture.md" | grep -v ".git/" | wc -l
# Expected: 0
```

---

## Classification Rules

| Finding                                  | Severity | Action                                |
| ---------------------------------------- | -------- | ------------------------------------- |
| `.md` doc at root (not README)           | CRITICAL | `git mv` to `docs/`                   |
| `.sh` at root                            | CRITICAL | `git mv` to `scripts/`                |
| Bare doc reference (no `docs/` prefix)   | HIGH     | `perl -i -pe` bulk replace            |
| Auto-generated artifact tracked in git   | MEDIUM   | `git rm` + add to `.gitignore`        |
| Module package without `__init__.py`     | HIGH     | Create `__init__.py` with exports     |
| Test file not mirroring module structure | MEDIUM   | Move to correct `tests/` subdirectory |
| Correct path already                     | OK       | No action needed                      |

---

## Root Allowed Files

```
run_pipeline.py     ← Single entry point
README.md           ← Project README
.gitignore          ← Git ignore rules
pyproject.toml      ← Python project config (if used)
setup.py            ← Legacy Python setup (if used)
requirements.txt    ← Dependencies (if not using pyproject.toml)
```

Everything else belongs in a subdirectory.
