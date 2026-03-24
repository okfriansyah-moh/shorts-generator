---
name: testing
description: "Testing patterns for Shorts Factory. Use when writing unit tests, integration tests, or test fixtures. Provides DTO fixture generation, FFmpeg mocking, no-GPU/no-network constraints, and idempotency test patterns."
---

# Testing Patterns Skill

## When to Use

- Writing unit tests for a module
- Creating integration tests across stages
- Generating test fixtures from DTOs
- Mocking FFmpeg or external dependencies
- Validating idempotency behavior

## Core Constraints

> Tests must be runnable **without GPU**, **without network**, and **without real video files**.

## Test Organization

```
tests/
├── conftest.py              # Shared fixtures
├── unit/
│   ├── test_ingestion.py
│   ├── test_scene_splitter.py
│   ├── test_transcription.py
│   ├── test_scoring.py
│   ├── test_clip_builder.py
│   └── ...                  # One file per module
├── integration/
│   ├── test_pipeline_resume.py
│   ├── test_checkpoint_recovery.py
│   └── test_end_to_end.py
└── fixtures/
    ├── sample_config.yaml
    └── ...
```

## DTO Fixture Generation

Create test DTOs using factory functions:

```python
# tests/conftest.py
from contracts.ingestion import IngestionResult
from contracts.scenes import SceneSegment, SceneList
from contracts.transcript import Transcript, TranscriptSegment, Word
from contracts.scoring import ScoredScene, ScoredSceneList

def make_ingestion_result(**overrides) -> IngestionResult:
    defaults = {
        "video_id": "a1b2c3d4e5f67890",
        "file_path": "/tmp/test_video.mp4",
        "duration_seconds": 3600.0,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "has_audio": True,
        "file_size_bytes": 500_000_000,
    }
    defaults.update(overrides)
    return IngestionResult(**defaults)

def make_scene_segment(**overrides) -> SceneSegment:
    defaults = {
        "scene_id": "a1b2c3d4e5f67890_0_5000",
        "video_id": "a1b2c3d4e5f67890",
        "start_time": 0,
        "end_time": 5000,
        "duration": 5.0,
    }
    defaults.update(overrides)
    return SceneSegment(**defaults)

def make_scene_list(count: int = 10) -> SceneList:
    scenes = []
    for i in range(count):
        start = i * 5000
        end = start + 5000
        scenes.append(make_scene_segment(
            scene_id=f"a1b2c3d4e5f67890_{start}_{end}",
            start_time=start,
            end_time=end,
            duration=5.0,
        ))
    return SceneList(video_id="a1b2c3d4e5f67890", scenes=tuple(scenes))

def make_transcript(word_count: int = 50) -> Transcript:
    words = []
    for i in range(word_count):
        words.append(Word(text=f"word{i}", start_time=i * 200, end_time=(i + 1) * 200, confidence=0.95))
    segment = TranscriptSegment(
        start_time=0,
        end_time=word_count * 200,
        text=" ".join(w.text for w in words),
        words=tuple(words),
    )
    return Transcript(video_id="a1b2c3d4e5f67890", language="en", segments=(segment,))

def make_scored_scene_list(count: int = 10) -> ScoredSceneList:
    scenes = []
    for i in range(count):
        start = i * 5000
        end = start + 5000
        scenes.append(ScoredScene(
            scene_id=f"a1b2c3d4e5f67890_{start}_{end}",
            video_id="a1b2c3d4e5f67890",
            start_time=start,
            end_time=end,
            duration=5.0,
            keyword_score=0.5 + (count - i) * 0.03,
            audio_energy=0.6,
            face_presence=0.7,
            scene_activity=0.5,
            sentence_density=0.4,
            composite_score=0.55 + (count - i) * 0.03,
        ))
    return ScoredSceneList(
        video_id="a1b2c3d4e5f67890",
        scenes=tuple(sorted(scenes, key=lambda s: (-s.composite_score, s.start_time))),
    )
```

