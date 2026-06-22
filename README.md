# Shorts Factory

An autonomous, local-only content production pipeline that transforms long-form gameplay, podcast, and sports recordings into fully packaged short-form videos — published to YouTube, TikTok, Instagram Reels, and Facebook Reels on a schedule, with zero cloud cost.

## What It Does

**Input:** 1 long-form video (5–120 minutes) — gameplay, podcast, or sports broadcast

**Output:** 10–15 short clips per run, each including:

- Vertical video (1080×1920, 30–60s, H.264)
- Composite layout — gameplay (top 65%) + face cam (bottom 35%), or smart speaker-crop for podcasts
- TTS narration + burned-in subtitles (ASS format, word-level karaoke)
- Thumbnail (1280×720, face + text overlay)
- Title, description, and tags
- Scheduled multi-platform publish across YouTube, TikTok, Instagram Reels, and Facebook Reels

---

## Quick Start

```bash
# Single video — gameplay (default)
python run_pipeline.py input.mp4

# Single video — podcast mode
python run_pipeline.py --video-type podcast input.mp4

# Sports — tennis (center crop, default)
python run_pipeline.py --video-type sports_tennis match.mp4

# Sports — football (action crop with hybrid tracking)
python run_pipeline.py --video-type sports_football match.mp4

# Sports — padel with explicit layout override
python run_pipeline.py --video-type sports_padel --sports-layout sports_letterbox match.mp4

# GPU-accelerated (NVIDIA)
python run_pipeline.py --gpu input.mp4

# Upload scheduled clips (runs via cron)
python scripts/upload_scheduler.py

# Auto-pick next raw video and run the pipeline (account required)
python scripts/generation_scheduler.py --account mrkimbum12
```

**Output layout:**

```
output/
├── shorts_factory.db                        # global SQLite DB
└── {account}/                               # e.g. mrkimbum12/
    └── {video_id}_{video_name}/             # e.g. c9e10a40da590d0d_ultra_instinct-dbz/
        ├── clips/
        │   └── shorts-{n}/                  # e.g. shorts-1/, shorts-2/
        │       ├── clip.mp4
        │       └── thumbnail.jpg
        ├── pending_ai_metadata.json          # exported by generation_scheduler
        ├── ai_metadata_results_new.json      # written by Claude, applied to DB
        ├── enriched_batch.json               # written by Claude during 8am enrichment
        └── pipeline.log
```

---

## Architecture

Modular monolith — single process, single SQLite database, 16 pipeline stages in strict sequential order:

```
ingestion → scene_splitter → transcription → face_detection → audio_analysis → scoring →
clip_builder → hook_generator → tts → subtitle → compositor → renderer →
thumbnail → metadata → storage → scheduler → publisher
```

Every stage communicates via frozen dataclass DTOs defined in `contracts/`. The orchestrator is the only component that calls modules, manages execution order, performs checkpointing, and writes to the database.

### Multi-Platform Fan-Out

A single clip upload fans out to all enabled platforms concurrently. One platform failing never blocks another.

```
upload_scheduler.py
    └─ publish_to_all_platforms(record, config)
            ├─ YouTubeClient   (thread 1) → UploadResult
            ├─ TikTokClient    (thread 2) → TikTokUploadResult
            └─ MetaClient      (thread 3) → MetaUploadResult
                    ├─ Instagram Reels
                    └─ Facebook Reels
```

### Multi-Account Architecture

Each account lives under `config/accounts/<name>/` with its own credentials and per-account config overrides deep-merged on top of global defaults at runtime. No code changes needed to add a new account.

```
config/
├── config.yaml                 # global defaults
└── accounts/
    └── mrkimbum12/             # one folder per account
        ├── account.yaml        # per-account overrides (deep-merged)
        └── youtube_credentials.json

output/
├── shorts_factory.db           # global SQLite DB (single source of truth)
└── mrkimbum12/                 # account-scoped output
    └── {video_id}_{video_name}/
        ├── clips/
        │   └── shorts-{n}/
        │       ├── clip.mp4
        │       └── thumbnail.jpg
        ├── pending_ai_metadata.json
        ├── ai_metadata_results_new.json
        └── pipeline.log

raw/
└── mrkimbum12/                 # drop source videos here
    ├── myvideo.mp4
    └── .processed              # ledger of already-processed filenames
```

