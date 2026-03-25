"""Unit tests for the audio analysis module.

Tests cover: varying energy, flat energy, normalization range,
RMS parsing, FFmpeg failure handling, and AudioEnergyData construction.
All tests run without GPU, network, or real video files.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from contracts.audio import AudioEnergyData, SceneAudioEnergy
from contracts.ingestion import IngestionResult
from contracts.scene import SceneList, SceneSegment


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
def no_audio_ingestion() -> IngestionResult:
    return IngestionResult(
        video_id="abcdef1234567890",
        path="/fake/video.mp4",
        duration_seconds=120.0,
        resolution=(1920, 1080),
        codec="h264",
        audio_codec="",
        has_audio=False,
        file_size_bytes=50_000_000,
        fps=30.0,
    )


@pytest.fixture()
def minimal_config() -> dict[str, Any]:
    return {
        "paths": {"temp_dir": "/tmp/test_audio"},
        "pipeline": {"ffmpeg_timeout": 10},
    }


def _make_scene(scene_id: str, video_id: str, start_ms: int, end_ms: int) -> SceneSegment:
    return SceneSegment(
        scene_id=scene_id,
        video_id=video_id,
        start_time=start_ms,
        end_time=end_ms,
        duration=(end_ms - start_ms) / 1000.0,
    )


def _make_scene_list(
    video_id: str, scenes: tuple[SceneSegment, ...]
) -> SceneList:
    total = sum(s.duration for s in scenes)
    return SceneList(video_id=video_id, scenes=scenes, total_duration=total)


# ---------------------------------------------------------------------------
# DTO construction tests
# ---------------------------------------------------------------------------

class TestSceneAudioEnergyDTO:
    def test_creation(self) -> None:
        e = SceneAudioEnergy(scene_id="s1", rms_energy=0.5, normalized_energy=0.7)
        assert e.scene_id == "s1"
        assert e.rms_energy == 0.5
        assert e.normalized_energy == 0.7

    def test_is_frozen(self) -> None:
        e = SceneAudioEnergy(scene_id="s1", rms_energy=0.5, normalized_energy=0.7)
        with pytest.raises((AttributeError, TypeError)):
            e.rms_energy = 1.0  # type: ignore[misc]


class TestAudioEnergyDataDTO:
    def test_creation(self) -> None:
        e = SceneAudioEnergy(scene_id="s1", rms_energy=0.5, normalized_energy=1.0)
        data = AudioEnergyData(
            video_id="abcdef1234567890",
            scene_energies=(e,),
            video_min_rms=0.5,
            video_max_rms=0.5,
            video_mean_rms=0.5,
        )
        assert data.video_id == "abcdef1234567890"
        assert len(data.scene_energies) == 1

    def test_is_frozen(self) -> None:
        data = AudioEnergyData(
            video_id="x",
            scene_energies=(),
            video_min_rms=0.0,
            video_max_rms=0.0,
            video_mean_rms=0.0,
        )
        with pytest.raises((AttributeError, TypeError)):
            data.video_id = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# analyze_audio() tests (mocked FFmpeg)
# ---------------------------------------------------------------------------

class TestAnalyzeAudio:
    @patch("modules.audio_analysis.analyze._extract_scene_rms")
    def test_varying_energy_normalization(
        self,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        minimal_config: dict[str, Any],
    ) -> None:
        """Three scenes with different RMS values; normalization should give [0, 0.5, 1.0]."""
        video_id = mock_ingestion.video_id
        scenes = (
            _make_scene(f"{video_id}_0_10000", video_id, 0, 10000),
            _make_scene(f"{video_id}_10000_20000", video_id, 10000, 20000),
            _make_scene(f"{video_id}_20000_30000", video_id, 20000, 30000),
        )
        scene_list = _make_scene_list(video_id, scenes)
        mock_extract.side_effect = [0.1, 0.2, 0.3]

        from modules.audio_analysis.analyze import analyze_audio
        result = analyze_audio(mock_ingestion, scene_list, minimal_config)

        assert isinstance(result, AudioEnergyData)
        assert result.video_min_rms == pytest.approx(0.1)
        assert result.video_max_rms == pytest.approx(0.3)
        assert result.video_mean_rms == pytest.approx(0.2)
        assert len(result.scene_energies) == 3
        assert result.scene_energies[0].normalized_energy == pytest.approx(0.0)
        assert result.scene_energies[1].normalized_energy == pytest.approx(0.5)
        assert result.scene_energies[2].normalized_energy == pytest.approx(1.0)

    @patch("modules.audio_analysis.analyze._extract_scene_rms")
    def test_flat_energy_all_zero(
        self,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        minimal_config: dict[str, Any],
    ) -> None:
        """All scenes same RMS → normalized_energy = 0.0 for all."""
        video_id = mock_ingestion.video_id
        scenes = (
            _make_scene(f"{video_id}_0_10000", video_id, 0, 10000),
            _make_scene(f"{video_id}_10000_20000", video_id, 10000, 20000),
        )
        scene_list = _make_scene_list(video_id, scenes)
        mock_extract.side_effect = [0.5, 0.5]

        from modules.audio_analysis.analyze import analyze_audio
        result = analyze_audio(mock_ingestion, scene_list, minimal_config)

        assert all(e.normalized_energy == pytest.approx(0.0) for e in result.scene_energies)

    @patch("modules.audio_analysis.analyze._extract_scene_rms")
    def test_normalized_energy_in_range(
        self,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        minimal_config: dict[str, Any],
    ) -> None:
        """All normalized_energy values should be in [0.0, 1.0]."""
        video_id = mock_ingestion.video_id
        rms_values = [0.01, 0.15, 0.3, 0.45, 0.6]
        scenes = tuple(
            _make_scene(f"{video_id}_{i * 10000}_{(i + 1) * 10000}", video_id, i * 10000, (i + 1) * 10000)
            for i in range(len(rms_values))
        )
        scene_list = _make_scene_list(video_id, scenes)
        mock_extract.side_effect = rms_values

        from modules.audio_analysis.analyze import analyze_audio
        result = analyze_audio(mock_ingestion, scene_list, minimal_config)

        for e in result.scene_energies:
            assert 0.0 <= e.normalized_energy <= 1.0

    def test_no_audio_raises(
        self,
        no_audio_ingestion: IngestionResult,
        minimal_config: dict[str, Any],
    ) -> None:
        video_id = no_audio_ingestion.video_id
        scene = _make_scene(f"{video_id}_0_10000", video_id, 0, 10000)
        scene_list = _make_scene_list(video_id, (scene,))

        from modules.audio_analysis.analyze import analyze_audio
        with pytest.raises(RuntimeError, match="no audio stream"):
            analyze_audio(no_audio_ingestion, scene_list, minimal_config)

    @patch("modules.audio_analysis.analyze._extract_scene_rms")
    def test_scene_order_preserved(
        self,
        mock_extract: MagicMock,
        mock_ingestion: IngestionResult,
        minimal_config: dict[str, Any],
    ) -> None:
        """scene_energies must be in same order as scene_list.scenes."""
        video_id = mock_ingestion.video_id
        scenes = (
            _make_scene(f"{video_id}_0_5000", video_id, 0, 5000),
            _make_scene(f"{video_id}_5000_10000", video_id, 5000, 10000),
            _make_scene(f"{video_id}_10000_15000", video_id, 10000, 15000),
        )
        scene_list = _make_scene_list(video_id, scenes)
        mock_extract.side_effect = [0.2, 0.4, 0.6]

        from modules.audio_analysis.analyze import analyze_audio
        result = analyze_audio(mock_ingestion, scene_list, minimal_config)

        for i, scene in enumerate(scenes):
            assert result.scene_energies[i].scene_id == scene.scene_id


# ---------------------------------------------------------------------------
# RMS parsing tests
# ---------------------------------------------------------------------------

class TestParseRmsFromOutput:
    def test_parses_metadata_format(self) -> None:
        from modules.audio_analysis.analyze import _parse_rms_from_output
        output = "lavfi.astats.Overall.RMS_level=-20.000000\n"
        result = _parse_rms_from_output(output, "test_scene")
        # -20 dB → linear = 10^(-20/20) = 10^(-1) = 0.1
        assert result == pytest.approx(0.1, rel=1e-4)

    def test_parses_minus_inf(self) -> None:
        from modules.audio_analysis.analyze import _parse_rms_from_output
        output = "lavfi.astats.Overall.RMS_level=-inf\n"
        result = _parse_rms_from_output(output, "test_scene")
        assert result == 0.0

    def test_returns_zero_if_no_match(self) -> None:
        from modules.audio_analysis.analyze import _parse_rms_from_output
        result = _parse_rms_from_output("no useful output here", "test_scene")
        assert result == 0.0

    def test_zero_db_returns_one(self) -> None:
        from modules.audio_analysis.analyze import _parse_rms_from_output
        output = "lavfi.astats.Overall.RMS_level=0.0\n"
        result = _parse_rms_from_output(output, "test_scene")
        assert result == pytest.approx(1.0, rel=1e-4)


# ---------------------------------------------------------------------------
# FFmpeg failure tests
# ---------------------------------------------------------------------------

class TestExtractSceneRms:
    @patch("modules.audio_analysis.analyze.subprocess.run")
    def test_ffmpeg_failure_returns_zero(
        self, mock_run: MagicMock, mock_ingestion: IngestionResult, minimal_config: dict[str, Any]
    ) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        video_id = mock_ingestion.video_id
        scene = _make_scene(f"{video_id}_0_10000", video_id, 0, 10000)
        from modules.audio_analysis.analyze import _extract_scene_rms
        result = _extract_scene_rms(scene, "/fake/video.mp4", video_id, minimal_config)
        assert result == 0.0

    @patch("modules.audio_analysis.analyze.subprocess.run")
    def test_ffmpeg_success_parses_output(
        self, mock_run: MagicMock, mock_ingestion: IngestionResult, minimal_config: dict[str, Any]
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="lavfi.astats.Overall.RMS_level=-20.000000\n",
        )
        video_id = mock_ingestion.video_id
        scene = _make_scene(f"{video_id}_0_10000", video_id, 0, 10000)
        from modules.audio_analysis.analyze import _extract_scene_rms
        result = _extract_scene_rms(scene, "/fake/video.mp4", video_id, minimal_config)
        assert result == pytest.approx(0.1, rel=1e-4)
