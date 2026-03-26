"""Unit tests for the subtitle module."""

from __future__ import annotations

import os

import pytest

from contracts.clip import ClipDefinition
from contracts.scoring import ScoredScene
from contracts.subtitle import SubtitleResult
from contracts.transcript import Transcript, TranscriptSegment, Word
from contracts.tts import TTSResult, TTSWordTiming
from modules.subtitle import process
from modules.subtitle.generate import (
    _chunk_words,
    _extract_clip_words,
    _format_ass_time,
    _generate_ass_header,
    _generate_narration_subs,
    _generate_transcript_karaoke,
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
        clip_index=0,
    )


def _make_words(count: int = 20, start_offset: int = 0) -> list[Word]:
    return [
        Word(
            text=f"word{i}",
            start_time=start_offset + i * 300,
            end_time=start_offset + i * 300 + 250,
            confidence=0.95,
        )
        for i in range(count)
    ]


def _make_transcript(
    video_id: str = "a1b2c3d4e5f67890",
    word_count: int = 20,
) -> Transcript:
    words = _make_words(word_count)
    if not words:
        return Transcript(
            video_id=video_id, segments=(), total_words=0, language="en",
        )
    segment = TranscriptSegment(
        text=" ".join(w.text for w in words),
        start_time=words[0].start_time,
        end_time=words[-1].end_time,
        words=tuple(words),
        confidence=0.95,
    )
    return Transcript(
        video_id=video_id,
        segments=(segment,),
        total_words=word_count,
        language="en",
    )


def _make_tts_result(
    clip_id: str = "abcd1234abcd1234",
    word_count: int = 5,
) -> TTSResult:
    timings = tuple(
        TTSWordTiming(
            text=f"narr{i}",
            start_ms=i * 400,
            end_ms=i * 400 + 350,
        )
        for i in range(word_count)
    )
    return TTSResult(
        clip_id=clip_id,
        audio_path="/tmp/fake_narration.wav",
        duration_seconds=word_count * 0.4,
        sample_rate=44100,
        word_timings=timings,
        engine_used="edge-tts",
    )


def _make_config() -> dict:
    return {
        "subtitle": {
            "font_size": 48,
            "font_name": "Arial",
            "outline_width": 3,
            "margin_bottom": 150,
        }
    }


# ---------------------------------------------------------------------------
# Tests: ASS time formatting
# ---------------------------------------------------------------------------

class TestFormatAssTime:

    def test_zero(self) -> None:
        assert _format_ass_time(0) == "0:00:00.00"

    def test_simple_seconds(self) -> None:
        assert _format_ass_time(5500) == "0:00:05.50"

    def test_minutes(self) -> None:
        assert _format_ass_time(90000) == "0:01:30.00"

    def test_negative_clamps_to_zero(self) -> None:
        assert _format_ass_time(-500) == "0:00:00.00"

    def test_centiseconds(self) -> None:
        assert _format_ass_time(1230) == "0:00:01.23"


# ---------------------------------------------------------------------------
# Tests: Word chunking
# ---------------------------------------------------------------------------

class TestChunkWords:

    def test_small_list(self) -> None:
        words = _make_words(3)
        chunks = _chunk_words(words, max_per_line=7)
        assert len(chunks) == 1
        assert len(chunks[0]) == 3

    def test_splits_at_max(self) -> None:
        words = _make_words(15)
        chunks = _chunk_words(words, max_per_line=7)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 7

    def test_empty_list(self) -> None:
        chunks = _chunk_words([], max_per_line=7)
        assert chunks == []

    def test_sentence_boundary_break(self) -> None:
        words = [
            Word(text="hello", start_time=0, end_time=200, confidence=0.9),
            Word(text="world.", start_time=200, end_time=400, confidence=0.9),
            Word(text="next", start_time=500, end_time=700, confidence=0.9),
        ]
        chunks = _chunk_words(words, max_per_line=7)
        assert len(chunks) == 2
        assert chunks[0][-1].text == "world."


# ---------------------------------------------------------------------------
# Tests: Karaoke subtitle generation
# ---------------------------------------------------------------------------

class TestKaraokeGeneration:

    def test_generates_events(self) -> None:
        words = _make_words(10)
        events = _generate_transcript_karaoke(words, clip_start_ms=0)
        assert len(events) > 0
        for event in events:
            assert event.startswith("Dialogue:")
            assert ",Transcript," in event

    def test_karaoke_tags_present(self) -> None:
        words = _make_words(5)
        events = _generate_transcript_karaoke(words, clip_start_ms=0)
        assert any("\\kf" in e for e in events)

    def test_clip_offset_applied(self) -> None:
        words = [
            Word(text="hello", start_time=10000, end_time=10500, confidence=0.9),
        ]
        events = _generate_transcript_karaoke(words, clip_start_ms=10000)
        # Start time should be 0 (10000 - 10000)
        assert "0:00:00.00" in events[0]


