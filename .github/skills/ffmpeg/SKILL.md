---
name: ffmpeg
description: "FFmpeg and FFprobe patterns for Shorts Factory. Use when implementing video probing, audio extraction, scene clipping, composite rendering, subtitle burn-in, or any subprocess-based video/audio processing. Covers all FFmpeg commands, filter chains, encoding presets, and subprocess execution patterns used across the pipeline."
---

# FFmpeg / FFprobe Skill

## When to Use

- Probing video metadata (ingestion)
- Extracting audio for transcription
- Extracting frames for face detection or thumbnail scoring
- Building composite video layouts (compositor)
- Rendering final clips with audio mixing and subtitle burn-in
- Any `subprocess.run(["ffmpeg", ...])` call

## Availability Check (Pre-Flight)

```python
import shutil

def verify_ffmpeg():
    assert shutil.which("ffmpeg"), "FFmpeg not found in PATH"
    assert shutil.which("ffprobe"), "FFprobe not found in PATH"
```

Run at pipeline startup. Pipeline aborts with CRITICAL if missing.

## FFprobe — Video Metadata Extraction

```bash
ffprobe -v error -show_format -show_streams -of json input.mp4
```

```python
import subprocess, json

def probe_video(file_path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", file_path],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe failed: {result.stderr}")
    return json.loads(result.stdout)
```

**Fields to extract:**

- `format.duration` → float seconds
- `streams[video].width`, `height` → resolution
- `streams[video].r_frame_rate` → fps (e.g. "30/1")
- `streams[audio]` exists → `has_audio = True`
- `format.size` → file size bytes

## Audio Extraction (for Transcription)

```bash
ffmpeg -i input.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 output.wav
```

- `-vn` — no video
- `-acodec pcm_s16le` — 16-bit PCM WAV (required by faster-whisper)
- `-ar 16000` — 16kHz sample rate (Whisper standard)
- `-ac 1` — mono

## Frame Extraction (for Face Detection)

```bash
ffmpeg -i input.mp4 -vf fps=2 -q:v 2 output/frames/frame_%06d.jpg
```

- `-vf fps=2` — 2 frames per second (face detection sampling rate)
- `-q:v 2` — high quality JPEG

Alternative — extract specific time range:

```bash
ffmpeg -ss 5.0 -to 10.0 -i input.mp4 -vf fps=2 -q:v 2 frames/frame_%06d.jpg
```

## Audio Energy Analysis (for Scoring)

```bash
ffmpeg -i input.mp4 -af astats=metadata=1:reset=1 -f null -
```

Parse stderr for per-frame RMS energy. Per-scene energy = average RMS over scene time range. Normalize to [0.0, 1.0] using min-max across all scenes.

## Segment Extraction (Clip Cutting)

```bash
ffmpeg -ss START_SEC -to END_SEC -i input.mp4 -c copy clip.mp4
```

- `-ss` before `-i` for fast seek (input seeking)
- `-c copy` — stream copy, no re-encoding (fast)
- Use for extracting raw clip segments before composition

## Composite Layout (9:16 Vertical)

```bash
ffmpeg -i gameplay.mp4 -i face.mp4 \
  -filter_complex "
    [0]crop=ih*9/16:ih[gameplay];
    [gameplay]scale=1080:1248[top];
    [1]crop=iw:ih*0.5:0:ih*0.25[face_crop];
    [face_crop]scale=1080:672[bottom];
    [top][bottom]vstack=inputs=2[v]
  " \
  -map "[v]" -c:v libx264 -crf 20 composite.mp4
```

**Layout breakdown:**
| Region | Size | Content |
|--------|------|---------|
| Top 65% | 1080 x 1248 | Gameplay (center-cropped to 9:16) |
| Bottom 35% | 1080 x 672 | Face cam (cropped around detected bbox, 1.2× zoom) |
| Total | 1080 x 1920 | Vertical short |

