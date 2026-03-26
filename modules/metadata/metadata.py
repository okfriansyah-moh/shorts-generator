"""Metadata generation module — builds YouTube-ready title, description, and tags.

Template-driven and deterministic. Combines hook text, story text, transcript
keywords, and channel configuration to produce metadata that satisfies
YouTube's field constraints:
  - title: 40–60 characters
  - description: 150–300 characters
  - tags: 10–15 unique lowercase strings (sorted)
"""

from __future__ import annotations

import logging

from contracts.clip import ClipDefinition
from contracts.hook import HookResult
from contracts.metadata import MetadataResult
from contracts.transcript import Transcript

logger = logging.getLogger(__name__)

# Default gaming tags used when keyword pool is too small.
_DEFAULT_GAMING_TAGS: tuple[str, ...] = (
    "gaming",
    "clips",
    "shorts",
    "gameplay",
    "highlight",
    "moments",
    "games",
    "gamer",
    "epic",
    "best",
    "funny",
    "fails",
    "wins",
    "clutch",
    "montage",
)

# Engagement keywords matched against transcript for tag extraction.
_ENGAGEMENT_KEYWORDS: frozenset[str] = frozenset({
    "kill", "shot", "win", "clutch", "snipe", "headshot", "ace",
    "flick", "rush", "push", "fight", "attack", "defend", "dodge",
    "bomb", "plant", "defuse", "score", "goal", "save", "block",
    "combo", "streak", "rampage", "dominate", "destroy",
    "boss", "tower", "base", "champion", "hero",
    "weapon", "shield", "armor", "spell", "ultimate",
    "ability", "power", "damage", "critical", "execute",
    "escape", "retreat", "ambush", "flank", "rotate", "engage",
})


def _truncate_at_word(text: str, max_len: int) -> str:
    """Truncate *text* to at most *max_len* chars, breaking at a word boundary.

    If the character immediately after the cut point is a space (meaning the
    cut falls exactly at a word boundary), the truncated text is returned as-is.
    Otherwise, the text is trimmed back to the last space to avoid splitting
    a word mid-way.
    """
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    # If the cut falls exactly at a word boundary, no further trimming needed.
    if max_len < len(text) and text[max_len] == " ":
        return truncated
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space]
    return truncated


def _build_title(hook_result: HookResult, config: dict) -> str:
    """Build a YouTube title of 40–60 characters from hook + story text.

    Strategy:
      1. Start with hook_text.
      2. Append story_text words one-by-one until reaching min_chars.
      3. Truncate at word boundary to max_chars.
      4. Pad to min_chars with spaces if still short (edge case).
    """
    meta_cfg = config.get("metadata", {})
    min_chars: int = meta_cfg.get("title_min_chars", 40)
    max_chars: int = meta_cfg.get("title_max_chars", 60)

    title = hook_result.hook_text.strip()
    story_words = hook_result.story_text.strip().split()

    # Extend with story words until we reach min_chars.
    for word in story_words:
        if len(title) >= min_chars:
            break
        candidate = title + " " + word
        if len(candidate) > max_chars:
            break
        title = candidate

    # Truncate if overshooting max.
    if len(title) > max_chars:
        title = _truncate_at_word(title, max_chars)

    # Final pad: right-pad with spaces to reach min_chars, then clamp.
    if len(title) < min_chars:
        title = title.ljust(min_chars)[:max_chars]

    return title


def _build_description(
    hook_result: HookResult,
    transcript: Transcript,
    config: dict,
) -> str:
    """Build a YouTube description of 150–300 characters.

    Combines: story_text, a transcript excerpt (if available), and hashtags
    from channel config plus standard #Shorts / #Gaming / #Clips hashtags.
    """
    meta_cfg = config.get("metadata", {})
    min_chars: int = meta_cfg.get("description_min_chars", 150)
    max_chars: int = meta_cfg.get("description_max_chars", 300)
    channel_hashtags: list[str] = sorted(
        config.get("channel", {}).get("hashtags", [])
    )

    parts: list[str] = []

    story = hook_result.story_text.strip()
    if story:
        parts.append(story)

    # Add a short transcript excerpt if speech is present.
    if transcript.total_words > 0 and transcript.segments:
        excerpt = transcript.segments[0].text.strip()
        if excerpt:
            parts.append(f'"{excerpt[:80]}"')

    # Standard gaming hashtags (deterministic order).
    standard_tags = ["#Shorts", "#Gaming", "#Clips"]
    all_hashtags = sorted(f"#{h}" for h in channel_hashtags) + standard_tags
    if all_hashtags:
        parts.append(" ".join(all_hashtags))

    desc = " ".join(parts)

    # Pad to min_chars with a generic watch-prompt line if needed.
    if len(desc) < min_chars:
        pad_line = "Watch the best gaming clips and highlight moments. Like and subscribe!"
        desc = desc + " " + pad_line if desc else pad_line

    if len(desc) > max_chars:
        desc = _truncate_at_word(desc, max_chars)

    return desc.strip()


