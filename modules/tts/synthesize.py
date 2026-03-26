"""TTS synthesis with Edge TTS and pyttsx3 fallback.

Implements text-to-speech synthesis with caching by text hash,
volume normalisation via FFmpeg, and offline fallback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from typing import Optional

from contracts.hook import HookResult
from contracts.tts import TTSResult, TTSWordTiming

logger = logging.getLogger(__name__)


def _get_cache_key(text: str, voice: str) -> str:
    """Content-addressable cache key from text + voice."""
    raw = f"{text}|{voice}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", audio_path,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[:200]}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def _normalize_audio(
    input_path: str,
    output_path: str,
    target_lufs: int = -14,
) -> None:
    """Normalise audio to target LUFS using FFmpeg loudnorm."""
    tmp_path = f"{output_path}.tmp"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
            "-ar", "44100", "-ac", "1",
            tmp_path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg normalize failed: {result.stderr[:200]}")
    os.replace(tmp_path, output_path)


def _synthesize_edge_tts(
    text: str,
    output_path: str,
    voice: str,
    rate: str,
    volume: str,
) -> tuple[str, float, list[TTSWordTiming]]:
    """Synthesise via Edge TTS with word-level timestamps.

    Returns (audio_path, duration_seconds, word_timings).
    """
    import asyncio

    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("edge-tts package not installed") from exc

    async def _do_synthesis() -> tuple[list[TTSWordTiming], None]:
        communicate = edge_tts.Communicate(
            text=text, voice=voice, rate=rate, volume=volume,
        )
        word_timings: list[TTSWordTiming] = []
        prev_start_ms: Optional[int] = None
        prev_text: Optional[str] = None

        with open(output_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    offset_ms = chunk["offset"] // 10_000  # 100ns ticks → ms
                    duration_ms = chunk["duration"] // 10_000
                    word_text = chunk["text"]

                    # Close previous word timing
                    if prev_text is not None and prev_start_ms is not None:
                        word_timings.append(TTSWordTiming(
                            text=prev_text,
                            start_ms=prev_start_ms,
                            end_ms=offset_ms,
                        ))

                    prev_start_ms = offset_ms
                    prev_text = word_text

        # Close last word
        if prev_text is not None and prev_start_ms is not None:
            end_ms = prev_start_ms + max(duration_ms, 100)
            word_timings.append(TTSWordTiming(
                text=prev_text,
                start_ms=prev_start_ms,
                end_ms=end_ms,
            ))

        return word_timings, None

    # Run async synthesis in sync context
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            timings, _ = pool.submit(
                lambda: asyncio.run(_do_synthesis())
            ).result(timeout=120)
    else:
        timings, _ = asyncio.run(_do_synthesis())

    duration = _get_audio_duration(output_path)
    return output_path, duration, timings


def _synthesize_pyttsx3(
    text: str,
    output_path: str,
) -> tuple[str, float]:
    """Offline fallback via pyttsx3."""
    try:
        import pyttsx3
    except ImportError as exc:
        raise RuntimeError("pyttsx3 not installed for fallback") from exc

    engine = pyttsx3.init()
    engine.setProperty("rate", 150)
    engine.setProperty("volume", 0.9)
    engine.save_to_file(text, output_path)
    engine.runAndWait()

    duration = _get_audio_duration(output_path)
    return output_path, duration


def process(
    hook_result: HookResult,
    config: dict,
    output_dir: str,
) -> TTSResult:
    """Synthesise TTS audio for hook + story text.

    Args:
        hook_result: Generated hook and story text.
        config: Configuration dict (tts section used).
        output_dir: Base output directory for the video.

    Returns:
        TTSResult with normalised audio path and word timings.
    """
    tts_config = config.get("tts", {})
    voice = tts_config.get("voice", "en-US-AriaNeural")
    rate = tts_config.get("rate", "+0%")
    volume = tts_config.get("volume", "+0%")
    target_lufs = tts_config.get("volume_normalization_lufs", -14)

    text = f"{hook_result.hook_text}. {hook_result.story_text}"

    # Cache directory
    cache_dir = os.path.join(output_dir, "tts_cache")
    os.makedirs(cache_dir, exist_ok=True)

    cache_key = _get_cache_key(text, voice)
    raw_path = os.path.join(cache_dir, f"{cache_key}_raw.mp3")
    normalized_path = os.path.join(cache_dir, f"{cache_key}.wav")

    # Check cache — idempotent
    if os.path.exists(normalized_path):
        duration = _get_audio_duration(normalized_path)
        logger.info(
            "TTS cache hit",
            extra={"clip_id": hook_result.clip_id, "cache_key": cache_key},
        )
        return TTSResult(
            clip_id=hook_result.clip_id,
            audio_path=normalized_path,
            duration_seconds=duration,
            sample_rate=tts_config.get("sample_rate", 44100),
            word_timings=(),
            engine_used="cached",
        )

    # Try Edge TTS first
    word_timings: tuple[TTSWordTiming, ...] = ()
    engine_used = "edge-tts"

    try:
        _, _, timings_list = _synthesize_edge_tts(
            text, raw_path, voice, rate, volume,
        )
        word_timings = tuple(timings_list)
        logger.info(
            "Edge TTS synthesis complete",
            extra={"clip_id": hook_result.clip_id, "words": len(word_timings)},
        )
    except Exception as edge_err:
        logger.warning(
            "Edge TTS failed, trying pyttsx3 fallback",
            extra={"clip_id": hook_result.clip_id, "error": str(edge_err)[:100]},
        )
        engine_used = "pyttsx3"
        try:
            _synthesize_pyttsx3(text, raw_path)
        except Exception as fallback_err:
            logger.error(
                "All TTS engines failed",
                extra={
                    "clip_id": hook_result.clip_id,
                    "error": str(fallback_err)[:100],
                },
            )
            raise RuntimeError(
                f"TTS synthesis failed for clip {hook_result.clip_id}"
            ) from fallback_err

    # Normalise volume
    try:
        _normalize_audio(raw_path, normalized_path, target_lufs)
    except Exception as norm_err:
        logger.warning(
            "Volume normalization failed, using raw audio",
            extra={"clip_id": hook_result.clip_id, "error": str(norm_err)[:100]},
        )
        # Fall back to raw audio if normalisation fails
        if os.path.exists(raw_path):
            os.replace(raw_path, normalized_path)
        else:
            raise

    duration = _get_audio_duration(normalized_path)

    # Clean up raw file
    if os.path.exists(raw_path):
        try:
            os.remove(raw_path)
        except OSError:
            pass

    logger.info(
        "TTS processing complete",
        extra={
            "clip_id": hook_result.clip_id,
            "engine": engine_used,
            "duration_s": round(duration, 2),
        },
    )

    return TTSResult(
        clip_id=hook_result.clip_id,
        audio_path=normalized_path,
        duration_seconds=duration,
        sample_rate=tts_config.get("sample_rate", 44100),
        word_timings=word_timings,
        engine_used=engine_used,
    )
