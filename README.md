# Shorts Factory

An autonomous, local-only content production pipeline that transforms long-form gameplay recordings into fully packaged YouTube Shorts — ready for scheduled publishing with zero cloud cost.

## What It Does

**Input:** 1 long-form gameplay video (30–120 minutes)

**Output:** 10–15 YouTube Shorts, each including:

- Vertical video (1080x1920, 30–60s, H.264)
- Composite layout — gameplay (top 65%) + face cam (bottom 35%)
- TTS narration + burned-in subtitles
- Thumbnail (1280x720, face + text overlay)
- Title, description, and tags
- Scheduled publish queue entry

## Architecture

```
Input Video → Ingestion → Scene Split → Transcription → Face Detection
  → Scoring → Clip Building → Hook → TTS → Subtitles → Composition
  → Rendering → Thumbnail → Metadata → Storage → Scheduling → Publishing
```

See [docs/architecture.md](docs/architecture.md) for the full design document.

## Design Principles

| Principle            | Description                                      |
| -------------------- | ------------------------------------------------ |
| Deterministic        | Same input → same output, every time             |
| Idempotent           | Safe to rerun — no duplicates, no corruption     |
| Modular Monolith     | Single process, 16 modules, DTO contracts        |
| Zero Cost            | Local execution only — no paid APIs              |
| Batch Processing     | One command processes the entire video           |
| Minimal Dependencies | FFmpeg, faster-whisper, MediaPipe, Edge TTS, PIL |

## Pipeline Modules

| Module         | Purpose                                                   |
| -------------- | --------------------------------------------------------- |
| Ingestion      | Validate video, compute fingerprint                       |
| Scene Splitter | Detect scene boundaries (3–20s segments)                  |
| Transcription  | Word-level speech-to-text (faster-whisper)                |
| Face Detection | Track face position (MediaPipe, 2fps sampling)            |
| Scoring Engine | Rank scenes by engagement (keywords, audio, face, motion) |
| Clip Builder   | Merge scenes into 30–60s clips                            |
| Hook Generator | Template-based narration scripts                          |
| TTS            | Speech synthesis (Edge TTS)                               |
| Subtitle       | Word-level timed subtitles (ASS format)                   |
| Compositor     | Face + gameplay 9:16 layout                               |
| Renderer       | Final MP4 with all layers merged                          |
| Thumbnail      | Frame selection + text overlay                            |
| Metadata       | Title, description, tags generation                       |
| Storage        | SQLite + filesystem persistence                           |
| Scheduler      | Daily publish date assignment                             |
| Publisher      | YouTube upload via API                                    |

## Usage

```bash
python run_pipeline.py input.mp4
```

Output:

```
output/
├── {video_id}/
│   ├── clips/
│   │   ├── {clip_id}/
│   │   │   ├── final.mp4
│   │   │   ├── thumbnail.jpg
│   │   │   ├── subtitles.ass
│   │   │   ├── narration.wav
│   │   │   └── metadata.json
│   │   └── ...
│   └── pipeline.log
└── shorts.db
```

## Performance

- 1-hour video → ~20–30 min processing (CPU)
- 1-hour video → ~10–15 min processing (GPU)
- Output: 10–15 Shorts per run
- Peak memory: ~4GB

## Tech Stack

- **Python 3.10+**
- **FFmpeg** — video/audio processing
- **PySceneDetect** — scene boundary detection
- **faster-whisper** — speech transcription (CTranslate2)
- **MediaPipe** — face detection and tracking
- **Edge TTS** — text-to-speech synthesis
- **Pillow** — thumbnail generation
- **SQLite** — clip lifecycle and queue management

## Project Structure

```
shorts-generator/
├── docs/
│   └── architecture.md
├── contracts/           # Shared DTO definitions
├── modules/
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
├── output/              # Generated Shorts
├── run_pipeline.py      # Main entry point
└── README.md
```

## Development

Each module is independently developable against shared DTO contracts:

```bash
# Branch per module
git checkout -b feature/scene-splitter
git checkout -b feature/scoring-engine

# Run tests for a single module
pytest tests/test_scene_splitter.py
```

## Non-Goals

- No microservices or distributed systems
- No paid APIs (OpenAI, cloud services)
- No autonomous AI agents
- No real-time processing
- No web UI or mobile app

## License

MIT
