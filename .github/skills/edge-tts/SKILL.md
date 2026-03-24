---
name: edge-tts
description: "Edge TTS patterns for Shorts Factory. Use when implementing the tts module. Covers async synthesis, voice selection, word timestamp extraction, volume normalization, caching by text hash, and pyttsx3 fallback strategy."
---

# Edge TTS Skill

## When to Use

- Implementing the tts module
- Configuring voice selection and speech parameters
- Implementing TTS caching for determinism
- Handling offline/fallback scenarios

## Library

- **Package:** `edge-tts` (PyPI)
- **Service:** Microsoft Edge Read Aloud API (free, no API key)
- **Requirement:** Internet connection for synthesis
- **Fallback:** `pyttsx3` for offline (lower quality)

## Voice Configuration

```python
# From config.yaml
voice: "en-US-ChristopherNeural"  # config.tts.voice
rate: "+0%"                        # config.tts.rate
volume: "+0%"                      # config.tts.volume
```

**Recommended voices:**

| Voice                     | Gender | Style      | Use Case                    |
| ------------------------- | ------ | ---------- | --------------------------- |
| `en-US-ChristopherNeural` | Male   | Energetic  | Default, gameplay narration |
| `en-US-AriaNeural`        | Female | Expressive | Alternative                 |
| `en-US-GuyNeural`         | Male   | Casual     | Relaxed content             |

## Synthesis (Async)

```python
import edge_tts
import asyncio

async def synthesize_tts(
    text: str,
    output_path: str,
    voice: str = "en-US-ChristopherNeural",
    rate: str = "+0%",
    volume: str = "+0%",
) -> tuple[str, float]:
    """Synthesize text to audio file. Returns (path, duration_seconds)."""

    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        volume=volume,
    )

    await communicate.save(output_path)

    # Get duration via ffprobe
    duration = get_audio_duration(output_path)
    return output_path, duration

# Sync wrapper for pipeline use
def synthesize_sync(text: str, output_path: str, config: dict) -> tuple[str, float]:
    return asyncio.run(synthesize_tts(
        text=text,
        output_path=output_path,
        voice=config["tts"]["voice"],
        rate=config["tts"]["rate"],
        volume=config["tts"]["volume"],
    ))
```

## Word Timestamp Extraction

Edge TTS provides word-level timestamps via the `SubMaker` interface:

```python
import edge_tts

async def synthesize_with_timestamps(
    text: str,
    output_path: str,
    voice: str,
) -> tuple[str, float, list[dict]]:
    """Synthesize with word-level timing data."""

    communicate = edge_tts.Communicate(text=text, voice=voice)
    submaker = edge_tts.SubMaker()

    with open(output_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)

    # Extract word timings from submaker
    # SubMaker produces WebVTT-style cues with word offsets
    word_timings = []
    for offset, text_part in zip(submaker.offset, submaker.subs):
        # offset is in 100-nanosecond units (ticks)
        start_ms = offset // 10_000
        word_timings.append({
            "text": text_part,
            "start_ms": start_ms,
        })

    duration = get_audio_duration(output_path)
    return output_path, duration, word_timings
```

Word timestamps are used by the subtitle module for narration subtitle alignment.

## Volume Normalization

TTS output volume must be normalized before mixing with gameplay audio:

```python
import subprocess

def normalize_audio(input_path: str, output_path: str, target_lufs: int = -14):
    """Normalize to broadcast standard LUFS."""
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        output_path,
    ], capture_output=True, timeout=60)
```

- **Target:** -14 LUFS (broadcast standard)
- **Mixing ratio:** 30% narration / 70% gameplay (applied in renderer)

## Caching for Determinism

Edge TTS may produce slightly different audio across runs. Cache by text hash:

```python
import hashlib
import os

def get_cache_key(text: str, voice: str) -> str:
    raw = f"{text}|{voice}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def synthesize_cached(text: str, video_id: str, config: dict) -> tuple[str, float]:
    voice = config["tts"]["voice"]
    cache_key = get_cache_key(text, voice)
    cache_dir = f"output/{video_id}/tts_cache"
    cache_path = f"{cache_dir}/{cache_key}.mp3"

    if os.path.exists(cache_path):
        duration = get_audio_duration(cache_path)
        return cache_path, duration

    os.makedirs(cache_dir, exist_ok=True)
    path, duration = synthesize_sync(text, cache_path, config)
    return path, duration
```

## Offline Fallback (pyttsx3)

```python
def synthesize_fallback(text: str, output_path: str) -> tuple[str, float]:
    """Offline TTS via pyttsx3 (lower quality)."""
    import pyttsx3

    engine = pyttsx3.init()
    engine.setProperty("rate", 150)
    engine.setProperty("volume", 0.9)
    engine.save_to_file(text, output_path)
    engine.runAndWait()

    duration = get_audio_duration(output_path)
    return output_path, duration
```

**Fallback trigger:** Edge TTS raises any exception (network error, API error, timeout).

## Full TTS Pipeline Pattern

```python
def process_tts(hook_result, config: dict) -> TTSResult:
    """Complete TTS module entry point."""
    text = f"{hook_result.hook_text}. {hook_result.story_text}"
    video_id = hook_result.clip_id[:16]  # Derive from clip context

    try:
        audio_path, duration = synthesize_cached(text, video_id, config)
    except Exception:
        logger.warning("Edge TTS failed, using offline fallback")
        audio_path, duration = synthesize_fallback(text, f"output/{video_id}/tts_fallback.wav")

    return TTSResult(
        clip_id=hook_result.clip_id,
        audio_path=audio_path,
        duration_seconds=duration,
        sample_rate=44100,
    )
```

## Audio Duration Helper

```python
import subprocess, json

def get_audio_duration(audio_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", audio_path],
        capture_output=True, text=True, timeout=10,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])
```

## Anti-Patterns

```python
# ❌ Not caching TTS output (non-deterministic across runs)
audio = synthesize_fresh(text)  # Different output each time

# ❌ No fallback (pipeline crashes when offline)
await edge_tts.Communicate(text).save(path)  # Network error → crash

# ❌ Blocking event loop in async context
asyncio.run(synthesize())  # Inside already-running loop

# ❌ TTS audio louder than gameplay (overwhelms content)
# Mixing ratio must be 30% narration / 70% gameplay

# ✅ Cache by text hash, fallback to pyttsx3, normalize volume
```