# ---------------------------------------------------------------------------
# Tests: Narration subtitles
# ---------------------------------------------------------------------------

class TestNarrationSubs:

    def test_generates_events(self) -> None:
        tts_words = [
            TTSWordTiming(text="hello", start_ms=0, end_ms=300),
            TTSWordTiming(text="world", start_ms=300, end_ms=600),
        ]
        events = _generate_narration_subs(tts_words)
        assert len(events) == 1
        assert ",Narration," in events[0]

    def test_empty_words(self) -> None:
        events = _generate_narration_subs([])
        assert events == []


# ---------------------------------------------------------------------------
# Tests: Extract clip words
# ---------------------------------------------------------------------------

class TestExtractClipWords:

    def test_filters_by_range(self) -> None:
        transcript = _make_transcript(word_count=20)
        # Words at 0..5750ms range; clip from 1000 to 3000
        words = _extract_clip_words(transcript, 1000, 3000)
        for w in words:
            assert w.start_time >= 1000
            assert w.start_time < 3000

    def test_empty_transcript(self) -> None:
        transcript = _make_transcript(word_count=0)
        words = _extract_clip_words(transcript, 0, 45000)
        assert words == []


# ---------------------------------------------------------------------------
# Tests: ASS header
# ---------------------------------------------------------------------------

class TestAssHeader:

    def test_contains_script_info(self) -> None:
        header = _generate_ass_header(_make_config())
        assert "[Script Info]" in header
        assert "PlayResX: 1080" in header
        assert "PlayResY: 1920" in header

    def test_contains_styles(self) -> None:
        header = _generate_ass_header(_make_config())
        assert "Style: Transcript" in header
        assert "Style: Narration" in header

    def test_contains_events_section(self) -> None:
        header = _generate_ass_header(_make_config())
        assert "[Events]" in header

    def test_config_values_applied(self) -> None:
        config = _make_config()
        config["subtitle"]["font_size"] = 60
        config["subtitle"]["margin_bottom"] = 200
        header = _generate_ass_header(config)
        assert ",60," in header
        assert ",200," in header


# ---------------------------------------------------------------------------
# Tests: Full process
# ---------------------------------------------------------------------------

class TestSubtitleProcess:

    def test_generates_ass_file(self, tmp_path) -> None:
        clip = _make_clip()
        transcript = _make_transcript(word_count=10)
        tts = _make_tts_result(word_count=5)
        config = _make_config()

        result = process(clip, transcript, tts, config, str(tmp_path))

        assert isinstance(result, SubtitleResult)
        assert result.clip_id == clip.clip_id
        assert os.path.exists(result.ass_path)
        assert result.has_transcript_subs is True
        assert result.has_narration_subs is True
        assert result.subtitle_count > 0

    def test_ass_file_content(self, tmp_path) -> None:
        clip = _make_clip()
        transcript = _make_transcript(word_count=10)
        tts = _make_tts_result(word_count=3)
        config = _make_config()

        result = process(clip, transcript, tts, config, str(tmp_path))

        with open(result.ass_path, "r") as f:
            content = f.read()

        assert "[Script Info]" in content
        assert "Dialogue:" in content

    def test_no_transcript_no_tts(self, tmp_path) -> None:
        clip = _make_clip()
        transcript = _make_transcript(word_count=0)
        tts = TTSResult(
            clip_id=clip.clip_id,
            audio_path="/tmp/fake.wav",
            duration_seconds=3.0,
            sample_rate=44100,
            word_timings=(),
            engine_used="edge-tts",
        )
        config = _make_config()

        result = process(clip, transcript, tts, config, str(tmp_path))

        assert result.has_transcript_subs is False
        assert result.has_narration_subs is False
        assert result.subtitle_count == 0

    def test_deterministic_output(self, tmp_path) -> None:
        clip = _make_clip()
        transcript = _make_transcript(word_count=10)
        tts = _make_tts_result(word_count=5)
        config = _make_config()

        # Generate twice
        dir1 = tmp_path / "run1"
        dir2 = tmp_path / "run2"
        r1 = process(clip, transcript, tts, config, str(dir1))
        r2 = process(clip, transcript, tts, config, str(dir2))

        with open(r1.ass_path) as f1, open(r2.ass_path) as f2:
            assert f1.read() == f2.read()


class TestSubtitleResultDTO:

    def test_frozen(self) -> None:
        result = SubtitleResult(
            clip_id="test", ass_path="/tmp/test.ass",
            has_transcript_subs=True, has_narration_subs=False,
            subtitle_count=5,
        )
        with pytest.raises(AttributeError):
            result.clip_id = "changed"  # type: ignore[misc]