**Fallback (no face detected):**

```bash
ffmpeg -i gameplay.mp4 \
  -filter_complex "crop=ih*9/16:ih,scale=1080:1920,zoompan=z='1.2':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'" \
  -c:v libx264 composite.mp4
```

## Final Render (All Layers Merged)

```bash
ffmpeg -i composite.mp4 -i narration.wav -i gameplay_audio.wav \
  -filter_complex "
    [1]volume=0.3[narr];
    [2]volume=0.7[game];
    [narr][game]amix=inputs=2:duration=first[a];
    subtitles=subtitles.ass[v]
  " \
  -map "[v]" -map "[a]" \
  -c:v libx264 -crf 20 -preset medium \
  -c:a aac -b:a 128k \
  -r 30 -t DURATION \
  output.mp4
```

**Audio mixing:** 70% gameplay + 30% narration via `volume` + `amix` filters.

## Output Specifications

| Property      | Value                              |
| ------------- | ---------------------------------- |
| Video codec   | H.264 High Profile (`libx264`)     |
| CRF           | 20 (config: `renderer.crf`)        |
| Preset        | medium (config: `renderer.preset`) |
| Resolution    | 1080×1920 (9:16 vertical)          |
| Frame rate    | 30 fps                             |
| Audio codec   | AAC 128kbps stereo                 |
| Sample rate   | 44.1 kHz                           |
| Container     | MP4                                |
| Duration      | 30–60 seconds                      |
| Max file size | 100 MB                             |

## Subprocess Execution Pattern

```python
import subprocess
import os
import logging

logger = logging.getLogger(__name__)

def run_ffmpeg(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Run FFmpeg with timeout and error capture."""
    cmd = ["ffmpeg", "-y"] + args  # -y = overwrite output
    logger.debug("FFmpeg command", extra={"cmd": " ".join(cmd)})

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        logger.error("FFmpeg failed", extra={"stderr": result.stderr[:500]})
        raise RuntimeError(f"FFmpeg error (exit {result.returncode})")

    return result
```

**Key parameters:**
| Parameter | Value | Reason |
|-----------|-------|--------|
| `-y` | Always | Overwrite output without prompting |
| `capture_output=True` | Always | Capture stderr for error logging |
| `text=True` | Always | Decode output as string |
| `timeout` | 300s default | Kill runaway processes |

## Atomic File Writes

```python
def run_ffmpeg_atomic(args: list[str], output_path: str, timeout: int = 300):
    """Write to .tmp, validate, then atomic rename."""
    tmp_path = f"{output_path}.tmp"
    # Replace output_path with tmp_path in args
    patched_args = [tmp_path if a == output_path else a for a in args]

    try:
        run_ffmpeg(patched_args, timeout=timeout)
        os.replace(tmp_path, output_path)  # Atomic rename
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
```

## Retry Policy

```
Per-clip rendering:
  max_retries: 1 (2 attempts total)
  On timeout: retry with CRF 28 (lower quality)
  On fatal error: skip clip, mark as failed

Timeout thresholds:
  Frame extraction:  60s
  Segment cutting:   60s
  Composite render: 300s (5 min)
  Final render:     300s (5 min)
```

## Anti-Patterns

```python
# ❌ No -y flag (hangs on overwrite prompt)
subprocess.run(["ffmpeg", "-i", input, output])

# ❌ No timeout (can hang indefinitely)
subprocess.run(["ffmpeg", ...], timeout=None)

# ❌ No error capture (silent failures)
subprocess.run(["ffmpeg", ...])

# ❌ Shell=True (security risk)
subprocess.run(f"ffmpeg -i {input} {output}", shell=True)

# ❌ Writing directly to final output (corruption on crash)
subprocess.run(["ffmpeg", "-i", src, "final_output.mp4"])

# ✅ Always use: -y, capture_output, timeout, atomic writes, list args
```
