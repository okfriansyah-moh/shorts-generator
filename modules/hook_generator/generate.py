"""Hook and story text generation from transcript keywords.

Template-based, deterministic, no LLMs. Template selection uses
hash-based rotation to avoid randomness while preventing reuse
within a batch.
"""

from __future__ import annotations

import hashlib
import logging
import re

from contracts.clip import ClipDefinition
from contracts.hook import HookResult
from contracts.transcript import Transcript

from .templates import (
    DEFAULT_ADJECTIVES,
    DEFAULT_SUBJECTS,
    FALLBACK_TEMPLATES,
    HOOK_TEMPLATES,
)

logger = logging.getLogger(__name__)

# Engagement keywords ranked by relevance for gaming content
_ENGAGEMENT_KEYWORDS: tuple[str, ...] = (
    "kill", "shot", "win", "clutch", "snipe", "headshot", "ace",
    "flick", "rush", "push", "fight", "attack", "defend", "dodge",
    "bomb", "plant", "defuse", "score", "goal", "save", "block",
    "combo", "streak", "rampage", "dominate", "destroy", "team",
    "enemy", "boss", "dragon", "tower", "turret", "base",
    "champion", "hero", "weapon", "shield", "armor", "spell",
    "ultimate", "ability", "power", "damage", "critical", "execute",
    "escape", "retreat", "ambush", "flank", "rotate", "engage",
)


def _extract_keywords(
    transcript: Transcript,
    clip_start_ms: int,
    clip_end_ms: int,
) -> list[str]:
    """Extract engagement keywords from the transcript within clip range."""
    words_in_range: list[str] = []
    for segment in transcript.segments:
        for word in segment.words:
            if clip_start_ms <= word.start_time < clip_end_ms:
                clean = re.sub(r"[^a-zA-Z]", "", word.text.lower())
                if clean:
                    words_in_range.append(clean)

    # Return engagement keywords found, sorted for determinism
    found = sorted(set(w for w in words_in_range if w in _ENGAGEMENT_KEYWORDS))
    return found


def _select_subject(keywords: list[str], clip_id: str) -> str:
    """Select the best subject keyword or fall back to default."""
    if keywords:
        return keywords[0]
    idx = int(hashlib.sha256(clip_id.encode()).hexdigest()[:8], 16)
    return DEFAULT_SUBJECTS[idx % len(DEFAULT_SUBJECTS)]


def _select_adjective(clip_id: str) -> str:
    """Select an adjective via deterministic rotation."""
    idx = int(hashlib.sha256(f"adj_{clip_id}".encode()).hexdigest()[:8], 16)
    return DEFAULT_ADJECTIVES[idx % len(DEFAULT_ADJECTIVES)]


def _select_template_index(clip_id: str, pool_size: int) -> int:
    """Deterministic template selection based on clip_id hash."""
    return int(hashlib.sha256(clip_id.encode()).hexdigest()[:8], 16) % pool_size


def _truncate_to_words(text: str, max_words: int) -> str:
    """Truncate text to at most max_words, preserving whole words."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def _fill_template(
    template: str,
    subject: str,
    adjective: str,
) -> str:
    """Fill template placeholders with extracted values."""
    result = template.replace("{subject}", subject)
    result = result.replace("{adjective}", adjective)
    result = result.replace("{action}", subject)
    return result


def process(
    clip: ClipDefinition,
    transcript: Transcript,
    config: dict,
    used_template_ids: frozenset[int] = frozenset(),
) -> tuple[HookResult, frozenset[int]]:
    """Generate hook and story text for a clip.

    Args:
        clip: The clip definition with time range and clip_id.
        transcript: Full video transcript with word-level timestamps.
        config: Configuration dict (hook_generator section used).
        used_template_ids: Immutable set of already-used template indices
                          in this batch. The updated set is returned.

    Returns:
        Tuple of (HookResult, updated_used_template_ids).
    """
    working_used = set(used_template_ids)

    hook_config = config.get("hook_generator", {})
    max_hook_words = hook_config.get("max_hook_words", 15)
    max_story_words = hook_config.get("max_story_words", 40)

    # Extract keywords from the clip's time range
    keywords = _extract_keywords(transcript, clip.start_time, clip.end_time)
    has_transcript = bool(keywords) or transcript.total_words > 0

    if has_transcript:
        pool = HOOK_TEMPLATES
        pool_name = "hook"
    else:
        pool = FALLBACK_TEMPLATES
        pool_name = "fallback"

    # Deterministic template selection with batch dedup
    base_idx = _select_template_index(clip.clip_id, len(pool))
    selected_idx = base_idx

    # Try to find an unused template; wrap around if needed
    for offset in range(len(pool)):
        candidate = (base_idx + offset) % len(pool)
        template_key = candidate if pool_name == "hook" else candidate + len(HOOK_TEMPLATES)
        if template_key not in working_used:
            selected_idx = candidate
            working_used.add(template_key)
            break
    else:
        # All templates used in this batch — reset and reuse
        logger.warning(
            "Template pool exhausted, reusing templates",
            extra={"clip_id": clip.clip_id, "pool": pool_name},
        )
        working_used = set()
        selected_idx = base_idx
        template_key = selected_idx if pool_name == "hook" else selected_idx + len(HOOK_TEMPLATES)
        working_used.add(template_key)

    hook_template, story_template = pool[selected_idx]
    template_id = f"{pool_name}_{selected_idx}"

    # Fill templates
    subject = _select_subject(keywords, clip.clip_id)
    adjective = _select_adjective(clip.clip_id)

    hook_text = _fill_template(hook_template, subject, adjective)
    story_text = _fill_template(story_template, subject, adjective)

    # Enforce word limits
    hook_text = _truncate_to_words(hook_text, max_hook_words)
    story_text = _truncate_to_words(story_text, max_story_words)

    logger.info(
        "Hook generated",
        extra={
            "clip_id": clip.clip_id,
            "template_id": template_id,
            "keywords": keywords[:5],
            "hook_words": len(hook_text.split()),
        },
    )

    return HookResult(
        clip_id=clip.clip_id,
        video_id=clip.video_id,
        hook_text=hook_text,
        story_text=story_text,
        template_id=template_id,
        keyword_source=tuple(keywords),
    ), frozenset(working_used)