---

## Design Principles

| Principle          | Description                                                       |
| ------------------ | ----------------------------------------------------------------- |
| Deterministic      | Same input + same config = identical output, every time           |
| Idempotent         | Safe to rerun — content-addressable IDs, `ON CONFLICT DO NOTHING` |
| Modular Monolith   | Single process, 16 isolated modules, frozen DTO contracts         |
| Zero Cost          | Local execution only — no paid APIs, no cloud                     |
| Database Authority | SQLite is the single source of truth for all pipeline state       |
| Orchestrator-Only  | Only the orchestrator calls modules and writes to the database    |
| Checkpoint/Resume  | Pipeline resumes from the last successful stage on restart        |

---

## Pipeline Modules

| #   | Module           | Purpose                                                  |
| --- | ---------------- | -------------------------------------------------------- |
| 1   | Ingestion        | Validate video, compute SHA256 fingerprint               |
| 2   | Scene Splitter   | Detect scene boundaries (PySceneDetect, 3–20s segments)  |
| 3   | Transcription    | Word-level speech-to-text (faster-whisper, CTranslate2)  |
| 4   | Face Detection   | Track face position (MediaPipe, 2fps sampling, optional) |
| 5   | Audio Analysis   | Per-scene RMS energy extraction (FFmpeg)                 |
| 6   | Scoring          | Rule-based scene ranking (keywords, audio, face, motion) |
| 7   | Clip Builder     | Merge top-scored scenes into 30–60s clips                |
| 8   | Hook Generator   | Template-based narration scripts                         |
| 9   | TTS              | Speech synthesis (Edge TTS, cached by text hash)         |
| 10  | Subtitle         | Word-level timed subtitles (ASS format, karaoke)         |
| 11  | Compositor       | Face + gameplay 9:16 vertical layout; speaker-crop for podcasts; letterbox/center/action-crop for sports |
| 12  | Renderer         | Final MP4 with all layers merged (FFmpeg)                |
| 13  | Thumbnail        | Frame selection + text overlay (Pillow)                  |
| 14  | Metadata         | Title, description, tags generation                      |
| 15  | Storage          | SQLite + filesystem persistence                          |
| 16  | Scheduler        | Daily publish date assignment                            |
| 17  | Publisher        | Multi-platform upload (YouTube, TikTok, Instagram, Facebook) |
| 18  | Analytics        | Pipeline report, quality metrics, publish status         |

---

## Multi-Platform Publishing

Platforms are enabled per-account in `account.yaml`. Disabled platforms are skipped entirely — no auth attempt, no thread spawned.

```yaml
platforms:
  youtube:
    enabled: true
    credentials: "youtube_credentials.json"
    initial_visibility: "unlisted"
    public_delay_minutes: 30

  tiktok:
    enabled: false
    credentials: "tiktok_credentials.json"
    privacy_level: "PUBLIC_TO_EVERYONE"

  instagram:
    enabled: false                    # requires public IP + port forward
    credentials: "meta_credentials.json"
    serve_port: 8080

  facebook:
    enabled: false
    credentials: "meta_credentials.json"
```

**Credentials setup:**

| Platform          | File                        | Required fields                                              |
| ----------------- | --------------------------- | ------------------------------------------------------------ |
| YouTube           | `youtube_credentials.json`  | `client_id`, `client_secret`, `refresh_token`, `token_uri`  |
| TikTok            | `tiktok_credentials.json`   | `client_key`, `client_secret`, `refresh_token`               |
| Instagram/Facebook| `meta_credentials.json`     | `access_token`, `instagram_user_id`, `facebook_page_id`      |

All credential files live under `config/accounts/<name>/` and are gitignored.

**Failure isolation:**

| Scenario              | Behaviour                                              |
| --------------------- | ------------------------------------------------------ |
| One platform fails    | Error stored in results, others unaffected             |
| All platforms fail    | Clip status → `failed`, error logged                   |
| At least one succeeds | Clip status → `published`, all platform IDs persisted  |
| Telegram notification | Sent on any success — shows all platform IDs           |

---

## Multi-Account Support

New account setup requires zero code changes:

