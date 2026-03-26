"""SubtitleResult DTO for Shorts Factory.

Produced by the subtitle module. Consumed by the renderer module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubtitleResult:
    """Frozen DTO representing generated ASS subtitles for a clip.

    Fields:
        clip_id: Reference to parent clip. 16 lowercase hex chars.
        ass_path: Absolute path to the generated .ass subtitle file. Non-empty.
        has_transcript_subs: True if transcript-based subtitles are present.
        has_narration_subs: True if TTS narration subtitles are present.
        subtitle_count: Total number of dialogue events in the ASS file. >= 0.
    """

    clip_id: str
    ass_path: str
    has_transcript_subs: bool
    has_narration_subs: bool
    subtitle_count: int
