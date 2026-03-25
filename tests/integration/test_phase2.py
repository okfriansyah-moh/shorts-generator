"""Integration tests for Phase 2 — Transcription & Signal Extraction.

Tests the full signal extraction chain using mocked external dependencies
(faster-whisper, MediaPipe, FFmpeg). No GPU, no network, no real video files required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from contracts.audio import AudioEnergyData
from contracts.face import FaceBBox, FaceDetectionResult, SceneFaceData
from contracts.ingestion import IngestionResult
from contracts.scene import SceneList, SceneSegment
from contracts.transcript import Transcript, TranscriptSegment, Word


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def video_id() -> str:
    return "aabbccddeeff0011"


@pytest.fixture()
def mock_ingestion(video_id: str) -> IngestionResult:
    return IngestionResult(
        video_id=video_id,
        path="/fake/test_video.mp4",
        duration_seconds=3600.0,
        resolution=(1920, 1080),
        codec="h264",
        audio_codec="aac",
        has_audio=True,
        file_size_bytes=100_000_000,
        fps=30.0,
    )


@pytest.fixture()
def mock_scene_list(video_id: str) -> SceneList:
    scenes = tuple(
        SceneSegment(
            scene_id=f"{video_id}_{i * 10000}_{(i + 1) * 10000}",
            video_id=video_id,
            start_time=i * 10000,
            end_time=(i + 1) * 10000,
            duration=10.0,
        )
        for i in range(3)
    )
    return SceneList(
        video_id=video_id,
        scenes=scenes,
        total_duration=30.0,
    )


@pytest.fixture()
def pipeline_config() -> dict[str, Any]:
    return {
        "paths": {"temp_dir": "/tmp/phase2_integration"},
        "pipeline": {"ffmpeg_timeout": 30},
        "transcription": {"model_size": "small", "language": "en", "beam_size": 5},
        "face_detection": {"sample_fps": 2, "min_confidence": 0.7, "ema_alpha": 0.3},
    }


# ---------------------------------------------------------------------------
# Full signal extraction chain test
# ---------------------------------------------------------------------------

class TestPhase2FullChain:
    """Verify the three signal extraction modules produce correct DTO shapes."""

    @patch("modules.transcription.transcribe._extract_audio_to_wav", return_value="/tmp/fake.wav")
    @patch("modules.transcription.transcribe._cleanup_temp_file")
    @patch("modules.transcription.transcribe._run_faster_whisper")
    def test_transcription_produces_transcript_dto(
        self,
        mock_whisper: MagicMock,
        mock_cleanup: MagicMock,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        mock_scene_list: SceneList,
        pipeline_config: dict[str, Any],
    ) -> None:
        """Transcription returns a Transcript with word-level timestamps."""
        words = (
            Word("hello", 0, 400, 0.95),
            Word("world", 500, 900, 0.90),
            Word("gaming", 1000, 1400, 0.88),
        )
        seg = TranscriptSegment(
            text="hello world gaming",
            start_time=0,
            end_time=1400,
            words=words,
            confidence=0.91,
        )
        mock_whisper.return_value = Transcript(
            video_id=mock_ingestion.video_id,
            segments=(seg,),
            total_words=3,
            language="en",
        )

        from modules.transcription.transcribe import transcribe
        result = transcribe(mock_ingestion, pipeline_config)

        assert isinstance(result, Transcript)
        assert result.video_id == mock_ingestion.video_id
        assert result.total_words == 3
        assert result.language == "en"
        # Verify word-level timestamps exist
        assert len(result.segments) > 0
        seg_result = result.segments[0]
        assert len(seg_result.words) == 3
        assert seg_result.words[0].start_time == 0
        assert seg_result.words[0].end_time == 400

    @patch("modules.face_detection.detect._load_mediapipe_detector")
    @patch("modules.face_detection.detect._process_scene")
    def test_face_detection_produces_result_dto(
        self,
        mock_process: MagicMock,
        mock_load: MagicMock,
        mock_ingestion: IngestionResult,
        mock_scene_list: SceneList,
        pipeline_config: dict[str, Any],
    ) -> None:
        """Face detection returns FaceDetectionResult with one entry per scene."""
        mock_load.return_value = MagicMock()

        def make_scene_data(scene: SceneSegment) -> SceneFaceData:
            bbox = FaceBBox(x=0.2, y=0.1, width=0.3, height=0.4, confidence=0.85, timestamp_ms=scene.start_time)
            return SceneFaceData(
                scene_id=scene.scene_id,
                face_visible_ratio=0.8,
                bounding_boxes=(bbox,),
                average_bbox=bbox,
                sample_count=20,
            )

        mock_process.side_effect = [
            make_scene_data(s) for s in mock_scene_list.scenes
        ]

        from modules.face_detection.detect import detect_faces
        result = detect_faces(mock_ingestion, mock_scene_list, pipeline_config)

        assert isinstance(result, FaceDetectionResult)
        assert result.video_id == mock_ingestion.video_id
        assert len(result.scene_data) == len(mock_scene_list.scenes)
        assert result.average_visibility == pytest.approx(0.8)
        assert result.faceless_scene_count == 0
        # Verify bounding boxes have normalized coordinates
        for scene_data in result.scene_data:
            if scene_data.bounding_boxes:
                b = scene_data.bounding_boxes[0]
                assert 0.0 <= b.x <= 1.0
                assert 0.0 <= b.y <= 1.0
                assert 0.0 < b.width <= 1.0
                assert 0.0 < b.height <= 1.0

    @patch("modules.audio_analysis.analyze._extract_scene_rms")
    def test_audio_analysis_produces_energy_dto(
        self,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        mock_scene_list: SceneList,
        pipeline_config: dict[str, Any],
    ) -> None:
        """Audio analysis returns AudioEnergyData with normalized per-scene energies."""
        mock_extract.side_effect = [0.1, 0.3, 0.5]

        from modules.audio_analysis.analyze import analyze_audio
        result = analyze_audio(mock_ingestion, mock_scene_list, pipeline_config)

        assert isinstance(result, AudioEnergyData)
        assert result.video_id == mock_ingestion.video_id
        assert len(result.scene_energies) == 3
        # Check normalization: min=0.0, max=1.0
        assert result.scene_energies[0].normalized_energy == pytest.approx(0.0)
        assert result.scene_energies[2].normalized_energy == pytest.approx(1.0)
        assert 0.0 <= result.scene_energies[1].normalized_energy <= 1.0
        assert result.video_min_rms == pytest.approx(0.1)
        assert result.video_max_rms == pytest.approx(0.5)


class TestPhase2EmptySignals:
    """Verify signal extraction modules handle empty results gracefully."""

    @patch("modules.transcription.transcribe._extract_audio_to_wav", return_value="/tmp/fake.wav")
    @patch("modules.transcription.transcribe._cleanup_temp_file")
    @patch("modules.transcription.transcribe._run_faster_whisper")
    def test_no_speech_returns_empty_transcript(
        self,
        mock_whisper: MagicMock,
        mock_cleanup: MagicMock,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        pipeline_config: dict[str, Any],
    ) -> None:
        mock_whisper.return_value = Transcript(
            video_id=mock_ingestion.video_id,
            segments=(),
            total_words=0,
            language="en",
        )
        from modules.transcription.transcribe import transcribe
        result = transcribe(mock_ingestion, pipeline_config)
        assert result.total_words == 0
        assert len(result.segments) == 0

    @patch("modules.face_detection.detect._load_mediapipe_detector")
    @patch("modules.face_detection.detect._process_scene")
    def test_no_faces_returns_zero_visibility(
        self,
        mock_process: MagicMock,
        mock_load: MagicMock,
        mock_ingestion: IngestionResult,
        mock_scene_list: SceneList,
        pipeline_config: dict[str, Any],
    ) -> None:
        mock_load.return_value = MagicMock()
        mock_process.side_effect = [
            SceneFaceData(
                scene_id=scene.scene_id,
                face_visible_ratio=0.0,
                bounding_boxes=(),
                average_bbox=None,
                sample_count=20,
            )
            for scene in mock_scene_list.scenes
        ]
        from modules.face_detection.detect import detect_faces
        result = detect_faces(mock_ingestion, mock_scene_list, pipeline_config)
        assert result.average_visibility == 0.0
        assert result.faceless_scene_count == len(mock_scene_list.scenes)

    @patch("modules.audio_analysis.analyze._extract_scene_rms")
    def test_flat_audio_returns_zero_normalized(
        self,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        mock_scene_list: SceneList,
        pipeline_config: dict[str, Any],
    ) -> None:
        mock_extract.side_effect = [0.5, 0.5, 0.5]
        from modules.audio_analysis.analyze import analyze_audio
        result = analyze_audio(mock_ingestion, mock_scene_list, pipeline_config)
        assert all(e.normalized_energy == 0.0 for e in result.scene_energies)
