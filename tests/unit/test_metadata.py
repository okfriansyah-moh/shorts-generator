"""Unit tests for the metadata module."""

from __future__ import annotations

import pytest  # noqa: F401 — used via pytest.raises

from contracts.clip import ClipDefinition
from contracts.hook import HookResult
from contracts.metadata import MetadataResult
from contracts.scoring import ScoredScene
from contracts.transcript import Transcript, TranscriptSegment, Word
from modules.metadata.metadata import (
    _build_description,
    _build_tags,
    _build_title,
    _truncate_at_word,
    process,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_scored_scene(
    scene_id: str = "a1b2c3d4e5f67890_0_45000",
    video_id: str = "a1b2c3d4e5f67890",
    start_time: int = 0,
    end_time: int = 45000,
) -> ScoredScene:
    return ScoredScene(
        scene_id=scene_id,
        video_id=video_id,
        start_time=start_time,
        end_time=end_time,
        duration=float(end_time - start_time) / 1000.0,
        keyword_score=0.5,
        audio_energy_score=0.6,
        face_presence_score=0.7,
        scene_activity_score=0.5,
        sentence_density_score=0.5,
        composite_score=0.57,
        rank=1,
    )


def _make_clip(
    clip_id: str = "abcd1234abcd1234",
    video_id: str = "a1b2c3d4e5f67890",
) -> ClipDefinition:
    scene = _make_scored_scene(video_id=video_id)
    return ClipDefinition(
        clip_id=clip_id,
        video_id=video_id,
        scenes=(scene,),
        start_time=0,
        end_time=45000,
        duration=45.0,
        average_score=0.57,
        clip_index=0,
    )


def _make_hook(
    clip_id: str = "abcd1234abcd1234",
    video_id: str = "a1b2c3d4e5f67890",
    hook_text: str = "INSANE 1V5 CLUTCH WIN",
    story_text: str = "Watch this incredible comeback in ranked play mode today.",
    keywords: tuple[str, ...] = ("clutch", "win", "ranked"),
) -> HookResult:
    return HookResult(
        clip_id=clip_id,
        video_id=video_id,
        hook_text=hook_text,
        story_text=story_text,
        template_id="action_result",
        keyword_source=keywords,
    )


def _make_transcript(with_words: bool = True) -> Transcript:
    if not with_words:
        return Transcript(
            video_id="a1b2c3d4e5f67890",
            segments=(),
            total_words=0,
            language="en",
        )
    words = tuple(
        Word(text=w, start_time=i * 500, end_time=(i + 1) * 500, confidence=0.95)
        for i, w in enumerate(["kill", "clutch", "win", "destroy", "attack", "boss"])
    )
    seg = TranscriptSegment(
        text=" ".join(w.text for w in words),
        start_time=0,
        end_time=3000,
        words=words,
        confidence=0.95,
    )
    return Transcript(
        video_id="a1b2c3d4e5f67890",
        segments=(seg,),
        total_words=len(words),
        language="en",
    )


def _sample_config(with_channel: bool = False) -> dict:
    cfg: dict = {
        "metadata": {
            "title_min_chars": 40,
            "title_max_chars": 60,
            "description_min_chars": 150,
            "description_max_chars": 300,
            "tag_count_min": 10,
            "tag_count_max": 15,
        },
        "channel": {
            "name": "GamingHighlights",
            "hashtags": ["GamingHighlights", "TopClips"] if with_channel else [],
            "static_tags": ["gaming", "clips"] if with_channel else [],
        },
    }
    return cfg


# ---------------------------------------------------------------------------
# Tests — _truncate_at_word
# ---------------------------------------------------------------------------

class TestTruncateAtWord:
    def test_short_text_unchanged(self):
        assert _truncate_at_word("hello world", 20) == "hello world"

    def test_truncates_at_word_boundary(self):
        result = _truncate_at_word("hello world foo bar", 11)
        assert result == "hello world"
        assert len(result) <= 11

    def test_truncates_at_hard_limit_if_no_space(self):
        result = _truncate_at_word("abcdefghij", 5)
        assert result == "abcde"

    def test_exact_max_length_unchanged(self):
        text = "hello"
        assert _truncate_at_word(text, 5) == text


# ---------------------------------------------------------------------------
# Tests — _build_title
# ---------------------------------------------------------------------------

class TestBuildTitle:
    def test_within_length_bounds(self):
        hook = _make_hook()
        config = _sample_config()
        title = _build_title(hook, config)
        assert 40 <= len(title) <= 60, f"Title length {len(title)}: {title!r}"

    def test_uses_hook_text(self):
        hook = _make_hook(hook_text="AMAZING SNIPER SHOT")
        config = _sample_config()
        title = _build_title(hook, config)
        assert "AMAZING SNIPER SHOT" in title

    def test_deterministic(self):
        hook = _make_hook()
        config = _sample_config()
        assert _build_title(hook, config) == _build_title(hook, config)

    def test_long_hook_truncated_to_max(self):
        # 65-char hook text
        long_hook = "A" * 65
        hook = _make_hook(hook_text=long_hook)
        config = _sample_config()
        title = _build_title(hook, config)
        assert len(title) <= 60

    def test_short_hook_padded_to_min(self):
        hook = _make_hook(
            hook_text="WIN",
            story_text="An incredible gaming moment from ranked competitive play mode here.",
        )
        config = _sample_config()
        title = _build_title(hook, config)
        assert len(title) >= 40, f"Title too short: {len(title)} — {title!r}"

    def test_max_chars_respected(self):
        hook = _make_hook(
            hook_text="SHORT",
            story_text=" ".join(["word"] * 20),
        )
        config = _sample_config()
        title = _build_title(hook, config)
        assert len(title) <= 60


# ---------------------------------------------------------------------------
# Tests — _build_description
# ---------------------------------------------------------------------------

class TestBuildDescription:
    def test_within_length_bounds(self):
        hook = _make_hook()
        transcript = _make_transcript()
        config = _sample_config()
        desc = _build_description(hook, transcript, config)
        assert 150 <= len(desc) <= 300, f"Description length {len(desc)}: {desc!r}"

    def test_contains_story_text(self):
        hook = _make_hook(story_text="Watch this incredible comeback.")
        transcript = _make_transcript(with_words=False)
        config = _sample_config()
        desc = _build_description(hook, transcript, config)
        assert "Watch this incredible comeback." in desc

    def test_deterministic(self):
        hook = _make_hook()
        transcript = _make_transcript()
        config = _sample_config()
        d1 = _build_description(hook, transcript, config)
        d2 = _build_description(hook, transcript, config)
        assert d1 == d2

    def test_max_chars_respected(self):
        hook = _make_hook(story_text="word " * 60)
        transcript = _make_transcript()
        config = _sample_config()
        desc = _build_description(hook, transcript, config)
        assert len(desc) <= 300

    def test_empty_transcript_handled(self):
        hook = _make_hook()
        transcript = _make_transcript(with_words=False)
        config = _sample_config()
        desc = _build_description(hook, transcript, config)
        assert len(desc) >= 150

    def test_channel_hashtags_included(self):
        hook = _make_hook()
        transcript = _make_transcript(with_words=False)
        config = _sample_config(with_channel=True)
        desc = _build_description(hook, transcript, config)
        assert "#GamingHighlights" in desc

    def test_standard_hashtags_present(self):
        hook = _make_hook()
        transcript = _make_transcript(with_words=False)
        config = _sample_config()
        desc = _build_description(hook, transcript, config)
        assert "#Shorts" in desc


# ---------------------------------------------------------------------------
# Tests — _build_tags
# ---------------------------------------------------------------------------

class TestBuildTags:
    def test_count_within_bounds(self):
        hook = _make_hook()
        transcript = _make_transcript()
        config = _sample_config()
        tags = _build_tags(hook, transcript, config)
        assert 10 <= len(tags) <= 15, f"Tag count {len(tags)}: {tags}"

    def test_sorted(self):
        hook = _make_hook()
        transcript = _make_transcript()
        config = _sample_config()
        tags = _build_tags(hook, transcript, config)
        assert tags == tuple(sorted(tags))

    def test_lowercase(self):
        hook = _make_hook(keywords=("CLUTCH", "WIN"))
        transcript = _make_transcript()
        config = _sample_config()
        tags = _build_tags(hook, transcript, config)
        for tag in tags:
            assert tag == tag.lower()

    def test_unique(self):
        hook = _make_hook()
        transcript = _make_transcript()
        config = _sample_config()
        tags = _build_tags(hook, transcript, config)
        assert len(tags) == len(set(tags))

    def test_includes_hook_keywords(self):
        hook = _make_hook(keywords=("clutch", "win", "ranked"))
        transcript = _make_transcript(with_words=False)
        config = _sample_config()
        tags = _build_tags(hook, transcript, config)
        assert "clutch" in tags
        assert "win" in tags

    def test_transcript_keywords_included(self):
        hook = _make_hook(keywords=())
        transcript = _make_transcript(with_words=True)  # contains "kill", "destroy", etc.
        config = _sample_config()
        tags = _build_tags(hook, transcript, config)
        # At least some engagement keywords should appear.
        engagement = {"kill", "destroy", "attack", "boss"}
        assert len(engagement & set(tags)) > 0

    def test_static_channel_tags_included(self):
        hook = _make_hook(keywords=())
        transcript = _make_transcript(with_words=False)
        config = _sample_config(with_channel=True)
        tags = _build_tags(hook, transcript, config)
        assert "gaming" in tags
        assert "clips" in tags

    def test_deterministic(self):
        hook = _make_hook()
        transcript = _make_transcript()
        config = _sample_config()
        assert _build_tags(hook, transcript, config) == _build_tags(hook, transcript, config)

    def test_empty_inputs_still_produces_min_tags(self):
        hook = _make_hook(keywords=())
        transcript = _make_transcript(with_words=False)
        config = _sample_config()
        tags = _build_tags(hook, transcript, config)
        assert len(tags) >= 10


# ---------------------------------------------------------------------------
# Tests — process
# ---------------------------------------------------------------------------

class TestProcess:
    def test_returns_metadata_result(self):
        hook = _make_hook()
        transcript = _make_transcript()
        clip = _make_clip()
        config = _sample_config()
        result = process(hook, transcript, clip, config)
        assert isinstance(result, MetadataResult)

    def test_clip_id_preserved(self):
        hook = _make_hook(clip_id="test1234test1234")
        transcript = _make_transcript()
        clip = _make_clip(clip_id="test1234test1234")
        config = _sample_config()
        result = process(hook, transcript, clip, config)
        assert result.clip_id == "test1234test1234"

    def test_video_id_preserved(self):
        hook = _make_hook(video_id="abcdef1234567890")
        transcript = _make_transcript()
        clip = _make_clip(video_id="abcdef1234567890")
        config = _sample_config()
        result = process(hook, transcript, clip, config)
        assert result.video_id == "abcdef1234567890"

    def test_title_within_bounds(self):
        hook = _make_hook()
        transcript = _make_transcript()
        clip = _make_clip()
        config = _sample_config()
        result = process(hook, transcript, clip, config)
        assert 40 <= len(result.title) <= 60

    def test_description_within_bounds(self):
        hook = _make_hook()
        transcript = _make_transcript()
        clip = _make_clip()
        config = _sample_config()
        result = process(hook, transcript, clip, config)
        assert 150 <= len(result.description) <= 300

    def test_tag_count_within_bounds(self):
        hook = _make_hook()
        transcript = _make_transcript()
        clip = _make_clip()
        config = _sample_config()
        result = process(hook, transcript, clip, config)
        assert 10 <= len(result.tags) <= 15

    def test_tags_sorted(self):
        hook = _make_hook()
        transcript = _make_transcript()
        clip = _make_clip()
        config = _sample_config()
        result = process(hook, transcript, clip, config)
        assert result.tags == tuple(sorted(result.tags))

    def test_deterministic(self):
        hook = _make_hook()
        transcript = _make_transcript()
        clip = _make_clip()
        config = _sample_config()
        r1 = process(hook, transcript, clip, config)
        r2 = process(hook, transcript, clip, config)
        assert r1 == r2

    def test_empty_transcript_does_not_crash(self):
        hook = _make_hook()
        transcript = _make_transcript(with_words=False)
        clip = _make_clip()
        config = _sample_config()
        result = process(hook, transcript, clip, config)
        assert isinstance(result, MetadataResult)

    def test_tags_are_tuple_of_strings(self):
        hook = _make_hook()
        transcript = _make_transcript()
        clip = _make_clip()
        config = _sample_config()
        result = process(hook, transcript, clip, config)
        assert isinstance(result.tags, tuple)
        for tag in result.tags:
            assert isinstance(tag, str)
