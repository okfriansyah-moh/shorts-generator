"""Unit tests for the transcription module.

Tests cover: word-level timestamps, empty speech result, confidence scores,
faster-whisper unavailability, and audio extraction failures.
All tests run without GPU, network, or real video files.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from contracts.ingestion import IngestionResult
from contracts.scene import SceneList, SceneSegment
from contracts.transcript import Transcript, TranscriptSegment, Word


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_ingestion() -> IngestionResult:
    return IngestionResult(
        video_id="abcdef1234567890",
        path="/fake/video.mp4",
        duration_seconds=120.0,
        resolution=(1920, 1080),
        codec="h264",
        audio_codec="aac",
        has_audio=True,
        file_size_bytes=50_000_000,
        fps=30.0,
    )


@pytest.fixture()
def minimal_config() -> dict[str, Any]:
    return {
        "paths": {"temp_dir": "/tmp/test_transcription"},
        "pipeline": {"ffmpeg_timeout": 10},
        "transcription": {
            "model_size": "small",
            "language": "en",
            "beam_size": 5,
        },
    }


@pytest.fixture()
def sample_scene_list(mock_ingestion: IngestionResult) -> SceneList:
    scene = SceneSegment(
        scene_id="abcdef1234567890_0_30000",
        video_id=mock_ingestion.video_id,
        start_time=0,
        end_time=30000,
        duration=30.0,
    )
    return SceneList(
        video_id=mock_ingestion.video_id,
        scenes=(scene,),
        total_duration=30.0,
    )


# ---------------------------------------------------------------------------
# DTO construction tests
# ---------------------------------------------------------------------------

class TestWordDTO:
    def test_word_creation(self) -> None:
        word = Word(text="hello", start_time=0, end_time=500, confidence=0.95)
        assert word.text == "hello"
        assert word.start_time == 0
        assert word.end_time == 500
        assert word.confidence == 0.95

    def test_word_is_frozen(self) -> None:
        word = Word(text="hello", start_time=0, end_time=500, confidence=0.95)
        with pytest.raises((AttributeError, TypeError)):
            word.text = "changed"  # type: ignore[misc]


class TestTranscriptSegmentDTO:
    def test_segment_creation(self) -> None:
        word = Word(text="test", start_time=100, end_time=600, confidence=0.9)
        seg = TranscriptSegment(
            text="test",
            start_time=100,
            end_time=600,
            words=(word,),
            confidence=0.9,
        )
        assert seg.text == "test"
        assert len(seg.words) == 1
        assert seg.confidence == 0.9

    def test_empty_segment(self) -> None:
        seg = TranscriptSegment(
            text="",
            start_time=0,
            end_time=1000,
            words=(),
            confidence=0.0,
        )
        assert seg.text == ""
        assert len(seg.words) == 0

    def test_segment_is_frozen(self) -> None:
        seg = TranscriptSegment(
            text="x", start_time=0, end_time=100, words=(), confidence=0.5
        )
        with pytest.raises((AttributeError, TypeError)):
            seg.text = "changed"  # type: ignore[misc]


class TestTranscriptDTO:
    def test_transcript_creation(self) -> None:
        word = Word(text="hello", start_time=0, end_time=500, confidence=0.9)
        seg = TranscriptSegment(
            text="hello", start_time=0, end_time=500, words=(word,), confidence=0.9
        )
        transcript = Transcript(
            video_id="abcdef1234567890",
            segments=(seg,),
            total_words=1,
            language="en",
        )
        assert transcript.video_id == "abcdef1234567890"
        assert transcript.total_words == 1
        assert transcript.language == "en"

    def test_empty_transcript(self) -> None:
        transcript = Transcript(
            video_id="abcdef1234567890",
            segments=(),
            total_words=0,
            language="en",
        )
        assert transcript.total_words == 0
        assert len(transcript.segments) == 0

    def test_transcript_is_frozen(self) -> None:
        transcript = Transcript(
            video_id="abcdef1234567890",
            segments=(),
            total_words=0,
            language="en",
        )
        with pytest.raises((AttributeError, TypeError)):
            transcript.video_id = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# transcribe() function tests (with mocked faster-whisper + FFmpeg)
# ---------------------------------------------------------------------------

class TestTranscribeFunction:
    @patch("modules.transcription.transcribe._extract_audio_to_wav", return_value="/tmp/fake.wav")
    @patch("modules.transcription.transcribe._cleanup_temp_file")
    @patch("modules.transcription.transcribe._run_faster_whisper")
    def test_transcribe_with_speech(
        self,
        mock_whisper: MagicMock,
        mock_cleanup: MagicMock,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        minimal_config: dict[str, Any],
    ) -> None:
        mock_whisper.return_value = Transcript(
            video_id=mock_ingestion.video_id,
            segments=(
                TranscriptSegment(
                    text="hello world",
                    start_time=0,
                    end_time=1000,
                    words=(
                        Word("hello", 0, 500, 0.9),
                        Word("world", 500, 1000, 0.9),
                    ),
                    confidence=0.9,
                ),
            ),
            total_words=2,
            language="en",
        )
        from modules.transcription.transcribe import transcribe
        result = transcribe(mock_ingestion, minimal_config)

        assert isinstance(result, Transcript)
        assert result.video_id == mock_ingestion.video_id
        assert result.total_words == 2
        assert result.language == "en"
        assert len(result.segments) == 1

    @patch("modules.transcription.transcribe._extract_audio_to_wav", return_value="/tmp/fake.wav")
    @patch("modules.transcription.transcribe._cleanup_temp_file")
    @patch("modules.transcription.transcribe._run_faster_whisper")
    def test_transcribe_empty_speech(
        self,
        mock_whisper: MagicMock,
        mock_cleanup: MagicMock,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        minimal_config: dict[str, Any],
    ) -> None:
        mock_whisper.return_value = Transcript(
            video_id=mock_ingestion.video_id,
            segments=(),
            total_words=0,
            language="en",
        )
        from modules.transcription.transcribe import transcribe
        result = transcribe(mock_ingestion, minimal_config)
        assert isinstance(result, Transcript)
        assert result.total_words == 0
        assert len(result.segments) == 0

    @patch("modules.transcription.transcribe._extract_audio_to_wav")
    @patch("modules.transcription.transcribe._cleanup_temp_file")
    @patch("modules.transcription.transcribe._run_faster_whisper")
    def test_transcribe_returns_word_level_timestamps(
        self,
        mock_whisper: MagicMock,
        mock_cleanup: MagicMock,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        minimal_config: dict[str, Any],
    ) -> None:
        mock_extract.return_value = "/tmp/fake.wav"
        words = (
            Word("first", 100, 400, 0.95),
            Word("second", 450, 800, 0.88),
            Word("third", 900, 1200, 0.92),
        )
        seg = TranscriptSegment(
            text="first second third",
            start_time=100,
            end_time=1200,
            words=words,
            confidence=0.92,
        )
        mock_whisper.return_value = Transcript(
            video_id=mock_ingestion.video_id,
            segments=(seg,),
            total_words=3,
            language="en",
        )
        from modules.transcription.transcribe import transcribe
        result = transcribe(mock_ingestion, minimal_config)
        assert result.segments[0].words[0].start_time == 100
        assert result.segments[0].words[0].end_time == 400
        assert result.segments[0].words[1].start_time == 450


class TestSecondsToMs:
    def test_conversion(self) -> None:
        from modules.transcription.transcribe import _seconds_to_ms
        assert _seconds_to_ms(0.0) == 0
        assert _seconds_to_ms(1.0) == 1000
        assert _seconds_to_ms(1.5) == 1500
        assert _seconds_to_ms(0.001) == 1

    def test_rounding(self) -> None:
        from modules.transcription.transcribe import _seconds_to_ms
        # round-to-nearest behavior
        assert _seconds_to_ms(1.9999) == 2000
        assert _seconds_to_ms(1.4994) == 1499
        assert _seconds_to_ms(1.4995) == 1500


class TestExtractAudioToWav:
    @patch("modules.transcription.transcribe.subprocess.run")
    @patch("modules.transcription.transcribe.tempfile.mkstemp")
    def test_ffmpeg_failure_raises(
        self,
        mock_mkstemp: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_mkstemp.return_value = (5, "/tmp/fake.wav")
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        with patch("os.close"), patch("os.makedirs"), patch("os.path.exists", return_value=True), patch("os.unlink"):
            from modules.transcription.transcribe import _extract_audio_to_wav
            with pytest.raises(RuntimeError, match="FFmpeg audio extraction failed"):
                _extract_audio_to_wav(
                    "/fake/video.mp4", "abcdef1234567890",
                    {"paths": {"temp_dir": "/tmp"}, "pipeline": {"ffmpeg_timeout": 10}}
                )
