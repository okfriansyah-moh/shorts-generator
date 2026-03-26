# Shorts Factory — Starter Guide

A beginner-friendly guide to setting up, running, and understanding the Shorts Factory pipeline.

---

## What Is Shorts Factory?

Shorts Factory takes a **long-form video** (30–120 minutes, like a gaming stream VOD) and automatically produces **10–15 YouTube Shorts-ready clips** (30–60 seconds each, vertical 1080×1920).

It does everything: scene detection, speech transcription, scoring, clip selection, face-cam compositing, text-to-speech narration, subtitles, thumbnails, metadata, scheduling, and publishing.

**No cloud services, no paid APIs, no GPUs required.** Everything runs locally on your machine. If you have an NVIDIA GPU, you can optionally enable hardware-accelerated encoding and transcription for faster processing.

---

## Requirements

### System

| Requirement | Minimum       | Recommended                               |
| ----------- | ------------- | ----------------------------------------- |
| OS          | macOS / Linux | macOS / Linux                             |
| Python      | 3.10+         | 3.11+                                     |
| RAM         | 8 GB          | 16 GB                                     |
| Disk (free) | 5 GB          | 20 GB                                     |
| CPU         | 4 cores       | 8 cores                                   |
| GPU         | Not required  | NVIDIA (optional, for NVENC acceleration) |

### External Tools

1. **FFmpeg** (required) — handles all video/audio processing
2. **FFprobe** (required) — comes bundled with FFmpeg

#### Install FFmpeg

**macOS (Homebrew):**

```bash
brew install ffmpeg
```

**Ubuntu/Debian:**

```bash
sudo apt update && sudo apt install ffmpeg
```

**Verify installation:**

```bash
ffmpeg -version
ffprobe -version
```

Both commands should print version info without errors.

---

## Setup

### 1. Clone the Repository

```bash
git clone <your-repo-url> shorts-generator
cd shorts-generator
```

### 2. Create a Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

> On every new terminal session, run `source .venv/bin/activate` to activate the environment.

### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

If there's no `requirements.txt`, install the core packages manually:

```bash
pip install pyyaml pyscenedetect faster-whisper mediapipe edge-tts pyttsx3 pillow
```

### 4. Verify Everything Works

```bash
python -m pytest tests/ -x -q
```

You should see something like `535 passed`. All tests run without a GPU, without network, and without real video files.

---

## Quick Start — Run Your First Pipeline

### 1. Place your video

Put a video file (MP4, MKV, AVI, MOV, or WebM) somewhere accessible. The video should be **30–120 minutes long** and ideally contain speech.

### 2. Run the pipeline

```bash
# Default CPU mode
python run_pipeline.py /path/to/your/video.mp4

# With NVIDIA GPU acceleration (optional — requires NVENC-capable GPU)
python run_pipeline.py --gpu /path/to/your/video.mp4
```

That's it. The pipeline will:

1. Analyze the video (scenes, speech, faces, audio energy)
2. Score each scene on engagement potential
3. Build the best clips (30–60 seconds each)
4. Generate hooks, narration, and subtitles for each clip
5. Composite into vertical 1080×1920 format
6. Render final MP4s with mixed audio
7. Create thumbnails and metadata
8. Schedule clips for publishing (one per day)

### 3. Find your output

All generated clips are saved in:

```
output/<video_id>/
├── clips/
│   ├── <clip_id_1>/
│   │   ├── final.mp4          # The finished Short
│   │   ├── thumbnail.jpg      # YouTube thumbnail
│   │   ├── metadata.json      # Title, description, tags
│   │   └── subtitles.ass      # Embedded subtitle file
│   ├── <clip_id_2>/
│   │   └── ...
│   └── ...
├── thumbnails/                 # All thumbnails in one place
├── tts_cache/                  # Cached narration audio
├── pipeline.log               # Detailed run log (JSON)
└── report.json                # Analytics summary
```

The `video_id` is a deterministic hash derived from your video file. Running the same video twice produces the **exact same output** (idempotent).

---

## Configuration

All settings live in `config/config.yaml`. You almost never need to change these, but here are the most useful ones:

### Output Location

```yaml
paths:
  output_dir: "output" # Where clips are saved
  temp_dir: "output/temp" # Temporary processing files
  database: "output/shorts_factory.db" # Pipeline state database
```

### Clip Duration

```yaml
pipeline:
  min_clip_duration: 30 # Minimum clip length (seconds)
  max_clip_duration: 60 # Maximum clip length (seconds)
  max_clips_per_run: 20 # Max clips to generate per video
```

### Scoring Weights

These control what makes a "good" clip. Higher weight = more importance.

```yaml
scoring:
  weights:
    keyword: 3 # Speech contains engaging keywords
    audio_energy: 2 # Loud/energetic moments
    face_presence: 2 # Face visible on camera
    scene_activity: 1 # Visual motion/action
    sentence_density: 1 # Natural speech pace
```

### TTS Voice

```yaml
tts:
  voice: "en-US-AriaNeural" # Microsoft Edge TTS voice
  rate: "+0%" # Speech rate adjustment
```

### Scheduler

```yaml
scheduler:
  posts_per_day: 1 # One clip published per day
  publish_time_utc: "10:00" # Publish at 10:00 AM UTC
```

### GPU Acceleration (Optional)

If you have an NVIDIA GPU with NVENC support, you can enable hardware-accelerated encoding and CUDA-based transcription:

```yaml
gpu:
  enabled: true # Enable GPU mode (default: false)
  encoder: "h264_nvenc" # NVIDIA hardware encoder
  preset: "p4" # p1 (fastest) to p7 (best quality)
  cq: 20 # Constant quality (lower = better)
  transcription_device: "cuda"
  transcription_compute_type: "float16"
```