## FFmpeg Mocking

```python
import subprocess
from unittest.mock import patch, MagicMock

def mock_ffmpeg_success():
    """Mock subprocess.run for FFmpeg commands."""
    mock_result = MagicMock(spec=subprocess.CompletedProcess)
    mock_result.returncode = 0
    mock_result.stdout = b""
    mock_result.stderr = b""
    return patch("subprocess.run", return_value=mock_result)

def mock_ffprobe_output(duration: float = 3600.0, width: int = 1920, height: int = 1080):
    """Mock ffprobe JSON output."""
    probe_output = {
        "streams": [{
            "codec_type": "video",
            "width": width,
            "height": height,
            "r_frame_rate": "30/1",
            "duration": str(duration),
        }, {
            "codec_type": "audio",
        }],
        "format": {
            "duration": str(duration),
            "size": "500000000",
        }
    }
    import json
    mock_result = MagicMock(spec=subprocess.CompletedProcess)
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(probe_output).encode()
    mock_result.stderr = b""
    return patch("subprocess.run", return_value=mock_result)
```

## SQLite Test Database

```python
import sqlite3
import tempfile
import os

def create_test_db():
    """Create an in-memory or temp-file SQLite database with schema."""
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = sqlite3.connect(db_path)

    # Apply migrations
    migrations_dir = "database/migrations"
    for migration_file in sorted(os.listdir(migrations_dir)):
        if migration_file.endswith(".sql"):
            with open(os.path.join(migrations_dir, migration_file)) as f:
                conn.executescript(f.read())

    conn.commit()
    return conn, db_path
```

## Idempotency Test Pattern

```python
def test_pipeline_idempotency(test_db, sample_input):
    """Running pipeline twice produces identical results."""
    # First run
    result_1 = pipeline.run(sample_input, config, db=test_db)
    clips_1 = test_db.execute("SELECT * FROM clips ORDER BY clip_id").fetchall()

    # Second run (same input)
    result_2 = pipeline.run(sample_input, config, db=test_db)
    clips_2 = test_db.execute("SELECT * FROM clips ORDER BY clip_id").fetchall()

    # Same number of clips
    assert len(clips_1) == len(clips_2)

    # Same clip IDs
    assert [c[0] for c in clips_1] == [c[0] for c in clips_2]

    # No duplicates
    all_ids = [c[0] for c in clips_2]
    assert len(all_ids) == len(set(all_ids))
```

## Determinism Test Pattern

```python
def test_scoring_determinism(sample_scene_list, sample_transcript, sample_face_result):
    """Scoring produces identical results across runs."""
    config = load_test_config()

    result_1 = scoring.process(sample_scene_list, sample_transcript, sample_face_result, config)
    result_2 = scoring.process(sample_scene_list, sample_transcript, sample_face_result, config)

    for s1, s2 in zip(result_1.scenes, result_2.scenes):
        assert s1.composite_score == s2.composite_score
        assert s1.scene_id == s2.scene_id
```

## Test Config

```python
def load_test_config():
    """Load test configuration with overridden paths."""
    import yaml
    with open("config/config.yaml") as f:
        config = yaml.safe_load(f)

    # Override paths for testing
    config["paths"]["output_dir"] = tempfile.mkdtemp()
    config["paths"]["temp_dir"] = tempfile.mkdtemp()
    config["paths"]["database"] = ":memory:"

    return config
```

## Checklist

Before committing tests:

- [ ] No real video files required (use mocks or tiny test fixtures)
- [ ] No network calls (mock all external services)
- [ ] No GPU required (CPU-only or mocked)
- [ ] FFmpeg commands mocked via `subprocess.run` patch
- [ ] SQLite uses temp file or `:memory:` database
- [ ] Temp directories cleaned up (use `tempfile.mkdtemp` + cleanup)
- [ ] Idempotency verified (run twice, same results)
- [ ] Determinism verified (same input = same output)
- [ ] DTO fixtures use factory functions, not raw dicts
