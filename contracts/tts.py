"""TTS DTOs for Shorts Factory.

Produced by the tts module. Consumed by subtitle and renderer modules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TTSWordTiming:
    """Frozen DTO representing a single TTS word with timing.

    Fields:
        text: Single word. Non-empty.
        start_ms: Word start offset in milliseconds relative to audio start. >= 0.
        end_ms: Word end offset in milliseconds. > start_ms.
    """

    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class TTSResult:
    """Frozen DTO representing synthesised narration audio for a clip.

    Fields:
        clip_id: Reference to parent clip. 16 lowercase hex chars.
        audio_path: Absolute path to the normalised audio file. Non-empty.
        duration_seconds: Audio duration in seconds. > 0.0.
        sample_rate: Audio sample rate in Hz. Default 44100.
        word_timings: Word-level timestamps for subtitle alignment. May be empty.
        engine_used: TTS engine identifier ("edge-tts" or "pyttsx3").
    """

    clip_id: str
    audio_path: str
    duration_seconds: float
    sample_rate: int
    word_timings: tuple[TTSWordTiming, ...]
    engine_used: str
