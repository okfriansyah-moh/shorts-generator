---
name: modularity
description: "Module boundary enforcement for Shorts Factory. Use when creating modules, reviewing imports, or validating the modular monolith architecture. Prevents cross-module imports, enforces package structure, and defines file ownership per phase."
---

# Module Boundary Skill

## When to Use

- Creating a new module under `modules/`
- Reviewing imports for cross-module violations
- Validating file ownership during parallel development
- Checking that modules expose only their public contract

## Core Rules

### 1. Module Package Structure

Every module follows this pattern:

```
modules/{module_name}/
├── __init__.py          # Public API — exports ONLY the entry function
├── {module_name}.py     # Core implementation
└── (internal helpers)   # Private, never imported externally
```

### 2. Import Rules

```python
# ✅ ALLOWED — contracts are shared
from contracts.ingestion import IngestionResult
from contracts.scenes import SceneList, SceneSegment

# ✅ ALLOWED — stdlib
import logging
import subprocess
import hashlib
import os
import json

# ✅ ALLOWED — approved third-party (within module)
# (only if declared in project dependencies)

# ❌ FORBIDDEN — cross-module import
from modules.scene_splitter.detector import detect_scenes
from modules.transcription.whisper_wrapper import transcribe
from modules.scoring.formula import compute_score

# ❌ FORBIDDEN — internal access to another module
from modules.renderer.ffmpeg_utils import run_ffmpeg
```

### 3. Public API Contract

Each module exposes exactly ONE entry function:

```python
# modules/scoring/__init__.py
from .scoring import process

# Signature pattern:
def process(
    scene_list: SceneList,
    transcript: Transcript,
    face_result: FaceDetectionResult,
    config: dict
) -> ScoredSceneList:
    """Score all scenes using 5-factor composite formula."""
    ...
```

**Rules:**

- Input: frozen dataclass DTOs from `contracts/`
- Output: frozen dataclass DTO from `contracts/`
- Config: dict from YAML (passed by orchestrator)
- No side effects visible to other modules
- No shared mutable state

### 4. What Can Be Shared

| Package         | Who Can Import         | Contains                                         |
| --------------- | ---------------------- | ------------------------------------------------ |
| `contracts/`    | All modules            | Frozen dataclass DTOs only                       |
| `config/`       | Orchestrator only      | YAML config files                                |
| `database/`     | Orchestrator only      | DB adapter + engine implementations + migrations |
| `orchestrator/` | `run_pipeline.py` only | Pipeline sequencing                              |

**Modules may NOT import from:**

- Other `modules/*` packages
- `orchestrator/`
- `database/` (the orchestrator handles all DB access via `database/adapter.py` — see `docs/db_adapter_spec.md`)
- `sqlite3`, `psycopg2`, or any database driver directly

## File Ownership Per Phase

| Phase   | Owned Files                                                                         | DO NOT TOUCH             |
| ------- | ----------------------------------------------------------------------------------- | ------------------------ |
| Phase 0 | `core/`, `database/`, `config/`, `run_pipeline.py`                                  | `modules/`, `contracts/` |
| Phase 1 | `modules/ingestion/`, `modules/scene_splitter/`                                     | Other modules            |
| Phase 2 | `modules/transcription/`, `modules/face_detection/`                                 | Other modules            |
| Phase 3 | `modules/scoring/`                                                                  | Other modules            |
| Phase 4 | `modules/clip_builder/`                                                             | Other modules            |
| Phase 5 | `modules/compositor/`                                                               | Other modules            |
| Phase 6 | `modules/hook_generator/`, `modules/tts/`, `modules/subtitle/`, `modules/renderer/` | Other modules            |
| Phase 7 | `modules/thumbnail/`, `modules/metadata/`                                           | Other modules            |
| Phase 8 | `modules/storage/`, `modules/scheduler/`                                            | Other modules            |
| Phase 9 | `modules/publisher/`                                                                | Other modules            |

**In parallel mode:** Only modify files in YOUR phase's ownership column. Treat everything else as read-only.

## Anti-Patterns

```python
# ❌ Module reads another module's output file directly
with open(f"output/{video_id}/scenes.json") as f:
    scenes = json.load(f)

# ✅ Module receives data via DTO from orchestrator
def process(scene_list: SceneList, config: dict) -> ScoredSceneList:
    for scene in scene_list.scenes:
        ...

# ❌ Shared mutable state
GLOBAL_CACHE = {}  # Mutable global accessed by multiple modules

# ❌ Module instantiates another module
from modules.transcription import process as transcribe
transcript = transcribe(ingestion_result, config)  # Only orchestrator does this

# ✅ Module is called only by orchestrator
# orchestrator/pipeline.py:
transcript = transcription.process(ingestion_result, config)
scored = scoring.process(scene_list, transcript, face_result, config)
```

## Checklist

Before committing:

- [ ] No imports from `modules.*` in any module (only `contracts.*`)
- [ ] Module `__init__.py` uses relative imports (`from .X import Y`, NOT `from modules.X.Y import Y`)
- [ ] Module `__init__.py` exports only the public entry function
- [ ] No global mutable state
- [ ] No direct file reads from another module's output directory
- [ ] Config values passed in, not read directly from YAML inside module
- [ ] Database access is NOT performed inside modules (orchestrator handles it)
- [ ] No modifications to `database/`, `docs/`, or `core/` (unless Phase 0)