def _build_tags(
    hook_result: HookResult,
    transcript: Transcript,
    config: dict,
) -> tuple[str, ...]:
    """Build a sorted tuple of 10–15 unique lowercase tags.

    Sources (in priority order):
      1. keyword_source from HookResult (direct keyword match).
      2. Transcript words matched against _ENGAGEMENT_KEYWORDS.
      3. channel.static_tags from config.
      4. _DEFAULT_GAMING_TAGS as fallback padding.
    """
    meta_cfg = config.get("metadata", {})
    min_count: int = meta_cfg.get("tag_count_min", 10)
    max_count: int = meta_cfg.get("tag_count_max", 15)
    static_tags: list[str] = config.get("channel", {}).get("static_tags", [])

    tags: set[str] = set()

    # 1. Hook keyword sources.
    for kw in hook_result.keyword_source:
        clean = kw.strip().lower()
        if clean:
            tags.add(clean)

    # 2. Engagement words from transcript.
    for seg in transcript.segments:
        for word in seg.words:
            if word.text.strip().lower() in _ENGAGEMENT_KEYWORDS:
                tags.add(word.text.strip().lower())

    # 3. Static channel tags.
    for tag in static_tags:
        clean = tag.strip().lower()
        if clean:
            tags.add(clean)

    # 4. Pad with default gaming tags (deterministic order via tuple).
    for tag in _DEFAULT_GAMING_TAGS:
        if len(tags) >= max_count:
            break
        tags.add(tag)

    # Sort and apply [min_count, max_count] bounds.
    sorted_tags = sorted(tags)[:max_count]

    # If still below min (edge case: almost empty input + few defaults), pad.
    for tag in _DEFAULT_GAMING_TAGS:
        if len(sorted_tags) >= min_count:
            break
        if tag not in sorted_tags:
            sorted_tags.append(tag)

    sorted_tags = sorted(sorted_tags[:max_count])

    # Enforce total character budget (contract: total chars <= 500).
    max_total_chars: int = meta_cfg.get("tag_total_chars_max", 500)
    total_chars = sum(len(t) for t in sorted_tags)
    while total_chars > max_total_chars and sorted_tags:
        # Drop the longest tag to get under budget.
        longest_idx = max(range(len(sorted_tags)), key=lambda i: len(sorted_tags[i]))
        removed = sorted_tags.pop(longest_idx)
        total_chars -= len(removed)

    return tuple(sorted_tags)


def process(
    hook_result: HookResult,
    transcript: Transcript,
    clip: ClipDefinition,
    config: dict,
) -> MetadataResult:
    """Generate YouTube-ready metadata for a clip.

    Deterministic: same input + same config always produces identical output.
    No network calls, no LLM, no randomness.

    Args:
        hook_result: Hook and story text from hook_generator.
        transcript: Full video transcript from transcription module.
        clip: ClipDefinition for the clip being processed.
        config: Full pipeline config dict.

    Returns:
        MetadataResult DTO with title, description, and tags.
    """
    title = _build_title(hook_result, config)
    description = _build_description(hook_result, transcript, config)
    tags = _build_tags(hook_result, transcript, config)
    category = config.get("metadata", {}).get("category", "Gaming")

    logger.info(
        "Metadata generated",
        extra={
            "clip_id": clip.clip_id,
            "stage": "metadata",
            "status": "ok",
            "title_len": len(title),
            "description_len": len(description),
            "tag_count": len(tags),
        },
    )

    return MetadataResult(
        clip_id=clip.clip_id,
        title=title,
        description=description,
        tags=tags,
        category=category,
    )