```bash
mkdir config/accounts/<new-account>
# Add account.yaml with only what differs from global defaults
# Add platform credential files
```

Per-account overrideable sections (deep-merged at runtime, override wins at every leaf):

| Section        | Per-account use case                            |
| -------------- | ----------------------------------------------- |
| `video_type`   | gameplay vs podcast vs sports_* per channel     |
| `metadata`     | language, title/description length constraints  |
| `scheduler`    | posts_per_day, publish_time_utc per channel     |
| `channel`      | name, hashtags, static_tags                     |
| `telegram`     | chat_id per channel (shared bot token globally) |
| `compositor`   | layout, face_region                             |
| `thumbnail`    | saturation/contrast/font per channel brand      |
| `tts`          | voice per channel                               |
| `scoring`      | weights tuned per content type                  |
| `clip_builder` | clip count/duration per channel strategy        |

---

## Video Type Support

### Gameplay (default)

Single fixed POV. Gameplay fills 65% of the frame; face cam occupies the bottom 35%.

```bash
python run_pipeline.py input.mp4
python run_pipeline.py --video-type gameplay input.mp4
```

### Podcast

Talking-head or panel recordings. Uses transcript-aligned speaker detection to identify the primary speaker, then smart-crops the wide shot to 9:16 — no manual camera adjustment needed.

```bash
python run_pipeline.py --video-type podcast input.mp4
```

**Speaker detection algorithm:**

```
1. Build 1-second time buckets over clip duration
2. Compute text activity per bucket (character count, proportional overlap)
3. Cluster face bboxes by centre position (threshold=0.20, greedy, deterministic L→R IDs)
4. Score clusters: frames × face_weight + frames × norm_text × text_weight
5. Primary speaker = cluster with highest total score (lower face_id tiebreak)
6. Median bbox of primary speaker bboxes → expand to 9:16 at 1.4× width → clamp to source bounds
```

| Situation              | Layout           | Description                                     |
| ---------------------- | ---------------- | ----------------------------------------------- |
| Transcript + faces     | `speaker_crop`   | Primary speaker detected via text-face alignment |
| No transcript, faces OK| `center_face_crop`| Largest-area face cluster used                  |
| No faces detected      | `center_crop`    | Simple 9:16 center crop                         |

### Sports (tennis / football / padel)

Broadcast and match recordings. Supports three compositor layouts selectable per clip via `--sports-layout`. The `sports_action_crop` layout runs a hybrid tracking strategy to anchor the crop on the action.

```bash
python run_pipeline.py --video-type sports_tennis match.mp4
python run_pipeline.py --video-type sports_football match.mp4
python run_pipeline.py --video-type sports_padel match.mp4

# Override the default layout for any sport
python run_pipeline.py --video-type sports_tennis --sports-layout sports_action_crop match.mp4
```

**Compositor layouts:**

| Layout | Description | Default for |
| ------------------- | -------------------------------------------- | ----------- |
| `sports_center_crop` | Center column crop to 9:16 | tennis, padel |
| `sports_letterbox` | Fit full frame, pad with black bars | — (manual) |
| `sports_action_crop` | Crop anchored on detected action point | football |

**Hybrid action-tracking strategy (for `sports_action_crop`):**

```
1. Face centroid    — weighted average of face bbox centers (face_visible_ratio ≥ 0.2)
2. MediaPipe Pose   — 1fps keyframes, average body landmark centroid
3. Motion energy    — 2fps 64×36 thumbnails, column-sum of pixel difference
4. Center fallback  — (0.5, 0.5), always succeeds
```

**Per-sport config tuning:**

| Sport | Min scene | `scene_activity` weight | `audio_energy` weight |
| -------- | --------- | ----------------------- | --------------------- |
| Tennis | 4.0s | 3 | 3 |
| Football | 2.0s | 5 | 5 |
| Padel | 3.0s | 4 | 3 |

**Adding a new sport — 4 file changes, zero architectural changes:**

1. `modules/compositor/sports_<name>.py` — thin wrapper, set `_SPORT` and `_DEFAULT_LAYOUT`
2. `run_pipeline.py` — add to `--video-type` choices + `_OVERLAY_REGISTRY` (2-layer entry)
3. `modules/compositor/compose.py` — add dispatch branch
4. `config/config.yaml` — add `sports_<name>_*` config sections

