---
name: config-validation
description: "Configuration validation for Shorts Factory. Use when implementing config loading, adding new parameters, or reviewing code for hardcoded magic numbers. Provides the config.yaml structure reference and validation rules."
---

# Configuration Validation Skill

## When to Use

- Implementing a module that reads configuration
- Adding a new configurable parameter
- Reviewing code for hardcoded magic numbers
- Validating config.yaml structure

## Core Invariant

> All thresholds, paths, and tunable parameters live in `config/config.yaml`. No hardcoded values in module code.

## config.yaml Structure

```yaml
# config/config.yaml — canonical structure

paths:
  output_dir: "output"
  temp_dir: "output/temp"
  database: "output/shorts_factory.db"

ingestion:
  min_duration_seconds: 1800 # 30 minutes
  max_duration_seconds: 7200 # 2 hours
  supported_formats: ["mp4", "mkv", "avi", "mov", "webm"]

scene_splitter:
  threshold: 27.0 # PySceneDetect threshold
  min_scene_duration: 3.0 # seconds
  max_scene_duration: 20.0 # seconds

transcription:
  model_size: "base" # faster-whisper model: tiny/base/small/medium/large
  language: "en"
  beam_size: 5

face_detection:
  sample_fps: 2 # Frames per second to sample
  min_confidence: 0.7 # MediaPipe face detection threshold
  min_face_size: 0.05 # Minimum face area as fraction of frame

scoring:
  weights:
    keyword: 0.20
    audio_energy: 0.25
    face_presence: 0.15
    scene_activity: 0.25
    sentence_density: 0.15
  min_composite_score: 0.4 # Minimum to be clip-eligible

clip_builder:
  target_duration_min: 30 # seconds
  target_duration_max: 60 # seconds
  max_clips_per_video: 15
  min_clips_per_video: 1
  max_overlap_ratio: 0.5

hook_generator:
  max_hook_words: 15
  max_story_words: 40
  templates_per_style: 5

tts:
  voice: "en-US-ChristopherNeural"
  rate: "+0%"
  volume: "+0%"
  output_format: "mp3"
  sample_rate: 44100

subtitle:
  font_size: 48
  font_name: "Arial"
  outline_width: 3
  margin_bottom: 150 # pixels from bottom

compositor:
  gameplay_ratio: 0.65 # Top 65% of frame
  facecam_ratio: 0.35 # Bottom 35% of frame
  output_width: 1080
  output_height: 1920

renderer:
  codec: "libx264"
  crf: 18
  preset: "medium"
  fps: 30
  max_file_size_mb: 100

thumbnail:
  width: 1280
  height: 720
  format: "jpeg"
  quality: 90
  max_text_words: 3
  font_size: 72

metadata:
  title_min_chars: 40
  title_max_chars: 60
  description_min_chars: 150
  description_max_chars: 300
  tag_count_min: 10
  tag_count_max: 15

scheduler:
  max_daily_uploads: 3
  min_gap_hours: 4
  preferred_hours: [9, 13, 18] # UTC

publisher:
  platform: "youtube"
  max_retries: 3
  retry_delays: [60, 300, 900]

pipeline:
  ffmpeg_timeout: 300 # seconds per FFmpeg command
  clip_failure_threshold: 0.5 # Abort if >50% clips fail
  disk_min_free_mb: 500
```

## Magic Number Detection

```python
# ❌ FORBIDDEN — hardcoded values
if scene.duration < 3.0:       # Where did 3.0 come from?
    skip_scene()

if len(clips) > 15:            # Why 15?
    clips = clips[:15]

crf = 18                       # Not configurable

# ✅ CORRECT — value from config
if scene.duration < config["scene_splitter"]["min_scene_duration"]:
    skip_scene()

if len(clips) > config["clip_builder"]["max_clips_per_video"]:
    clips = clips[:config["clip_builder"]["max_clips_per_video"]]

crf = config["renderer"]["crf"]
```

## Config Loading Pattern

```python
import yaml

def load_config(config_path: str = "config/config.yaml") -> dict:
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)

# Orchestrator passes config to each module
config = load_config()
result = scoring.process(scene_list, transcript, face_result, config)
```

## Environment Variable Overrides

For deployment flexibility, environment variables can override config values:

```python
import os

def apply_env_overrides(config: dict) -> dict:
    """Apply environment variable overrides using SF_ prefix."""
    # SF_OUTPUT_DIR → config["paths"]["output_dir"]
    if env_val := os.environ.get("SF_OUTPUT_DIR"):
        config["paths"]["output_dir"] = env_val

    # SF_TRANSCRIPTION_MODEL → config["transcription"]["model_size"]
    if env_val := os.environ.get("SF_TRANSCRIPTION_MODEL"):
        config["transcription"]["model_size"] = env_val

    return config
```

## Rules

1. **Never add defaults in module code** — All defaults go in `config.yaml`
2. **Modules receive config as parameter** — They don't load YAML directly
3. **Config is read-only during pipeline execution** — Never mutate config at runtime
4. **Environment variables override YAML** — `SF_` prefixed env vars take precedence
5. **Use `yaml.safe_load`** — Never `yaml.load()` (security risk)

## Checklist

Before committing:

- [ ] No numeric literals that should be configurable
- [ ] No string paths hardcoded in module code
- [ ] Config values accessed via dict keys, not hardcoded
- [ ] Module receives config as parameter from orchestrator
- [ ] New parameters added to both `config.yaml` and documentation
- [ ] `yaml.safe_load()` used, never `yaml.load()`
