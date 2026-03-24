---
name: faster-whisper
description: "faster-whisper (CTranslate2) patterns for Shorts Factory. Use when implementing the transcription module. Covers model loading, transcription parameters, word-level timestamps, confidence handling, empty transcript fallback, and DTO conversion."
---

# faster-whisper Transcription Skill

## When to Use

- Implementing the transcription module
- Configuring model size / compute type
- Handling word-level timestamps for subtitle alignment
- Dealing with empty or low-confidence transcripts

## Library

- **Package:** `faster-whisper` (PyPI)
- **Backend:** CTranslate2 (4× faster than OpenAI Whisper)
- **Models:** tiny / base / small / medium / large-v2
- **Default:** `base` (from `config.transcription.model_size`)
- **Cache:** `~/.cache/huggingface/` (auto-downloaded on first use)

## Model Loading

```python
from faster_whisper import WhisperModel

def load_model(config: dict) -> WhisperModel:
    model_size = config["transcription"]["model_size"]  # "base"
    return WhisperModel(
        model_size,
        device="cpu",          # CPU-first (GPU optional)
        compute_type="int8",   # Quantized for CPU speed
    )
```

| Model    | Size | Speed (1hr audio, CPU) | Accuracy |
| -------- | ---- | ---------------------- | -------- |
| tiny     | 39M  | ~2 min                 | Low      |
| base     | 74M  | ~5 min                 | Medium   |
| small    | 244M | ~10 min                | Good     |
| medium   | 769M | ~20 min                | High     |
| large-v2 | 1.5G | ~40 min                | Highest  |

**Recommendation:** `base` for development, `small` for production.

## Transcription

```python
def transcribe(model: WhisperModel, audio_path: str, config: dict):
    segments, info = model.transcribe(
        audio_path,
        language=config["transcription"]["language"],  # "en"
        beam_size=config["transcription"]["beam_size"],  # 5
        word_timestamps=True,  # REQUIRED for subtitle alignment
        vad_filter=True,       # Skip silence regions (faster)
    )
    # segments is a generator — must iterate to get results
    return list(segments), info
```

**Critical:** `word_timestamps=True` is mandatory. Without it, subtitle alignment is impossible.

## Word-Level Timestamp Extraction

```python
from contracts.transcript import Word, TranscriptSegment, Transcript

def build_transcript(segments, info, video_id: str) -> Transcript:
    transcript_segments = []

    for seg in segments:
        words = []
        if seg.words:
            words = tuple(
                Word(
                    text=w.word.strip(),
                    start_time=int(w.start * 1000),  # seconds → ms
                    end_time=int(w.end * 1000),
                    confidence=w.probability if hasattr(w, 'probability') else 0.95,
                )
                for w in seg.words
                if w.word.strip()  # Skip empty/whitespace-only words
            )

        transcript_segments.append(TranscriptSegment(
            start_time=int(seg.start * 1000),
            end_time=int(seg.end * 1000),
            text=seg.text.strip(),
            words=words,
        ))

    return Transcript(
        video_id=video_id,
        language=info.language,
        segments=tuple(transcript_segments),
    )
```

## Timing Conversion

| Source                          | Format             | Target                 |
| ------------------------------- | ------------------ | ---------------------- |
| `segment.start` / `segment.end` | seconds (float)    | `int(val * 1000)` → ms |
| `word.start` / `word.end`       | seconds (float)    | `int(val * 1000)` → ms |
| DTO `start_time` / `end_time`   | milliseconds (int) | All downstream modules |

## Confidence Handling

faster-whisper provides:

- **Segment-level:** `segment.no_speech_prob` (0–1, higher = likely silence)
- **Word-level:** `word.probability` (0–1, higher = more confident)

```python
# Filter low-confidence segments
if seg.no_speech_prob > 0.8:
    continue  # Skip — likely not speech

# Word confidence for DTO
confidence = w.probability if hasattr(w, 'probability') else 0.95
```

## Empty Transcript Handling

Empty transcripts are valid (e.g., gameplay with no speech):

```python
def transcribe_video(model, audio_path, video_id, config) -> Transcript:
    segments, info = model.transcribe(audio_path, ...)
    segment_list = list(segments)

    if not segment_list:
        # No speech detected — return empty Transcript (valid state)
        return Transcript(
            video_id=video_id,
            language=config["transcription"]["language"],
            segments=tuple(),
        )

    return build_transcript(segment_list, info, video_id)
```

**Downstream impact of empty transcript:**

- Scoring: `keyword_score = 0.0`, `sentence_density = 0.0` (other signals still work)
- Hook generator: uses generic template (no keywords to extract)
- Subtitle: no transcript subtitles, only TTS narration subtitles

## Audio Input Requirements

- **Format:** WAV (16-bit PCM)
- **Sample rate:** 16kHz (Whisper standard)
- **Channels:** Mono
- **Extraction via FFmpeg:**
  ```bash
  ffmpeg -i input.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 audio.wav
  ```

## Caching / Idempotency

Transcription is deterministic (same audio → same output). Cache result:

```python
import json, os

cache_path = f"output/{video_id}/transcript.json"

if os.path.exists(cache_path):
    # Load cached transcript
    return load_transcript_from_json(cache_path, video_id)

# Run transcription
transcript = transcribe_video(model, audio_path, video_id, config)

# Cache result
os.makedirs(os.path.dirname(cache_path), exist_ok=True)
save_transcript_to_json(transcript, cache_path)
```

## Performance

| Model  | 1-hour audio (CPU) | RAM Peak |
| ------ | ------------------ | -------- |
| base   | ~5 min             | ~1 GB    |
| small  | ~10 min            | ~2 GB    |
| medium | ~20 min            | ~4 GB    |

## Anti-Patterns

```python
# ❌ Forgetting word_timestamps (breaks subtitles)
model.transcribe(audio, word_timestamps=False)

# ❌ Not consuming the generator (segments is lazy)
segments, info = model.transcribe(audio)
# segments not iterated → no transcription happens

# ❌ Using float seconds directly in DTOs
Word(start_time=seg.start)  # Wrong: needs int(ms)

# ❌ Crashing on empty transcript
assert len(segments) > 0  # Valid to have no speech

# ✅ Always consume generator, convert to ms, handle empty case
segment_list = list(segments)  # Force evaluation
```
