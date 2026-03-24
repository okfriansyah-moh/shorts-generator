---
name: doc-standardization
description: "Documentation placement and standardization rules for Shorts Factory. Use when adding new documentation, checking doc layout compliance, or reviewing PRs that add .md files. Enforces the canonical docs/ structure and prevents doc sprawl."
---

# Doc Standardization Skill

## Trigger

Use this skill when:

- Adding new documentation to the repository
- Checking whether existing docs follow the canonical placement rules
- Reviewing a PR that adds `.md` files to root
- Enforcing the documentation structure

---

## Core Documentation Files

These are the operational documentation files. All content must fit into one of these:

| File                             | Purpose                                                            |
| -------------------------------- | ------------------------------------------------------------------ |
| `README.md`                      | Master reference: setup, architecture summary, quick start         |
| `docs/implementation_roadmap.md` | Phased plan: phase-by-phase feature and milestone tracking         |
| `docs/progress_report.md`        | AI-readable review: current state, completed work, pending gaps    |
| `docs/startup_guide.md`          | Setup & troubleshooting: system requirements, dependencies, launch |

## Architecture Reference Files

| File                        | Purpose                                                   |
| --------------------------- | --------------------------------------------------------- |
| `docs/architecture.md`      | 18-section system architecture                            |
| `docs/orchestrator_spec.md` | 15-section orchestrator specification                     |
| `docs/dto_contracts.md`     | 22 DTO definitions with fields, types, and constraints    |
| `docs/db_adapter_spec.md`   | Database adapter interface, SQL compatibility, migrations |
| `docs/PARALLEL_DEV.md`      | Parallel development orchestration — 3-mode execution     |
| `docs/AGENTS_AND_SKILLS.md` | Agent/skill system documentation                          |

---

## Root Directory Rules

`README.md` is the ONLY `.md` file allowed at root. All others MUST live in `docs/`.

**Forbidden at root:**

- `ARCHITECTURE.md` → must be `docs/architecture.md`
- `QUICK_START.md` → merge into `README.md` or `docs/startup_guide.md`
- `STATUS.md` → merge into `docs/progress_report.md`
- Any `.phase-complete` or `PHASE_TASK.md` auto-generated files → add to `.gitignore`

---

## Compliance Check

```bash
# Find .md files at root that shouldn't be there
find . -maxdepth 1 -name "*.md" ! -name "README.md"
# Expected output: empty

# Find orphaned docs outside docs/
find . -name "*.md" ! -path "./.github/*" ! -path "./docs/*" \
  ! -path "./tests/*" ! -path "./.git/*" ! -name "README.md" \
  ! -path "./contracts/*"
# Expected output: empty
```

---

## Documentation Update Rules

1. **After code changes to the pipeline** → update `README.md` Architecture Summary section
2. **After adding config parameters** → update `config/` YAML with comments
3. **At end of each development session** → update `docs/progress_report.md`
4. **After schema changes** → add migration in `database/migrations/` (YYYYMMDD000NNN format)
5. **Never create supplementary docs** for one-off fixes or workarounds
6. **Never duplicate content** across docs — pick one home and reference from others

---

## Reference Documents Table

The Reference Documents table in `.github/copilot-instructions.md` must use `docs/` prefix for all entries:

```markdown
| `docs/architecture.md` | 18-section system architecture |
| `docs/implementation_roadmap.md` | 11-phase implementation roadmap |
| `docs/orchestrator_spec.md` | 15-section orchestrator specification |
| `docs/dto_contracts.md` | 22 DTO definitions with fields/types/constraints |
| `docs/db_adapter_spec.md` | Database adapter interface, SQL compatibility |
| `docs/PARALLEL_DEV.md` | Parallel development orchestration guide |
| `contracts/` | Frozen dataclass DTO definitions |
| `config/` | YAML configuration files |
```