---

## GPU Acceleration

Optional NVIDIA GPU mode — CPU-only by default, no configuration required.

```bash
# CLI flag
python run_pipeline.py --gpu input.mp4

# Or in config
gpu:
  enabled: true
  encoder: h264_nvenc
```

When GPU mode is active:
- FFmpeg uses `h264_nvenc` for compositor and renderer encoding
- faster-whisper uses `device=cuda, compute_type=float16` with automatic CPU fallback
- Startup validates `nvidia-smi` and FFmpeg NVENC encoder availability

---

## Scheduling & Cron

Two-tier operational split — heavy CPU generation runs via Claude Cowork at night; lightweight platform uploads run via local crontab three times per day.

| Responsibility | Tool           | Why                                                          |
| -------------- | -------------- | ------------------------------------------------------------ |
| **Generation** | Claude Cowork  | CPU-heavy (transcription, rendering, AI metadata enrichment) |
| **Upload**     | Local crontab  | Lightweight API calls only — no Claude needed                |

---

### Generation — Claude Cowork (nightly, per account)

Two Cowork tasks per account: one for **video generation** (runs the pipeline + AI metadata), one for **AI enrichment** (polishes metadata + regenerates thumbnails + checks queue depth). Tasks are staggered 10 minutes apart per account.

**Current setup:**

| Task name                  | Account     | Schedule (WIB) | Schedule (UTC) | Purpose                              |
| -------------------------- | ----------- | -------------- | -------------- | ------------------------------------ |
| `shorts-generator-8pm`     | mrkimbum12  | 20:00          | 13:00          | Pipeline + AI viral metadata         |
| `shorts-generator-8am`     | mrkimbum12  | 08:00          | 01:00          | AI enrichment + thumbnails + queue   |

**Adding a new account** — create two new Cowork tasks staggered 10 minutes after the existing ones:

| Task name                  | Account          | Schedule (WIB) | Schedule (UTC) |
| -------------------------- | ---------------- | -------------- | -------------- |
| `shorts-generator-8pm-2`   | newaccount       | 20:10          | 13:10          |
| `shorts-generator-8am-2`   | newaccount       | 08:10          | 01:10          |

Each generation task loops `generation_scheduler.py` until all clips for the current video are fully rendered before exiting:

```bash
# Loop until all clips rendered (run inside the Cowork task prompt)
python scripts/generation_scheduler.py --account mrkimbum12
# Repeat until unrendered clip count = 0
```

Each enrichment task runs:

```bash
python scripts/ai_enricher.py --account mrkimbum12 --export   # export clips needing enrichment
# Claude rewrites titles/descriptions/tags, then:
python scripts/ai_enricher.py --account mrkimbum12 --apply output/mrkimbum12/{video_folder}/enriched_batch.json
python scripts/thumbnail_overlay.py --all
python scripts/ai_enricher.py --account mrkimbum12 --status   # check queue depth
```

---

### Upload — Local Crontab (3 waves/day, per account)

Three upload waves per day. Each wave stagers accounts 5 minutes apart so API rate limits are never hit simultaneously. The upload scheduler checks for clips with `scheduled_at <= now` and publishes them to all enabled platforms.

| Wave   | Time (WIB) | Time (UTC) |
| ------ | ---------- | ---------- |
| Wave 1 | 09:00      | 02:00      |
| Wave 2 | 14:00      | 07:00      |
| Wave 3 | 19:00      | 12:00      |

**Current crontab (mrkimbum12, all times UTC):**

```cron
SF=/Users/mekari/Developer/personal-project/shorts-generator
PY=/opt/homebrew/bin/python3
LOG=$SF/output/upload_cron.log

# ── Wave 1: 09:00 WIB (02:00 UTC) ──────────────────────────────────────────
 0  2 * * *  cd $SF && $PY scripts/upload_scheduler.py --account mrkimbum12  >> $LOG 2>&1

# ── Wave 2: 14:00 WIB (07:00 UTC) ──────────────────────────────────────────
 0  7 * * *  cd $SF && $PY scripts/upload_scheduler.py --account mrkimbum12  >> $LOG 2>&1

# ── Wave 3: 19:00 WIB (12:00 UTC) ──────────────────────────────────────────
 0 12 * * *  cd $SF && $PY scripts/upload_scheduler.py --account mrkimbum12  >> $LOG 2>&1
```

