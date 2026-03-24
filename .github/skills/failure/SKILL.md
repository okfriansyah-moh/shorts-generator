---
name: failure
description: "Failure handling for Shorts Factory. Use when implementing retry logic, abort thresholds, graceful degradation, or error recovery. Defines retry policies, failure thresholds, state transitions on error, and FFmpeg timeout handling."
---

# Failure Handling Skill

## When to Use

- Implementing retry logic for clip rendering or publishing
- Setting abort thresholds for pipeline failures
- Handling optional module failures (face detection, TTS)
- Implementing FFmpeg timeout and crash recovery

## Failure Thresholds

| Condition                    | Threshold              | Action                                     |
| ---------------------------- | ---------------------- | ------------------------------------------ |
| Clip render failures         | > 50% of clips fail    | Abort pipeline, status = `failed`          |
| Face detection failure       | > 70% scenes faceless  | Log WARN, continue with fallback layout    |
| Disk space during processing | < 500MB remaining      | Abort pipeline, clean intermediates        |
| FFmpeg process timeout       | > 300 seconds per clip | Kill process, retry once, then skip clip   |
| Empty transcript             | 0 words detected       | Log WARN, scoring uses remaining 4 signals |
| TTS synthesis failure        | Cannot generate audio  | Skip narration, use gameplay audio only    |

## Retry Policies

### Per-Clip Rendering

```
max_retries: 2
retry_action: re-render with fallback FFmpeg settings (lower bitrate, CRF 23 → 28)
dead_action: skip clip, log failure, continue to next clip
```

### Publishing (YouTube Upload)

```
max_retries: 3
retry_delays: [60, 300, 900]  # Exponential backoff in seconds
dead_action: mark as failed, alert operator, retain all assets
```

## State Transitions on Failure

### Pipeline-Level

```
started → failed           (ingestion or validation failure)
analyzing → failed          (scene splitter or transcription failure)
building → failed           (all clips or >50% clips failed)
building → partial          (some clips failed, others succeeded)
```

### Clip-Level

```
generated → failed          (rendering failure after retries exhausted)
scheduled → failed          (publish failure after 3 retries)
failed → scheduled          (manual retry, max 3 times total)
```

**Terminal states:** `published` and `failed` (after exhausting retries) are final.

## Graceful Degradation Patterns

### Face Detection Optional

```python
def compose_layout(clip: ClipDefinition, face_result: FaceDetectionResult | None, config: dict) -> CompositeStream:
    if face_result is None or face_result.average_visibility < 0.1:
        # Fallback: gameplay-only layout with zoom
        return create_gameplay_only_layout(clip, config)
    else:
        # Normal: 65/35 face+gameplay split
        return create_face_gameplay_layout(clip, face_result, config)
```

### Empty Transcript Handling

```python
def score_scene(scene: SceneSegment, transcript: Transcript | None, ...) -> float:
    keyword_score = 0.0  # No transcript → keyword score defaults to 0
    sentence_density = 0.0  # No transcript → sentence density defaults to 0
    # Remaining signals still contribute
    return compute_composite(keyword_score, audio_energy, face_presence, scene_activity, sentence_density, weights)
```

### FFmpeg Crash Recovery

```python
def run_ffmpeg_safe(command: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, timeout=timeout, capture_output=True, check=True)
        return result
    except subprocess.TimeoutExpired:
        logger.warning("FFmpeg timeout, retrying with lower quality")
        # Retry with fallback settings
        fallback_cmd = apply_fallback_settings(command)
        return subprocess.run(fallback_cmd, timeout=timeout * 2, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        logger.error("FFmpeg failed", extra={"stderr": e.stderr.decode()})
        raise
```

## Pre-Flight Checks (Run Before Pipeline Starts)

```python
def preflight_checks(input_path: str, config: dict) -> None:
    # 1. FFmpeg available
    assert shutil.which("ffmpeg"), "FFmpeg not found in PATH"
    assert shutil.which("ffprobe"), "FFprobe not found in PATH"

    # 2. Python version
    assert sys.version_info >= (3, 10), f"Python 3.10+ required, got {sys.version}"

    # 3. Disk space
    input_size = os.path.getsize(input_path)
    free_space = shutil.disk_usage(config["paths"]["output_dir"]).free
    assert free_space >= input_size * 3, f"Insufficient disk space: {free_space} < {input_size * 3}"

    # 4. Input validation
    assert os.path.isfile(input_path), f"Input file not found: {input_path}"
```

## Checklist

Before committing error handling code, verify:

- [ ] Rendering retry uses lower quality on retry (not same settings)
- [ ] Publishing retry uses exponential backoff
- [ ] Pipeline aborts if >50% clips fail
- [ ] Face detection failure falls back to gameplay-only layout
- [ ] Empty transcript doesn't crash scoring (defaults to 0 for text signals)
- [ ] FFmpeg commands have timeout parameter
- [ ] All failures are logged with structured JSON
- [ ] Pipeline status correctly transitions to `failed` or `partial`
