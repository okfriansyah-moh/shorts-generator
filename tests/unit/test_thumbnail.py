"""Unit tests for the thumbnail module."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from contracts.clip import ClipDefinition
from contracts.face import FaceDetectionResult
from contracts.hook import HookResult
from contracts.ingestion import IngestionResult
from contracts.scoring import ScoredScene
from contracts.thumbnail import ThumbnailResult
from modules.thumbnail.thumbnail import (
    _build_text_overlay,
    _build_vf_filter,
    _jpeg_quality_to_qscale,
    _select_timestamp,
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
        keyword_score=0.6,
        audio_energy_score=0.7,
        face_presence_score=0.8,
        scene_activity_score=0.5,
        sentence_density_score=0.5,
        composite_score=0.62,
        rank=1,
    )


def _make_clip(
    clip_id: str = "abcd1234abcd1234",
    video_id: str = "a1b2c3d4e5f67890",
    start_time: int = 0,
    end_time: int = 45000,
    clip_index: int = 0,
) -> ClipDefinition:
    scene = _make_scored_scene(
        scene_id=f"{video_id}_{start_time}_{end_time}",
        video_id=video_id,
        start_time=start_time,
        end_time=end_time,
    )
    return ClipDefinition(
        clip_id=clip_id,
        video_id=video_id,
        scenes=(scene,),
        start_time=start_time,
        end_time=end_time,
        duration=float(end_time - start_time) / 1000.0,
        average_score=0.62,
        clip_index=clip_index,
    )


def _make_hook(
    clip_id: str = "abcd1234abcd1234",
    video_id: str = "a1b2c3d4e5f67890",
    hook_text: str = "INSANE 1V5 CLUTCH WIN",
    story_text: str = "Watch this incredible comeback in ranked play.",
) -> HookResult:
    return HookResult(
        clip_id=clip_id,
        video_id=video_id,
        hook_text=hook_text,
        story_text=story_text,
        template_id="action_result",
        keyword_source=("clutch", "win"),
    )


def _make_ingestion(
    video_id: str = "a1b2c3d4e5f67890",
    path: str = "/fake/video.mp4",
) -> IngestionResult:
    return IngestionResult(
        video_id=video_id,
        path=path,
        duration_seconds=3600.0,
        resolution=(1920, 1080),
        codec="h264",
        audio_codec="aac",
        has_audio=True,
        file_size_bytes=500_000_000,
        fps=30.0,
    )


def _make_face_result(video_id: str = "a1b2c3d4e5f67890") -> FaceDetectionResult:
    return FaceDetectionResult(
        video_id=video_id,
        scene_data=(),
        average_visibility=0.0,
        faceless_scene_count=0,
    )


def _sample_config(output_dir: str) -> dict:
    return {
        "paths": {"output_dir": output_dir, "temp_dir": os.path.join(output_dir, "tmp")},
        "thumbnail": {
            "width": 1280,
            "height": 720,
            "format": "jpeg",
            "quality": 90,
            "max_text_words": 3,
            "font_size": 72,
            "saturation_boost": 1.15,
            "contrast_boost": 1.10,
        },
    }


# ---------------------------------------------------------------------------
# Unit tests — helper functions
# ---------------------------------------------------------------------------

class TestSelectTimestamp:
    def test_returns_15_percent_into_clip(self):
        clip = _make_clip(start_time=0, end_time=45000)
        ts = _select_timestamp(clip)
        expected = 0.0 + 45.0 * 0.15
        assert abs(ts - expected) < 1e-6

    def test_deterministic(self):
        clip = _make_clip(start_time=10000, end_time=55000)
        assert _select_timestamp(clip) == _select_timestamp(clip)

    def test_respects_start_offset(self):
        clip = _make_clip(start_time=60000, end_time=100000)
        ts = _select_timestamp(clip)
        assert ts > 60.0
        assert ts < 100.0

    def test_within_clip_bounds(self):
        clip = _make_clip(start_time=5000, end_time=40000)
        ts = _select_timestamp(clip)
        assert ts >= clip.start_time / 1000.0
        assert ts <= clip.end_time / 1000.0


class TestBuildTextOverlay:
    def test_truncates_to_max_words(self):
        hook = _make_hook(hook_text="ONE TWO THREE FOUR FIVE")
        assert _build_text_overlay(hook, max_words=3) == "ONE TWO THREE"

    def test_uppercase(self):
        hook = _make_hook(hook_text="insane clutch win")
        assert _build_text_overlay(hook, max_words=3) == "INSANE CLUTCH WIN"

    def test_exact_max_words(self):
        hook = _make_hook(hook_text="A B")
        assert _build_text_overlay(hook, max_words=3) == "A B"

    def test_single_word(self):
        hook = _make_hook(hook_text="WIN")
        assert _build_text_overlay(hook, max_words=3) == "WIN"


class TestJpegQualityToQscale:
    def test_high_quality_low_qscale(self):
        q = _jpeg_quality_to_qscale(90)
        assert q <= 5

    def test_low_quality_high_qscale(self):
        q = _jpeg_quality_to_qscale(10)
        assert q >= 20

    def test_bounds(self):
        assert _jpeg_quality_to_qscale(100) >= 1
        assert _jpeg_quality_to_qscale(1) <= 31

    def test_deterministic(self):
        assert _jpeg_quality_to_qscale(90) == _jpeg_quality_to_qscale(90)


class TestBuildVfFilter:
    def test_contains_scale(self):
        vf = _build_vf_filter("/tmp/text.txt", 1.15, 1.10, 72)
        assert "scale=1280:720" in vf

    def test_contains_pad(self):
        vf = _build_vf_filter("/tmp/text.txt", 1.15, 1.10, 72)
        assert "pad=1280:720" in vf

    def test_contains_saturation(self):
        vf = _build_vf_filter("/tmp/text.txt", 1.15, 1.10, 72)
        assert "saturation=1.1500" in vf

    def test_contains_contrast(self):
        vf = _build_vf_filter("/tmp/text.txt", 1.15, 1.10, 72)
        assert "contrast=1.1000" in vf

    def test_contains_drawtext(self):
        vf = _build_vf_filter("/tmp/text.txt", 1.15, 1.10, 72)
        assert "drawtext" in vf
        assert "/tmp/text.txt" in vf

    def test_contains_fontsize(self):
        vf = _build_vf_filter("/tmp/text.txt", 1.15, 1.10, 72)
        assert "fontsize=72" in vf


# ---------------------------------------------------------------------------
# Unit tests — process function
# ---------------------------------------------------------------------------

class TestProcess:
    def _run_process(self, output_dir: str, **clip_overrides) -> ThumbnailResult:
        clip = _make_clip(**clip_overrides)
        hook = _make_hook(clip_id=clip.clip_id, video_id=clip.video_id)
        ingestion = _make_ingestion(video_id=clip.video_id)
        face_result = _make_face_result(video_id=clip.video_id)
        config = _sample_config(output_dir)

        mock_cp = MagicMock()
        mock_cp.returncode = 0
        mock_cp.stdout = ""
        mock_cp.stderr = ""

        with patch("subprocess.run", return_value=mock_cp), \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=12345):
            result = process(clip, face_result, hook, ingestion, config, output_dir)

        return result

    def test_returns_thumbnail_result(self, tmp_path):
        result = self._run_process(str(tmp_path))
        assert isinstance(result, ThumbnailResult)

    def test_correct_dimensions(self, tmp_path):
        result = self._run_process(str(tmp_path))
        assert result.resolution == (1280, 720)

    def test_clip_id_preserved(self, tmp_path):
        result = self._run_process(str(tmp_path), clip_id="test1234test1234")
        assert result.clip_id == "test1234test1234"

    def test_thumbnail_path_is_jpeg(self, tmp_path):
        result = self._run_process(str(tmp_path))
        assert result.image_path.endswith(".jpg")

    def test_text_overlay_max_words(self, tmp_path):
        clip = _make_clip()
        hook = _make_hook(hook_text="ONE TWO THREE FOUR FIVE")
        ingestion = _make_ingestion()
        face_result = _make_face_result()
        config = _sample_config(str(tmp_path))
        config["thumbnail"]["max_text_words"] = 3

        mock_cp = MagicMock()
        mock_cp.returncode = 0
        mock_cp.stdout = ""
        mock_cp.stderr = ""

        with patch("subprocess.run", return_value=mock_cp), \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=99):
            result = process(clip, face_result, hook, ingestion, config, str(tmp_path))

        assert result.text_overlay == "ONE TWO THREE"

    def test_calls_ffmpeg(self, tmp_path):
        """FFmpeg subprocess must be invoked when no cached file exists."""
        clip = _make_clip()
        hook = _make_hook()
        ingestion = _make_ingestion()
        face_result = _make_face_result()
        config = _sample_config(str(tmp_path))

        mock_cp = MagicMock()
        mock_cp.returncode = 0
        mock_cp.stdout = ""
        mock_cp.stderr = ""

        # First isfile call = no cache; second call = FFmpeg produced the file.
        with patch("subprocess.run", return_value=mock_cp) as mock_run, \
             patch("os.path.isfile", side_effect=[False, True]), \
             patch("os.path.getsize", return_value=99):
            process(clip, face_result, hook, ingestion, config, str(tmp_path))

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "ffmpeg" in cmd[0]

    def test_idempotent_returns_cached(self, tmp_path):
        """If file already exists and is non-empty, FFmpeg must NOT be called."""
        clip = _make_clip()
        hook = _make_hook()
        ingestion = _make_ingestion()
        face_result = _make_face_result()
        config = _sample_config(str(tmp_path))

        thumbnail_dir = os.path.join(str(tmp_path), clip.video_id, "thumbnails")
        os.makedirs(thumbnail_dir, exist_ok=True)
        cached_path = os.path.join(thumbnail_dir, f"{clip.clip_id}.jpg")
        # Create a non-empty fake thumbnail.
        with open(cached_path, "wb") as fh:
            fh.write(b"\xff\xd8\xff" + b"\x00" * 100)

        with patch("subprocess.run") as mock_run:
            result = process(clip, face_result, hook, ingestion, config, str(tmp_path))

        mock_run.assert_not_called()
        assert result.image_path == cached_path

    def test_ffmpeg_failure_raises(self, tmp_path):
        """RuntimeError must be raised if FFmpeg exits non-zero."""
        clip = _make_clip()
        hook = _make_hook()
        ingestion = _make_ingestion()
        face_result = _make_face_result()
        config = _sample_config(str(tmp_path))

        mock_cp = MagicMock()
        mock_cp.returncode = 1
        mock_cp.stdout = ""
        mock_cp.stderr = "No such file or directory"

        with patch("subprocess.run", return_value=mock_cp):
            with pytest.raises(RuntimeError):
                process(clip, face_result, hook, ingestion, config, str(tmp_path))

    def test_deterministic_same_output(self, tmp_path):
        """Two calls with identical input produce identical ThumbnailResult."""
        clip = _make_clip()
        hook = _make_hook()
        ingestion = _make_ingestion()
        face_result = _make_face_result()
        config = _sample_config(str(tmp_path))

        mock_cp = MagicMock()
        mock_cp.returncode = 0
        mock_cp.stdout = ""
        mock_cp.stderr = ""

        with patch("subprocess.run", return_value=mock_cp), \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=99):
            result1 = process(clip, face_result, hook, ingestion, config, str(tmp_path))

        # Reset (no cached file).
        tmp2 = tempfile.mkdtemp()
        config2 = _sample_config(tmp2)
        with patch("subprocess.run", return_value=mock_cp), \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=99):
            result2 = process(clip, face_result, hook, ingestion, config2, tmp2)

        assert result1.clip_id == result2.clip_id
        assert result1.resolution == result2.resolution
        assert result1.text_overlay == result2.text_overlay

    def test_none_face_result_accepted(self, tmp_path):
        """process() must accept None face_result without error."""
        clip = _make_clip()
        hook = _make_hook()
        ingestion = _make_ingestion()
        config = _sample_config(str(tmp_path))

        mock_cp = MagicMock()
        mock_cp.returncode = 0
        mock_cp.stdout = ""
        mock_cp.stderr = ""

        with patch("subprocess.run", return_value=mock_cp), \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=99):
            result = process(clip, None, hook, ingestion, config, str(tmp_path))

        assert isinstance(result, ThumbnailResult)
