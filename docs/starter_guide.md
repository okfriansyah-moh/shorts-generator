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

| Requirement | Minimum                 | Recommended                               |
| ----------- | ----------------------- | ----------------------------------------- |
| OS          | Windows / macOS / Linux | Windows 10+ / macOS 12+ / Ubuntu 22.04+   |
| Python      | 3.10+                   | 3.11+                                     |
| RAM         | 8 GB                    | 16 GB                                     |
| Disk (free) | 5 GB                    | 20 GB                                     |
| CPU         | 4 cores                 | 8 cores                                   |
| GPU         | Not required            | NVIDIA (optional, for NVENC acceleration) |

### External Tools

1. **FFmpeg** (required) — handles all video/audio processing
2. **FFprobe** (required) — comes bundled with FFmpeg

#### Install FFmpeg

**Windows (winget — recommended):**

```powershell
winget install Gyan.FFmpeg
```

After installing, restart your terminal. FFmpeg is added to PATH automatically.

**Windows (Chocolatey):**

```powershell
choco install ffmpeg
```

**Windows (manual):**

1. Download from https://www.gyan.dev/ffmpeg/builds/ (get the "full" release build)
2. Extract the zip to `C:\ffmpeg`
3. Add `C:\ffmpeg\bin` to your system PATH:
   - Search "Environment Variables" in Start menu
   - Edit `Path` under System variables
   - Add `C:\ffmpeg\bin`
4. Restart your terminal

**macOS (Homebrew):**

```bash
brew install ffmpeg
```

If you don't have Homebrew: visit https://brew.sh and run the install command first.

**Ubuntu/Debian:**

```bash
sudo apt update && sudo apt install ffmpeg
```

**Fedora:**

```bash
sudo dnf install ffmpeg
```

**Arch Linux:**

```bash
sudo pacman -S ffmpeg
```

**Verify installation (all platforms):**

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

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

> **Tip:** On every new terminal session, re-run the activate command to enter the virtual environment.
>
> **Windows note:** If you get a "running scripts is disabled" error in PowerShell, run:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` and try again.

### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

If there's no `requirements.txt`, install the core packages manually:

```bash
pip install pyyaml scenedetect faster-whisper mediapipe edge-tts pyttsx3 pillow
```

### 4. Verify Everything Works

```bash
python3 -m pytest tests/ -x -q
```

You should see something like `537 passed`. All tests run without a GPU, without network, and without real video files.

---

## Quick Start — Run Your First Pipeline

### 1. Place your video

Put a video file (MP4, MKV, AVI, MOV, or WebM) somewhere accessible. The video should be **30–120 minutes long** and ideally contain speech.

### 2. Run the pipeline

```bash
# Basic — generates clips with original audio and split layout (face + gameplay)
python3 run_pipeline.py /path/to/your/video.mp4

# Local only — skip scheduling and publishing (recommended for first run)
python3 run_pipeline.py --local-only /path/to/your/video.mp4

# Gameplay-only layout — blurred background, no face cam split
python3 run_pipeline.py --gameplay-only /path/to/your/video.mp4

# With TTS narration mixed into original audio
python3 run_pipeline.py --tts /path/to/your/video.mp4

# Custom output directory
python3 run_pipeline.py --output /path/to/output /path/to/your/video.mp4

# Skip face detection (faster, but defaults to inferred face region)
python3 run_pipeline.py --no-face-detection /path/to/your/video.mp4

# With NVIDIA GPU acceleration (optional — requires NVENC-capable GPU)
python3 run_pipeline.py --gpu /path/to/your/video.mp4

# Combined: local + GPU + gameplay-only
python3 run_pipeline.py --local-only --gpu --gameplay-only /path/to/your/video.mp4

# ── Podcast video type ──────────────────────────────────────────
# Use --video-type podcast for podcast/interview/panel videos.
# Podcast mode uses transcript-aligned speaker detection to identify
# the primary speaker, then generates a stable 9:16 crop plan.
# Falls back to largest-face or center-crop when transcript/faces
# are unavailable. Fully isolated from the gameplay path.

# Podcast mode — speaker-aware crop via transcript alignment
python3 run_pipeline.py --video-type podcast /path/to/podcast.mp4

# Podcast + local only (recommended for first podcast run)
python3 run_pipeline.py --video-type podcast --local-only /path/to/podcast.mp4

