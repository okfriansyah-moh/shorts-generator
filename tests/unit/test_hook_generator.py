"""Unit tests for the hook_generator module."""

from __future__ import annotations

from contracts.clip import ClipDefinition
from contracts.hook import HookResult
from contracts.scoring import ScoredScene
from contracts.transcript import Transcript, TranscriptSegment, Word
from modules.hook_generator import process
from modules.hook_generator.templates import (
    FALLBACK_TEMPLATES,
    HOOK_TEMPLATES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_scored_scene(
    video_id: str = "a1b2c3d4e5f67890",
    start: int = 0,
    end: int = 5000,
) -> ScoredScene:
    return ScoredScene(
        scene_id=f"{video_id}_{start}_{end}",
        video_id=video_id,
        start_time=start,
        end_time=end,
        duration=(end - start) / 1000.0,
        keyword_score=0.5,
        audio_energy_score=0.6,
        face_presence_score=0.7,
        scene_activity_score=0.5,
        sentence_density_score=0.4,
        composite_score=0.55,
        rank=1,
    )


def _make_clip(
    clip_id: str = "abcd1234abcd1234",
    video_id: str = "a1b2c3d4e5f67890",
    start: int = 0,
    end: int = 45000,
    clip_index: int = 0,
) -> ClipDefinition:
    scenes = tuple(
        _make_scored_scene(video_id, s, s + 5000)
        for s in range(start, end, 5000)
    )
    return ClipDefinition(
        clip_id=clip_id,
        video_id=video_id,
        scenes=scenes,
        start_time=start,
        end_time=end,
        duration=(end - start) / 1000.0,
        average_score=0.55,
        clip_index=clip_index,
    )


def _make_transcript(
    video_id: str = "a1b2c3d4e5f67890",
    words: list[tuple[str, int, int]] | None = None,
) -> Transcript:
    if words is None:
        words = [
            ("kill", 1000, 1200),
            ("shot", 2000, 2200),
            ("the", 3000, 3100),
            ("enemy", 4000, 4300),
            ("with", 5000, 5100),
            ("a", 6000, 6050),
            ("headshot", 7000, 7400),
        ]
    word_objs = tuple(
        Word(text=w, start_time=s, end_time=e, confidence=0.95)
        for w, s, e in words
    )
    segment = TranscriptSegment(
        text=" ".join(w[0] for w in words),
        start_time=words[0][1] if words else 0,
        end_time=words[-1][2] if words else 0,
        words=word_objs,
        confidence=0.95,
    )
    return Transcript(
        video_id=video_id,
        segments=(segment,),
        total_words=len(words),
        language="en",
    )


def _make_empty_transcript(video_id: str = "a1b2c3d4e5f67890") -> Transcript:
    return Transcript(
        video_id=video_id,
        segments=(),
        total_words=0,
        language="en",
    )


def _make_config() -> dict:
    return {
        "hook_generator": {
            "max_hook_words": 15,
            "max_story_words": 40,
            "templates_per_style": 5,
        }
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHookGeneration:
    """Test hook generation with various inputs."""

    def test_returns_hook_result(self) -> None:
        clip = _make_clip()
        transcript = _make_transcript()
        result, used = process(clip, transcript, _make_config())

        assert isinstance(result, HookResult)
        assert result.clip_id == clip.clip_id
        assert result.video_id == clip.video_id
        assert isinstance(used, frozenset)

    def test_hook_within_word_limit(self) -> None:
        clip = _make_clip()
        transcript = _make_transcript()
        result, _ = process(clip, transcript, _make_config())

        hook_words = len(result.hook_text.split())
        story_words = len(result.story_text.split())
        assert hook_words <= 15
        assert story_words <= 40

    def test_non_empty_texts(self) -> None:
        clip = _make_clip()
        transcript = _make_transcript()
        result, _ = process(clip, transcript, _make_config())

        assert len(result.hook_text) > 0
        assert len(result.story_text) > 0
        assert len(result.template_id) > 0

    def test_keywords_extracted(self) -> None:
        clip = _make_clip()
        transcript = _make_transcript()
        result, _ = process(clip, transcript, _make_config())

        # "kill", "shot", "headshot", "enemy" are engagement keywords
        assert len(result.keyword_source) > 0

    def test_empty_transcript_uses_fallback(self) -> None:
        clip = _make_clip()
        transcript = _make_empty_transcript()
        result, _ = process(clip, transcript, _make_config())

        assert isinstance(result, HookResult)
        assert result.template_id.startswith("fallback_")
        assert len(result.hook_text) > 0

    def test_deterministic_output(self) -> None:
        clip = _make_clip()
        transcript = _make_transcript()
        config = _make_config()

        result1, _ = process(clip, transcript, config)
        result2, _ = process(clip, transcript, config)

        assert result1.hook_text == result2.hook_text
        assert result1.story_text == result2.story_text
        assert result1.template_id == result2.template_id

    def test_different_clips_get_different_templates(self) -> None:
        transcript = _make_transcript()
        config = _make_config()

        results = []
        for i in range(5):
            clip = _make_clip(clip_id=f"clip{i:015d}a", clip_index=i)
            result, _ = process(clip, transcript, config)
            results.append(result.template_id)

        # At least some templates should differ across clips
        assert len(set(results)) > 1


class TestBatchTemplateDedup:
    """Test that templates are not reused within a batch."""

    def test_no_template_reuse_in_batch(self) -> None:
        transcript = _make_transcript()
        config = _make_config()
        used: frozenset[int] = frozenset()

        template_ids = []
        for i in range(min(10, len(HOOK_TEMPLATES))):
            clip = _make_clip(clip_id=f"batchclip{i:06d}a", clip_index=i)
            result, used = process(clip, transcript, config, used_template_ids=used)
            template_ids.append(result.template_id)

        # All templates should be unique within the batch
        assert len(template_ids) == len(set(template_ids))

    def test_pool_exhaustion_resets(self) -> None:
        """When all templates are used, pool resets and continues."""
        transcript = _make_transcript()
        config = _make_config()
        used: frozenset[int] = frozenset()

        # Generate more hooks than templates available
        total = len(HOOK_TEMPLATES) + 3
        results = []
        for i in range(total):
            clip = _make_clip(clip_id=f"exhaust{i:09d}ab", clip_index=i)
            result, used = process(clip, transcript, config, used_template_ids=used)
            results.append(result)

        # Should not crash and all results should be valid
        assert len(results) == total
        for r in results:
            assert len(r.hook_text) > 0


class TestTemplatePool:
    """Validate template pool integrity."""

    def test_minimum_template_count(self) -> None:
        assert len(HOOK_TEMPLATES) >= 30

    def test_fallback_templates_exist(self) -> None:
        assert len(FALLBACK_TEMPLATES) >= 3

    def test_templates_are_tuples_of_pairs(self) -> None:
        for hook, story in HOOK_TEMPLATES:
            assert isinstance(hook, str)
            assert isinstance(story, str)
            assert len(hook) > 0
            assert len(story) > 0

    def test_no_duplicate_hook_templates(self) -> None:
        hooks = [h for h, _ in HOOK_TEMPLATES]
        assert len(hooks) == len(set(hooks))
