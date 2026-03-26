"""HookResult DTO for Shorts Factory.

Produced by the hook_generator module. Consumed by tts, thumbnail,
and metadata modules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HookResult:
    """Frozen DTO representing generated hook and story text for a clip.

    Fields:
        clip_id: Reference to parent clip. 16 lowercase hex chars.
        video_id: Parent video reference. 16 lowercase hex chars.
        hook_text: Attention-grabbing opening line. 1–15 words.
        story_text: Brief narrative context. 1–40 words.
        template_id: Identifier of the template used. Non-empty string.
        keyword_source: Keywords extracted from transcript. May be empty.
    """

    clip_id: str
    video_id: str
    hook_text: str
    story_text: str
    template_id: str
    keyword_source: tuple[str, ...]