# Podcast + GPU acceleration
python3 run_pipeline.py --video-type podcast --gpu /path/to/podcast.mp4
```

**Windows users:** Replace `python3` with `python` in all commands above.

That's it. The pipeline will:

1. Analyze the video (scenes, speech, faces, audio energy)
2. Score each scene on engagement potential
3. Build the best clips (30–60 seconds each)
4. Generate hooks, narration, and subtitles for each clip
5. Composite into vertical 1080×1920 format:
   - **Gameplay**: split face+gameplay or gameplay-only with blurred background
   - **Podcast**: transcript-aligned speaker crop (falls back to face crop or center)
6. Render final MP4s with original audio (or mixed with TTS if `--tts` is used)
7. Create thumbnails and metadata
8. Schedule clips for publishing (one per day) — skipped with `--local-only`

### 3. Find your output

All generated clips are saved in:

```
output/<video_id>/
├── clips/
│   ├── shorts-1/
│   │   ├── final.mp4          # The finished Short
│   │   ├── composite.mp4      # Intermediate composite (silent)
│   │   ├── thumbnail.jpg      # YouTube thumbnail
│   │   ├── metadata.json      # Title, description, tags
│   │   └── subtitles.ass      # Embedded subtitle file
│   ├── shorts-2/
│   │   └── ...
│   └── shorts-N/
│       └── ...
├── thumbnails/                 # All thumbnails in one place
│   ├── shorts-1.jpg
│   └── shorts-2.jpg
├── tts_cache/                  # Cached narration audio
├── pipeline.log               # Detailed run log (JSON)
└── report.json                # Analytics summary
```

Clips are numbered `shorts-1`, `shorts-2`, etc. for easy browsing. The `video_id` is a deterministic hash derived from your video file. Running the same video twice produces the **exact same output** (idempotent).

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

### Compositor Layout

Control the default video layout and face cam position:

```yaml
compositor:
  default_layout: "split" # "split" = face + gameplay (default), "gameplay_only" = blurred bg
  face_region:
    "bottom_left" # Where to crop the face cam from the source video
    # Options: bottom_left, bottom_right, top_left, top_right, center
```

> **Tip**: If your source video has a face cam PiP in a specific corner, set `face_region` to match. The compositor will crop that area for the face panel even if MediaPipe face detection is unavailable.

### Audio Source

```yaml
renderer:
  audio_source:
    "original" # "original" = keep game/mic audio (default)
    # "mixed" = blend original (70%) + TTS narration (30%)
```

You can also toggle this per-run with `--tts` (sets `audio_source` to `"mixed"`).

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

### Face Detection (Optional)

Face detection uses MediaPipe. The **bundled legacy detector** (`mp.solutions.face_detection`) works out of the box — **no model file download required**. Just install `mediapipe` via pip and face detection works.

If you want to use the newer Tasks API instead, you can optionally provide a `.tflite` model file. The pipeline auto-detects which API to use:

1. If a valid `.task`/`.tflite` model file exists → uses Tasks API
2. Otherwise → uses bundled legacy detector (no download needed)

**Optional model file setup (not required):**

```bash
mkdir -p models
# NOTE: Google may remove these URLs at any time.
# The pipeline works without a model file — the bundled legacy detector is used.
curl -fL -o models/blaze_face_short_range.tflite \
  https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite
# Verify it's a binary file, not an HTML error page:
file models/blaze_face_short_range.tflite
```

**Configuration:**

```yaml
face_detection:
  model_path: "models/blaze_face_short_range.tflite" # Optional — used only for Tasks API
  skip: false # Set to true to disable face detection entirely
```

**CLI override:**

```bash
# Skip face detection via CLI flag
python3 run_pipeline.py --no-face-detection /path/to/your/video.mp4

# GPU + no face detection + custom output
python3 run_pipeline.py --gpu --no-face-detection --output /path/to/output /path/to/your/video.mp4
```

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
python3 scripts/publish_cron.py
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

### "Face detection skipped" warning

The pipeline can't find the MediaPipe model file or the `mediapipe` package isn't installed. This is non-fatal — clips still use the split layout by default, using the `compositor.face_region` config to crop the face cam area. To get better face tracking: run `pip install mediapipe` and download the model file (see the Face Detection section above).

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
- **Run tests**: `python3 -m pytest tests/ -v` for verbose test output
- **Contribute**: Each module is in its own folder under `modules/`. Follow the conventions in `.github/copilot-instructions.md`.