**Adding a second account** — append staggered lines (5 min apart per wave):

```cron
# ── Wave 1 ──────────────────────────────────────────────────────────────────
 5  2 * * *  cd $SF && $PY scripts/upload_scheduler.py --account newaccount  >> $LOG 2>&1

# ── Wave 2 ──────────────────────────────────────────────────────────────────
 5  7 * * *  cd $SF && $PY scripts/upload_scheduler.py --account newaccount  >> $LOG 2>&1

# ── Wave 3 ──────────────────────────────────────────────────────────────────
 5 12 * * *  cd $SF && $PY scripts/upload_scheduler.py --account newaccount  >> $LOG 2>&1
```

To apply:

```bash
crontab -e
# paste the block above (replace SF and PY paths)
```

Each `upload_scheduler.py --account <name>` run:
1. Queries the DB for `status = 'scheduled' AND scheduled_at <= now AND account_name = <name>`
2. Fans out to all enabled platforms concurrently (YouTube, TikTok, Instagram, Facebook)
3. Sends a Telegram notification on success with all platform IDs
4. Marks the clip `published` if at least one platform succeeds; `failed` if all fail

---

## Performance Targets

For a 1-hour input video on consumer hardware (8-core CPU, 16GB RAM, no GPU):

| Metric         | Target               |
| -------------- | -------------------- |
| Total pipeline | 20–30 min            |
| Peak memory    | ~4GB                 |
| Disk per batch | ~2GB (12 clips)      |
| Ingestion      | < 10 seconds         |
| Scoring        | < 5 seconds          |
| Output         | 10–15 Shorts per run |

---

## Tech Stack

- **Python 3.10+** — type hints on all public interfaces
- **FFmpeg** — all video/audio processing (subprocess, no Python video libraries)
- **PySceneDetect** — scene boundary detection
- **faster-whisper** — speech transcription (CTranslate2)
- **MediaPipe** — face detection and tracking
- **Edge TTS** — text-to-speech synthesis
- **Pillow** — thumbnail generation
- **SQLite** — pipeline state, clip lifecycle, and queue management
- **YouTube Data API v3** — OAuth2 upload with unlisted → public visibility transition
- **TikTok Content Posting API** — OAuth2 token refresh, file-based upload, async status polling
- **Meta Graph API** — Instagram Reels + Facebook Reels via temp local HTTP server

---

## Project Structure

