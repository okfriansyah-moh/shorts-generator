"""Keyword scoring for scene engagement analysis.

Scores scenes based on the density of engagement keywords found in the
transcript words that overlap with the scene's time range.
"""

from __future__ import annotations

import re

from contracts.transcript import Transcript

# Default engagement keywords used when config provides none.
_DEFAULT_KEYWORDS: tuple[str, ...] = (
    "amazing",
    "insane",
    "crazy",
    "epic",
    "clutch",
    "perfect",
    "win",
    "won",
    "lose",
    "lost",
    "kill",
    "killed",
    "death",
    "best",
    "worst",
    "first",
    "last",
    "final",
    "boss",
    "rare",
    "secret",
    "hidden",
    "trick",
    "tip",
    "easy",
    "hard",
    "impossible",
    "never",
    "always",
    "must",
    "need",
    "important",
    "critical",
    "incredible",
    "unbelievable",
    "shocking",
    "unexpected",
    "surprise",
)

_STRIP_PATTERN = re.compile(r"[^a-z]")


def get_keywords(config: dict) -> frozenset[str]:
    """Return the configured keyword set, falling back to defaults."""
    configured: list[str] = config.get("scoring", {}).get("keywords", [])
    if configured:
        return frozenset(kw.lower() for kw in sorted(configured))
    return frozenset(_DEFAULT_KEYWORDS)


def score_keyword(
    scene_start_ms: int,
    scene_end_ms: int,
    transcript: Transcript,
    keywords: frozenset[str],
) -> float:
    """Compute keyword engagement score for a scene's time window.

    Score = min(keyword_count / total_word_count, 1.0).
    Returns 0.0 when no words fall within the scene boundaries.
    """
    scene_words: list[str] = []
    for segment in transcript.segments:
        for word in segment.words:
            if word.start_time >= scene_start_ms and word.end_time <= scene_end_ms:
                scene_words.append(word.text.lower())

    if not scene_words:
        return 0.0

    keyword_count = sum(
        1 for w in scene_words if _STRIP_PATTERN.sub("", w) in keywords
    )
    return min(keyword_count / len(scene_words), 1.0)
