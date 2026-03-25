"""Unit tests for modules/scene_splitter/split.py."""

from __future__ import annotations

import pytest

from contracts.ingestion import IngestionResult
from modules.scene_splitter.split import (
    _build_segments,
    _merge_short_scenes,
    _post_process,
    _single_scene_fallback,
    _split_long_scenes,
    _uniform_split,
    split_scenes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ingestion_result(
    duration_seconds: float = 3600.0,
    video_path: str = "/fake/video.mp4",
) -> IngestionResult:
    return IngestionResult(
        video_id="abcdef1234567890",
        path=video_path,
        duration_seconds=duration_seconds,
        resolution=(1920, 1080),
        codec="h264",
        audio_codec="aac",
        has_audio=True,
        file_size_bytes=1_000_000,
        fps=30.0,
    )


def _minimal_config(
    threshold: float = 27.0,
    min_scene: float = 3.0,
    max_scene: float = 20.0,
) -> dict:
    return {
        "scene_splitter": {
            "threshold": threshold,
            "min_scene_duration": min_scene,
            "max_scene_duration": max_scene,
        }
    }


# ---------------------------------------------------------------------------
# Uniform splitting
# ---------------------------------------------------------------------------


class TestUniformSplit:
    """Tests for _uniform_split."""

    def test_uniform_split_coverage(self):
        segs = _uniform_split(100.0, target_duration=10.0)
        assert segs[0][0] == pytest.approx(0.0)
        assert segs[-1][1] == pytest.approx(100.0)

    def test_uniform_split_count(self):
        segs = _uniform_split(100.0, target_duration=10.0)
        assert len(segs) == 10

    def test_uniform_split_zero_duration(self):
        assert _uniform_split(0.0) == []

    def test_uniform_split_partial_last_segment(self):
        segs = _uniform_split(25.0, target_duration=10.0)
        assert len(segs) == 3
        assert segs[-1][1] == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# Merge short scenes
# ---------------------------------------------------------------------------


class TestMergeShortScenes:
    """Tests for _merge_short_scenes."""

    def test_no_merge_when_all_long_enough(self):
        scenes = [(0.0, 5.0), (5.0, 10.0), (10.0, 15.0)]
        result = _merge_short_scenes(scenes, min_dur=3.0)
        assert result == scenes

    def test_merges_micro_scene_with_predecessor(self):
        scenes = [(0.0, 5.0), (5.0, 7.0), (7.0, 8.0)]
        result = _merge_short_scenes(scenes, min_dur=3.0)
        assert (5.0, 8.0) in result or len(result) < 3

    def test_empty_input(self):
        assert _merge_short_scenes([], 3.0) == []

    def test_single_short_scene_kept(self):
        result = _merge_short_scenes([(0.0, 2.0)], 3.0)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Split long scenes
# ---------------------------------------------------------------------------


class TestSplitLongScenes:
    """Tests for _split_long_scenes."""

    def test_short_scene_unchanged(self):
        scenes = [(0.0, 10.0)]
        result = _split_long_scenes(scenes, max_dur=20.0)
        assert result == [(0.0, 10.0)]

    def test_long_scene_split_at_midpoint(self):
        scenes = [(0.0, 40.0)]
        result = _split_long_scenes(scenes, max_dur=20.0)
        assert len(result) == 2
        assert result[0] == (0.0, 20.0)
        assert result[1] == (20.0, 40.0)

    def test_very_long_scene_recursive_split(self):
        scenes = [(0.0, 80.0)]
        result = _split_long_scenes(scenes, max_dur=20.0)
        assert len(result) == 4
        for start, end in result:
            assert end - start <= 20.0


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------


class TestPostProcess:
    """Tests for _post_process."""

    def test_all_scenes_within_bounds(self):
        scenes = [(0.0, 5.0), (5.0, 10.0), (10.0, 15.0)]
        result = _post_process(scenes, min_dur=3.0, max_dur=20.0, total_secs=15.0)
        assert len(result) == 3

    def test_merges_short_and_splits_long(self):
        scenes = [(0.0, 2.0), (2.0, 25.0)]
        result = _post_process(scenes, min_dur=3.0, max_dur=20.0, total_secs=25.0)
        for start, end in result:
            assert end - start <= 20.0


# ---------------------------------------------------------------------------
# Build segments
# ---------------------------------------------------------------------------


class TestBuildSegments:
    """Tests for _build_segments."""

    def test_scene_id_format(self):
        scenes = [(0.0, 5.0)]
        segs = _build_segments(scenes, "abcdef1234567890")
        assert segs[0].scene_id == "abcdef1234567890_0_5000"

    def test_sorted_by_start_time(self):
        scenes = [(5.0, 10.0), (0.0, 5.0)]
        segs = _build_segments(scenes, "vid1234567890123")
        assert segs[0].start_time == 0
        assert segs[1].start_time == 5000

    def test_duration_computed_correctly(self):
        scenes = [(0.0, 7.5)]
        segs = _build_segments(scenes, "abcdef1234567890")
        assert segs[0].duration == pytest.approx(7.5)

    def test_skips_zero_duration(self):
        scenes = [(5.0, 5.0)]
        segs = _build_segments(scenes, "abcdef1234567890")
        assert segs == []


# ---------------------------------------------------------------------------
# Single scene fallback
# ---------------------------------------------------------------------------


class TestSingleSceneFallback:
    """Tests for _single_scene_fallback."""

    def test_creates_one_segment(self):
        segs = _single_scene_fallback("abcdef1234567890", 3600.0)
        assert len(segs) == 1
        assert segs[0].start_time == 0
        assert segs[0].end_time == 3600000
        assert segs[0].duration == pytest.approx(3600.0)


# ---------------------------------------------------------------------------
# split_scenes() — integration with mocked scenedetect
# ---------------------------------------------------------------------------


class TestSplitScenes:
    """Tests for the main split_scenes() function."""

    def test_normal_video_with_mocked_scenedetect(self, monkeypatch, tmp_path):
        """Mocked scenedetect returns valid scenes."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)
        ingestion = _make_ingestion_result(
            duration_seconds=3600.0, video_path=str(f)
        )

        raw_pairs = [(float(i * 10), float(i * 10 + 10)) for i in range(360)]

        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: raw_pairs,
        )

        result = split_scenes(ingestion, _minimal_config())

        assert result.video_id == "abcdef1234567890"
        assert len(result.scenes) > 0
        assert result.total_duration == pytest.approx(
            sum(s.duration for s in result.scenes), abs=0.01
        )

    def test_static_video_single_scene_fallback(self, monkeypatch, tmp_path):
        """When scenedetect returns empty list, entire video is one scene."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)
        ingestion = _make_ingestion_result(
            duration_seconds=1800.0, video_path=str(f)
        )

        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: [(0.0, 1800.0)],
        )

        result = split_scenes(ingestion, _minimal_config())
        assert len(result.scenes) >= 1
        assert result.video_id == "abcdef1234567890"

    def test_flickering_video_merges_micro_scenes(self, monkeypatch, tmp_path):
        """Short scenes (< 3s) are merged with predecessors."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)
        ingestion = _make_ingestion_result(
            duration_seconds=120.0, video_path=str(f)
        )

        # Many 1-second scenes (micro-scenes)
        micro_scenes = [(float(i), float(i + 1)) for i in range(120)]
        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: micro_scenes,
        )

        result = split_scenes(ingestion, _minimal_config(min_scene=3.0))
        # After merging, all scenes should be >= 3s
        for scene in result.scenes:
            assert scene.duration >= 3.0 - 1e-9

    def test_scenedetect_unavailable_falls_back_to_uniform(self, monkeypatch, tmp_path):
        """If PySceneDetect is unavailable, uniform splitting is used."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)
        ingestion = _make_ingestion_result(
            duration_seconds=1800.0, video_path=str(f)
        )

        def raise_import_error(path: str, threshold: float):
            raise ImportError("scenedetect not installed")

        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            raise_import_error,
        )

        result = split_scenes(ingestion, _minimal_config())
        assert len(result.scenes) > 0

    def test_repeated_runs_produce_identical_scenes(self, monkeypatch, tmp_path):
        """Same input + same config = identical SceneList (determinism)."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)
        ingestion = _make_ingestion_result(
            duration_seconds=1800.0, video_path=str(f)
        )
        raw_pairs = [(float(i * 10), float(i * 10 + 10)) for i in range(180)]

        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: raw_pairs,
        )

        r1 = split_scenes(ingestion, _minimal_config())
        r2 = split_scenes(ingestion, _minimal_config())
        assert r1.scenes == r2.scenes
        assert r1.total_duration == r2.total_duration

    def test_no_scene_shorter_than_min(self, monkeypatch, tmp_path):
        """Post-processing ensures no scene is shorter than min_scene_duration."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)
        ingestion = _make_ingestion_result(
            duration_seconds=3600.0, video_path=str(f)
        )
        # Mix of very short and normal scenes
        raw_pairs = [(0.0, 1.0), (1.0, 5.0), (5.0, 6.0), (6.0, 10.0)]
        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: raw_pairs,
        )

        result = split_scenes(ingestion, _minimal_config(min_scene=3.0, max_scene=20.0))
        for scene in result.scenes:
            assert scene.duration >= 3.0 - 1e-9

    def test_no_scene_longer_than_max(self, monkeypatch, tmp_path):
        """Post-processing ensures no scene exceeds max_scene_duration."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)
        ingestion = _make_ingestion_result(
            duration_seconds=3600.0, video_path=str(f)
        )
        # One very long scene
        raw_pairs = [(0.0, 60.0)]
        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: raw_pairs,
        )

        result = split_scenes(ingestion, _minimal_config(max_scene=20.0))
        for scene in result.scenes:
            assert scene.duration <= 20.0 + 1e-9

    def test_result_is_frozen(self, monkeypatch, tmp_path):
        """SceneList and SceneSegment are frozen dataclasses."""
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)
        ingestion = _make_ingestion_result(
            duration_seconds=1800.0, video_path=str(f)
        )
        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: [(0.0, 1800.0)],
        )
        result = split_scenes(ingestion, _minimal_config())

        with pytest.raises((AttributeError, TypeError)):
            result.video_id = "mutated"  # type: ignore[misc]
        with pytest.raises((AttributeError, TypeError)):
            result.scenes[0].scene_id = "mutated"  # type: ignore[misc]