```
shorts-generator/
├── run_pipeline.py                  # Single entry point
├── contracts/                       # Frozen dataclass DTO definitions
│   ├── ingestion.py
│   ├── scene.py
│   ├── transcript.py
│   ├── face.py
│   ├── audio.py
│   ├── scoring.py
│   ├── clip.py
│   ├── compositor.py
│   ├── hook.py
│   ├── tts.py
│   ├── subtitle.py
│   ├── render.py
│   ├── thumbnail.py
│   ├── metadata.py
│   ├── storage.py
│   ├── strategies.py
│   ├── analytics.py
│   └── errors.py
├── modules/
│   ├── ingestion/
│   ├── scene_splitter/
│   ├── transcription/
│   ├── face_detection/
│   ├── audio_analysis/
│   ├── scoring/
│   ├── clip_builder/
│   ├── hook_generator/
│   ├── tts/
│   ├── subtitle/
│   ├── compositor/
│   │   ├── compose.py             # Dispatcher — gameplay, podcast, or sports
│   │   ├── gameplay_crop.py
│   │   ├── face_crop.py
│   │   ├── fallback.py
│   │   ├── podcast.py
│   │   ├── sports_utils.py        # Universal crop filters + shared FFmpeg executor
│   │   ├── sports_tennis.py       # Tennis wrapper (center_crop default)
│   │   ├── sports_football.py     # Football wrapper (action_crop default)
│   │   └── sports_padel.py        # Padel wrapper (center_crop default)
│   ├── renderer/
│   ├── thumbnail/
│   ├── metadata/
│   ├── storage/
│   ├── scheduler/
│   ├── publisher/
│   │   ├── multi_platform.py      # Fan-out orchestrator
│   │   ├── youtube_client.py
│   │   ├── tiktok_client.py
│   │   ├── meta_client.py         # Instagram + Facebook Reels
│   │   └── visibility.py
│   ├── strategies/
│   │   ├── podcast_strategy.py    # Transcript-aligned speaker detection
│   │   └── sports_strategy.py     # Hybrid action-tracking cascade → SportsFramePlan
│   ├── notifier/
│   │   └── telegram.py            # Upload + error notifications
│   └── analytics/
├── core/
│   ├── config.py                  # YAML config loader + env overrides
│   ├── logging.py                 # Structured JSON logging
│   ├── dependencies.py            # FFmpeg/FFprobe/Python checks
│   ├── gpu.py                     # GPU configuration resolver
│   ├── account_loader.py          # Per-account config deep-merge
│   └── orchestrator.py            # Pipeline orchestrator
├── database/
│   ├── adapter.py
│   ├── connection.py
│   └── migrations/
├── config/
│   ├── config.yaml                # Global defaults
│   └── accounts/
│       └── <account-name>/
│           ├── account.yaml       # Per-account overrides
│           └── *_credentials.json # Platform credentials (gitignored)
├── scripts/
│   ├── upload_scheduler.py        # Cron-driven upload runner
│   ├── generation_scheduler.py    # Picks next raw video and runs pipeline
│   ├── scheduled_run.py           # Wrapper for cron-based pipeline runs
│   ├── ai_enricher.py             # AI metadata enrichment
│   ├── apply_ai_metadata.py       # Applies AI-generated metadata to DB
│   ├── thumbnail_overlay.py       # Standalone thumbnail re-generation
│   ├── rebuild_db.py              # DB state rebuild from filesystem
│   └── run_parallel.sh            # Parallel development orchestrator
├── tests/
│   ├── unit/
│   └── integration/
├── output/                        # Generated clips (gitignored)
└── docs/
    ├── architecture.md
    ├── implementation_roadmap.md
    ├── orchestrator_spec.md
    ├── dto_contracts.md
    ├── db_adapter_spec.md
    ├── progress_report.md
    ├── PARALLEL_DEV.md
    └── AGENTS_AND_SKILLS.md
```

---

## Key Invariants

- **Determinism**: same input + same config = identical output (no `random`, no LLMs for decisions)
- **Idempotency**: content-addressable IDs (`video_id = SHA256(first_10MB + file_size)[:16]`), all SQL uses `ON CONFLICT DO NOTHING`
- **Module isolation**: modules communicate only through frozen DTOs in `contracts/`, no cross-module imports
- **Orchestrator authority**: only the orchestrator calls modules, writes to the database, and handles checkpointing
- **Database adapter**: all database access through `database/adapter.py` — modules never touch the database directly
- **Platform isolation**: one platform failing never blocks another; clip is published if at least one platform succeeds

---

## Documentation

| Document                                                    | Purpose                                          |
| ----------------------------------------------------------- | ------------------------------------------------ |
| [architecture.md](docs/architecture.md)                     | 18-section system architecture                   |
| [implementation_roadmap.md](docs/implementation_roadmap.md) | 11-phase roadmap (Phase 0–10) with exit criteria |
| [orchestrator_spec.md](docs/orchestrator_spec.md)           | Orchestrator execution model + checkpointing     |
| [dto_contracts.md](docs/dto_contracts.md)                   | DTO definitions with validation rules            |
| [db_adapter_spec.md](docs/db_adapter_spec.md)               | Database abstraction layer + migration strategy  |
| [progress_report.md](docs/progress_report.md)               | Full implementation history and change log       |
| [PARALLEL_DEV.md](docs/PARALLEL_DEV.md)                     | 3-mode parallel development guide                |
| [AGENTS_AND_SKILLS.md](docs/AGENTS_AND_SKILLS.md)           | 9 agents, 26 skills, composition matrices        |

---

## Non-Goals

- No microservices or distributed systems
- No paid APIs (OpenAI, Anthropic, cloud services)
- No real-time or streaming processing
- No web UI, dashboard, or mobile interface
- No cloud deployment or scaling

---

## License

MIT
