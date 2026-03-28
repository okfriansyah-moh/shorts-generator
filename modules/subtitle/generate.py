"""ASS subtitle generation with word-level karaoke timing.

Generates .ass subtitle files from transcript word-level timestamps
and TTS narration timings. Supports safe-area positioning to avoid
overlap with the face cam region (bottom 35%).
"""

from __future__ import annotations

import logging
import os

from contracts.clip import ClipDefinition
from contracts.subtitle import SubtitleResult
from contracts.transcript import Transcript, Word
from contracts.tts import TTSResult, TTSWordTiming

logger = logging.getLogger(__name__)


def _format_ass_time(ms: int) -> str:
    """Convert milliseconds to ASS time format H:MM:SS.cc."""
    if ms < 0:
        ms = 0
    total_seconds = ms / 1000.0
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    centiseconds = int((total_seconds * 100) % 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _generate_ass_header(config: dict) -> str:
    """Generate ASS file header with style definitions."""
    sub_config = config.get("subtitle", {})
    font_name = sub_config.get("font_name", "Arial")
    font_size = sub_config.get("font_size", 48)
    outline_width = sub_config.get("outline_width", 3)
    margin_bottom = sub_config.get("margin_bottom", 700)

    return (
        "[Script Info]\n"
        "Title: Shorts Factory Subtitles\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Transcript,{font_name},{font_size},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline_width},1,"
        f"8,20,20,{margin_bottom},1\n"
        f"Style: Narration,{font_name},42,&H0000FFFF,&H000000FF,"
        f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline_width},1,"
        "8,20,20,200,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )


def _chunk_words(words: list[Word], max_per_line: int = 7) -> list[list[Word]]:
    """Group words into display lines of at most max_per_line words.

    Also breaks at sentence-ending punctuation.
    """
    chunks: list[list[Word]] = []
    current: list[Word] = []

    for word in words:
        current.append(word)
        is_sentence_end = word.text.rstrip().endswith((".", "!", "?"))
        if len(current) >= max_per_line or is_sentence_end:
            chunks.append(current)
            current = []

    if current:
        chunks.append(current)

    return chunks


def _chunk_tts_words(
    words: list[TTSWordTiming],
    max_per_line: int = 7,
) -> list[list[TTSWordTiming]]:
    """Group TTS word timings into display lines."""
    chunks: list[list[TTSWordTiming]] = []
    current: list[TTSWordTiming] = []

    for word in words:
        current.append(word)
        is_sentence_end = word.text.rstrip().endswith((".", "!", "?"))
        if len(current) >= max_per_line or is_sentence_end:
            chunks.append(current)
            current = []

    if current:
        chunks.append(current)

    return chunks


def _generate_transcript_karaoke(
    words: list[Word],
    clip_start_ms: int,
) -> list[str]:
    """Generate karaoke-style ASS dialogue events from transcript words."""
    events: list[str] = []
    chunks = _chunk_words(words)

    for group in chunks:
        if not group:
            continue

        group_start = _format_ass_time(group[0].start_time - clip_start_ms)
        group_end = _format_ass_time(group[-1].end_time - clip_start_ms)

        # Build karaoke tags
        parts: list[str] = []
        for w in group:
            duration_cs = max((w.end_time - w.start_time) // 10, 1)
            parts.append(f"{{\\kf{duration_cs}}}{w.text}")

        text = " ".join(parts)
        events.append(
            f"Dialogue: 0,{group_start},{group_end},Transcript,,0,0,0,,{text}"
        )

    return events


def _generate_narration_subs(
    tts_words: list[TTSWordTiming],
) -> list[str]:
    """Generate timed subtitle events for TTS narration."""
    events: list[str] = []
    chunks = _chunk_tts_words(tts_words)

    for group in chunks:
        if not group:
            continue

        group_start = _format_ass_time(group[0].start_ms)
        group_end = _format_ass_time(group[-1].end_ms)
        text = " ".join(w.text for w in group)
        events.append(
            f"Dialogue: 1,{group_start},{group_end},Narration,,0,0,0,,{text}"
        )

    return events


def _extract_clip_words(
    transcript: Transcript,
    clip_start_ms: int,
    clip_end_ms: int,
) -> list[Word]:
    """Extract words from transcript that fall within the clip time range."""
    words: list[Word] = []
    for segment in transcript.segments:
        for word in segment.words:
            if clip_start_ms <= word.start_time < clip_end_ms:
                words.append(word)
    # Already sorted by start_time from transcript contract
    return words


def process(
    clip: ClipDefinition,
    transcript: Transcript,
    tts_result: TTSResult,
    config: dict,
    output_dir: str,
) -> SubtitleResult:
    """Generate ASS subtitle file for a clip.

    Args:
        clip: Clip definition with time boundaries.
        transcript: Full video transcript with word-level timestamps.
        tts_result: TTS synthesis result with optional word timings.
        config: Configuration dict (subtitle section used).
        output_dir: Base output directory for the video.

    Returns:
        SubtitleResult with path to the generated .ass file.
    """
    folder_name = f"shorts-{clip.clip_index + 1}"
    clip_dir = os.path.join(output_dir, "clips", folder_name)
    os.makedirs(clip_dir, exist_ok=True)
    ass_path = os.path.join(clip_dir, "subtitles.ass")

    # Build ASS content
    header = _generate_ass_header(config)
    events: list[str] = []

    # Transcript subtitles (gameplay speech)
    clip_words = _extract_clip_words(
        transcript, clip.start_time, clip.end_time,
    )
    has_transcript_subs = len(clip_words) > 0
    if has_transcript_subs:
        events.extend(_generate_transcript_karaoke(clip_words, clip.start_time))

    # Narration subtitles (TTS voice-over)
    tts_words = list(tts_result.word_timings)
    has_narration_subs = len(tts_words) > 0
    if has_narration_subs:
        events.extend(_generate_narration_subs(tts_words))

    # Sort events deterministically by start time then layer.
    # Each event is "Dialogue: {layer},{start},{end},..." — extract start
    # time (field 1) and layer (field 0) for a correct chronological sort.
    def _event_sort_key(line: str) -> tuple[str, str]:
        parts = line.split(",", 3)
        # parts[0] = "Dialogue: {layer}", parts[1] = start time
        return (parts[1], parts[0]) if len(parts) >= 2 else (line, "")
    events.sort(key=_event_sort_key)

    # Write ASS file atomically
    ass_content = header + "\n".join(events) + "\n"
    tmp_path = f"{ass_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(ass_content)
    os.replace(tmp_path, ass_path)

    subtitle_count = len(events)

    logger.info(
        "Subtitles generated",
        extra={
            "clip_id": clip.clip_id,
            "transcript_events": sum(1 for e in events if ",Transcript," in e),
            "narration_events": sum(1 for e in events if ",Narration," in e),
            "total_events": subtitle_count,
        },
    )

    return SubtitleResult(
        clip_id=clip.clip_id,
        ass_path=ass_path,
        has_transcript_subs=has_transcript_subs,
        has_narration_subs=has_narration_subs,
        subtitle_count=subtitle_count,
    )
