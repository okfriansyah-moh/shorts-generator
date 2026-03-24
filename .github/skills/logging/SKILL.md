---
name: logging
description: "Structured logging for Shorts Factory. Use when implementing logging in any module or reviewing log output format. Defines the 7+1 required fields, stage-specific extensions, log levels, and anti-patterns."
---

# Structured Logging Skill

## When to Use

- Adding logging to a new module
- Reviewing log output for completeness
- Implementing stage-specific log extensions
- Checking for print statements or unstructured logging

## Core Invariant

> All logging uses Python stdlib `logging` with structured JSON fields. No `print()` statements. Every log entry includes the 7+1 base fields.

## 7+1 Base Fields (Required)

Every log entry MUST include:

| #   | Field       | Source                | Example                                  |
| --- | ----------- | --------------------- | ---------------------------------------- |
| 1   | `timestamp` | Auto (formatter)      | `"2025-03-24T10:30:00.000Z"`             |
| 2   | `level`     | Logger                | `"INFO"`                                 |
| 3   | `module`    | Logger name           | `"scoring"`                              |
| 4   | `stage`     | Extra                 | `"scoring"`                              |
| 5   | `video_id`  | Extra                 | `"a1b2c3d4e5f67890"`                     |
| 6   | `run_id`    | Extra                 | `"550e8400-e29b-41d4-a716-446655440000"` |
| 7   | `message`   | Logger                | `"Scene scoring complete"`               |
| +1  | `clip_id`   | Extra (per-clip only) | `"f0e1d2c3b4a59687"`                     |

## Logger Setup Pattern

```python
import logging

logger = logging.getLogger(__name__)

def process(scene_list, transcript, face_result, config):
    video_id = scene_list.scenes[0].video_id  # From input DTO

    logger.info(
        "Scoring started",
        extra={
            "stage": "scoring",
            "video_id": video_id,
            "scene_count": len(scene_list.scenes),
        }
    )

    # ... processing ...

    logger.info(
        "Scoring complete",
        extra={
            "stage": "scoring",
            "video_id": video_id,
            "clips_selected": len(result.scenes),
            "top_score": result.scenes[0].composite_score if result.scenes else 0.0,
        }
    )

    return result
```

## Log Levels

| Level      | Usage                                  | Example                                    |
| ---------- | -------------------------------------- | ------------------------------------------ |
| `DEBUG`    | Internal processing details            | `"Processing scene 12/45"`                 |
| `INFO`     | Stage start/complete, key milestones   | `"Scoring complete, 12 scenes scored"`     |
| `WARNING`  | Degraded behavior, fallbacks triggered | `"Face not detected, using gameplay-only"` |
| `ERROR`    | Recoverable failure, retrying          | `"FFmpeg render failed, retrying"`         |
| `CRITICAL` | Pipeline abort, unrecoverable          | `"Database corrupted, aborting"`           |

## Stage-Specific Extensions

### Ingestion

```python
extra={"stage": "ingestion", "video_id": video_id, "file_path": file_path,
       "duration": duration, "resolution": f"{w}x{h}", "has_audio": True}
```

### Scene Splitter

```python
extra={"stage": "scene_splitter", "video_id": video_id,
       "scene_count": count, "avg_duration": avg_dur}
```

### Transcription

```python
extra={"stage": "transcription", "video_id": video_id,
       "word_count": word_count, "language": language, "model": model_size}
```

### Scoring

```python
extra={"stage": "scoring", "video_id": video_id,
       "scenes_scored": count, "top_score": top, "min_score": min_s}
```

### Per-Clip Stages (hook_generator through storage)

```python
extra={"stage": "renderer", "video_id": video_id, "clip_id": clip_id,
       "clip_index": idx, "total_clips": total, "duration": dur}
```

### Publisher

```python
extra={"stage": "publisher", "video_id": video_id, "clip_id": clip_id,
       "platform": "youtube", "upload_status": "success"}
```

## Logging Configuration (Orchestrator)

```python
import logging
import json
import sys

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        # Merge extra fields
        for key in ("stage", "video_id", "run_id", "clip_id"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        # Include any additional extra fields
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        return json.dumps(log_entry)

def configure_logging(level: str = "INFO"):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logging.root.addHandler(handler)
    logging.root.setLevel(getattr(logging, level.upper()))
```

## Anti-Patterns

```python
# ❌ FORBIDDEN — print statements
print(f"Processing scene {scene_id}")
print("Done!")

# ❌ FORBIDDEN — unstructured logging
logger.info(f"Scored {count} scenes, top = {score}")  # No structured fields

# ❌ FORBIDDEN — logging sensitive data
logger.info("Config loaded", extra={"api_key": config["api_key"]})

# ❌ FORBIDDEN — excessive DEBUG in production
for word in transcript.words:  # 10,000 log entries
    logger.debug(f"Word: {word.text}")

# ✅ CORRECT
logger.info("Scoring complete", extra={
    "stage": "scoring",
    "video_id": video_id,
    "scenes_scored": len(scenes),
    "top_score": top_score,
})
```

## Checklist

Before committing:

- [ ] No `print()` statements anywhere
- [ ] All log entries include `stage` and `video_id` in extra
- [ ] Per-clip stages include `clip_id` in extra
- [ ] Log levels are appropriate (INFO for milestones, DEBUG for details)
- [ ] No sensitive data logged (paths are OK, credentials are not)
- [ ] Stage start and stage complete are both logged at INFO level
- [ ] Failures logged at ERROR with exception details