You can also enable it per-run via CLI (`--gpu`) or environment variable (`SF_GPU_ENABLED=true`) without modifying config.yaml.

**Requirements for GPU mode:**

- NVIDIA GPU with NVENC support (GTX 1060+ / RTX series)
- NVIDIA drivers installed (`nvidia-smi` must work)
- FFmpeg compiled with `--enable-nvenc`
- For CUDA transcription: PyTorch with CUDA (`pip install torch --index-url https://download.pytorch.org/whl/cu121`)

> **Note:** If CUDA is unavailable for transcription, it automatically falls back to CPU. NVENC encoding will hard-fail at startup if the GPU or drivers are missing.

---

## Publishing to YouTube

Publishing is a **separate step** from the main pipeline. The pipeline generates and schedules clips; publishing uploads them.

### Setup YouTube OAuth2

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the **YouTube Data API v3**
3. Create OAuth 2.0 credentials (Desktop App type)
4. Download the `client_secrets.json` file
5. Place it in the project root

### Run the Publisher

```bash
python scripts/publish_cron.py
```

This will:

- Find all clips with status `scheduled` and `scheduled_at <= now`
- Upload each one to YouTube as **unlisted**
- Transition to **public** after 30 minutes (configurable)
- Update the database with publish status

**For automatic publishing**, set up a cron job:

```bash
# Run every hour
0 * * * * cd /path/to/shorts-generator && .venv/bin/python scripts/publish_cron.py
```

---

## Understanding the Pipeline

The pipeline has **16 stages** that run in strict order:

```
Stage 0:  ingestion        → Validate video, compute fingerprint
Stage 1:  scene_splitter   → Split into 3-20 second scenes
Stage 2:  transcription    → Speech-to-text with word timestamps
Stage 3:  face_detection   → Detect face positions at 2fps
Stage 4:  scoring          → Score each scene (5 factors)
Stage 5:  clip_builder     → Merge best scenes into 30-60s clips
Stage 6:  hook_generator   → Generate hook text for each clip
Stage 7:  tts              → Text-to-speech narration
Stage 8:  subtitle         → Generate .ass subtitle files
Stage 9:  compositor       → Compose vertical 9:16 layout
Stage 10: renderer         → Final MP4 with mixed audio
Stage 11: thumbnail        → 1280×720 JPEG with text overlay
Stage 12: metadata         → Title, description, tags
Stage 13: storage          → Verify & organize all artifacts
Stage 14: scheduler        → Assign publish dates (1/day)
Stage 15: publisher        → Upload to YouTube (via cron)
```

**Stages 0–5** run once per video. **Stages 6–13** run once per clip. **Stages 14–15** run once per batch.

### Key Design Principles

- **Deterministic**: Same video + same config = identical output. Always.
- **Idempotent**: Running twice on the same video produces no duplicates.
- **Resumable**: If the pipeline crashes, rerun it — it picks up from the last checkpoint.
- **No cloud**: Everything runs locally. No API keys needed (except for YouTube publishing).
- **GPU optional**: Works on CPU by default; add `--gpu` for NVIDIA acceleration.

---

## Troubleshooting

### "FFmpeg not found"

FFmpeg isn't installed or not in your PATH. Run `which ffmpeg` to check. Install it with your package manager (see Requirements above).

### "Video file not found"

Double-check the path you passed to `run_pipeline.py`. It must be an absolute or valid relative path to an actual file.

### "Video duration out of range"

The video must be 30–120 minutes long. This is configurable in `config.yaml`:

```yaml
ingestion:
  min_duration_seconds: 1800 # 30 minutes
  max_duration_seconds: 7200 # 120 minutes
```

### "No valid clips produced"

The scoring engine couldn't find enough engaging content. Try:

- Lowering `scoring.min_composite_score` (default: 0.2)
- Lowering `clip_builder.target_duration_min` (default: 30)
- Using a video with more speech, action, or face-cam

### Pipeline fails partway through

Just rerun the same command. The pipeline checkpoints after each stage and resumes from where it left off. No data is lost.

### "nvidia-smi not found" or "FFmpeg does not support h264_nvenc"

GPU mode (`--gpu`) requires NVIDIA drivers and an NVENC-capable FFmpeg build. If you don't have a GPU, simply omit the `--gpu` flag — the pipeline works fine on CPU.

### Tests fail

Make sure you're in the virtual environment (`source .venv/bin/activate`) and all dependencies are installed. Tests don't need FFmpeg, a GPU, or network access.

---

## Project Structure (Simplified)

```
shorts-generator/
├── run_pipeline.py      # Run this to process a video
├── scripts/
│   └── publish_cron.py  # Run this to publish scheduled clips
├── config/
│   └── config.yaml      # All settings live here
├── contracts/           # Data structures (DTOs) shared between modules
├── modules/             # The 16 pipeline stages (one folder each)
├── core/                # Orchestrator, config loader, logging, GPU resolver
├── database/            # SQLite database layer
├── output/              # Generated clips go here
├── tests/               # Unit and integration tests
└── docs/                # Architecture docs (you are here)
```

---

## Next Steps

- **Customize config**: Adjust scoring weights, clip duration, TTS voice in `config/config.yaml`
- **Read the architecture**: See `docs/architecture.md` for the full system design
- **Check progress**: See `docs/progress_report.md` for implementation status
- **Run tests**: `python -m pytest tests/ -v` for verbose test output
- **Contribute**: Each module is in its own folder under `modules/`. Follow the conventions in `.github/copilot-instructions.md`.
