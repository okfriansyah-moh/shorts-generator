"""Transcript DTOs for Shorts Factory.

Produced by the transcription module. Consumed by scoring,
hook_generator, subtitle, and metadata modules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    """Frozen DTO representing a single transcribed word with timestamps.

    Fields:
        text: Single word. Non-empty, stripped of leading/trailing whitespace.
        start_time: Word start timestamp in milliseconds. >= 0.
        end_time: Word end timestamp in milliseconds. > start_time.
        confidence: Transcription confidence. 0.0 <= confidence <= 1.0.
    """

    text: str
    start_time: int
    end_time: int
    confidence: float


@dataclass(frozen=True)
class TranscriptSegment:
    """Frozen DTO representing a contiguous transcript segment.

    Fields:
        text: Full segment text. May be empty if no speech in this time range.
        start_time: Segment start in milliseconds. >= 0.
        end_time: Segment end in milliseconds. > start_time.
        words: Word-level breakdown sorted by start_time ASC. May be empty.
        confidence: Average transcription confidence. 0.0 <= confidence <= 1.0.
    """

    text: str
    start_time: int
    end_time: int
    words: tuple[Word, ...]
    confidence: float


@dataclass(frozen=True)
class Transcript:
    """Frozen DTO representing the full word-level transcript for a video.

    Fields:
        video_id: Parent video reference. 16 lowercase hex chars.
        segments: Ordered transcript segments, sorted by start_time ASC. May be empty.
        total_words: Total word count. >= 0. Equals sum(len(s.words) for s in segments).
        language: Detected language code (ISO 639-1, e.g. "en").
    """

    video_id: str
    segments: tuple[TranscriptSegment, ...]
    total_words: int
    language: str
