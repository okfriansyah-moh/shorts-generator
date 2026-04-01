"""Unit tests for podcast video type support.

Tests cover:
  - Config overlay mechanism (_apply_video_type_overrides)
  - Podcast compositor: face-follow crop computation
  - Podcast compositor: center crop filter generation
  - Podcast compositor: dispatcher routes podcast to process_podcast
  - Podcast compositor: idempotency (skips FFmpeg when output exists)
  - Podcast compositor: fallback from face_follow to center on failure
  - Existing gameplay path isolation (unchanged by podcast support)
  - CLI --video-type argument parsing
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from contracts.clip import ClipDefinition
from contracts.compositor import CompositeStream
from contracts.face import FaceBBox, FaceDetectionResult, SceneFaceData
from contracts.ingestion import IngestionResult
from contracts.scoring import ScoredScene

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

VIDEO_ID = "a1b2c3d4e5f67890"
CLIP_ID = "c1d2e3f4a5b6c7d8"


def _make_scored_scene(
    video_id: str = VIDEO_ID,
    start_ms: int = 0,
    end_ms: int = 35000,
    score: float = 0.7,
) -> ScoredScene:
    return ScoredScene(
        scene_id=f"{video_id}_{start_ms}_{end_ms}",
        video_id=video_id,
        start_time=start_ms,
        end_time=end_ms,
        duration=(end_ms - start_ms) / 1000.0,
        keyword_score=score,
        audio_energy_score=score,
        face_presence_score=score,
        scene_activity_score=score,
        sentence_density_score=score,
        composite_score=score,
        rank=1,
    )


def _make_clip(
    start_ms: int = 0,
    end_ms: int = 35000,
    clip_index: int = 0,
) -> ClipDefinition:
    scene = _make_scored_scene(start_ms=start_ms, end_ms=end_ms)
    duration = (end_ms - start_ms) / 1000.0
    return ClipDefinition(
        clip_id=CLIP_ID,
        video_id=VIDEO_ID,
        scenes=(scene,),
        start_time=start_ms,
        end_time=end_ms,
        duration=duration,
        average_score=0.7,
        clip_index=clip_index,
    )


def _make_face_bbox(
    x: float = 0.3,
    y: float = 0.2,
    width: float = 0.15,
    height: float = 0.25,
    confidence: float = 0.9,
) -> FaceBBox:
    return FaceBBox(x=x, y=y, width=width, height=height, confidence=confidence, timestamp_ms=0)


def _make_scene_face_data(
    scene_id: str | None = None,
    visibility: float = 0.8,
    bbox: FaceBBox | None = None,
) -> SceneFaceData:
    sid = scene_id or f"{VIDEO_ID}_0_35000"
    b = bbox or _make_face_bbox()
    return SceneFaceData(
        scene_id=sid,
        face_visible_ratio=visibility,
        bounding_boxes=(b,),
        average_bbox=b,
        sample_count=10,
    )


def _make_face_result(
    scene_data: tuple[SceneFaceData, ...] | None = None,
) -> FaceDetectionResult:
    sd = scene_data or (_make_scene_face_data(),)
    return FaceDetectionResult(
        video_id=VIDEO_ID,
        scene_data=sd,
        average_visibility=0.8,
        faceless_scene_count=0,
        estimated_pip_bbox=_make_face_bbox(),
    )


def _make_ingestion_result() -> IngestionResult:
    return IngestionResult(
        video_id=VIDEO_ID,
        path="/tmp/test_video.mp4",
        duration_seconds=120.0,
        resolution=(1920, 1080),
        fps=30.0,
        codec="h264",
        has_audio=True,
        file_size_bytes=100_000_000,
        audio_codec="aac",
    )


def _podcast_config() -> dict:
    return {
        "video_type": "podcast",
        "paths": {"output_dir": "/tmp/test_output"},
        "pipeline": {"output_framerate": 30, "ffmpeg_timeout": 300},
        "compositor": {"default_layout": "split"},
        "podcast_compositor": {
            "crop_strategy": "face_follow",
            "face_padding": 0.15,
            "output_width": 1080,
            "output_height": 1920,
        },
        "_runtime": {"video_dir_name": "test_video"},
    }


def _gameplay_config() -> dict:
    return {
        "video_type": "gameplay",
        "paths": {"output_dir": "/tmp/test_output"},
        "pipeline": {"output_framerate": 30, "ffmpeg_timeout": 300},
        "compositor": {
            "default_layout": "split",
            "face_region": "auto",
            "face_zoom_factor": 1.0,
        },
        "_runtime": {"video_dir_name": "test_video"},
        "gpu": {"enabled": False},
    }


# ---------------------------------------------------------------------------
# Config overlay tests
# ---------------------------------------------------------------------------


class TestVideoTypeConfigOverlay:
    """Test _apply_video_type_overrides merges podcast config correctly."""

    def test_podcast_overlay_merges_scoring_weights(self):
        from run_pipeline import _apply_video_type_overrides

        config = {
            "video_type": "podcast",
            "scoring": {
                "weights": {"keyword": 3, "audio_energy": 2, "scene_activity": 1},
                "min_composite_score": 0.2,
            },
            "podcast_scoring": {
                "weights": {"keyword": 3, "scene_activity": 0, "sentence_density": 3},
                "min_composite_score": 0.15,
            },
        }
        _apply_video_type_overrides(config)
        assert config["scoring"]["min_composite_score"] == 0.15
        assert config["scoring"]["weights"]["sentence_density"] == 3
        assert config["scoring"]["weights"]["scene_activity"] == 0

    def test_gameplay_overlay_is_noop(self):
        from run_pipeline import _apply_video_type_overrides

        config = {
            "video_type": "gameplay",
            "scoring": {"weights": {"keyword": 3}},
            "podcast_scoring": {"weights": {"keyword": 99}},
        }
        _apply_video_type_overrides(config)
        assert config["scoring"]["weights"]["keyword"] == 3

    def test_missing_video_type_defaults_to_gameplay(self):
        from run_pipeline import _apply_video_type_overrides

        config = {"scoring": {"weights": {"keyword": 3}}}
        _apply_video_type_overrides(config)
        assert config["scoring"]["weights"]["keyword"] == 3

    def test_podcast_overlay_merges_scene_splitter(self):
        from run_pipeline import _apply_video_type_overrides

        config = {
            "video_type": "podcast",
            "scene_splitter": {"threshold": 27.0, "min_scene_duration": 3.0},
            "podcast_scene_splitter": {"threshold": 20.0, "min_scene_duration": 5.0},
        }
        _apply_video_type_overrides(config)
        assert config["scene_splitter"]["threshold"] == 20.0
        assert config["scene_splitter"]["min_scene_duration"] == 5.0

    def test_podcast_overlay_merges_face_detection(self):
        from run_pipeline import _apply_video_type_overrides

        config = {
            "video_type": "podcast",
            "face_detection": {"sample_fps": 2, "min_confidence": 0.7, "model_path": "models/x.tflite"},
            "podcast_face_detection": {"sample_fps": 1, "min_confidence": 0.5},
        }
        _apply_video_type_overrides(config)
        assert config["face_detection"]["sample_fps"] == 1
        assert config["face_detection"]["min_confidence"] == 0.5
        # Base key not in overlay is preserved
        assert config["face_detection"]["model_path"] == "models/x.tflite"


# ---------------------------------------------------------------------------
# Podcast compositor: crop filter tests
# ---------------------------------------------------------------------------


class TestPodcastCropFilter:
    """Test _build_plan_filter and _build_center_crop_filter output."""

    def test_center_crop_filter_format(self):
        from modules.compositor.podcast import _build_center_crop_filter

        vf = _build_center_crop_filter(1920, 1080)
        assert "crop=" in vf
        assert "scale=1080:1920" in vf
        assert "setsar=1" in vf

    def test_plan_filter_embeds_plan_coords(self):
        from contracts.strategies import PodcastFramePlan
        from modules.compositor.podcast import _build_plan_filter

        plan = PodcastFramePlan(
            crop_x=100, crop_y=0, crop_width=608, crop_height=1080,
            speaker_face_id="face_0", layout="speaker_crop",
        )
        vf = _build_plan_filter(plan)
        assert "crop=608:1080:100:0" in vf
        assert "scale=1080:1920" in vf
        assert "setsar=1" in vf


# ---------------------------------------------------------------------------
# Podcast compositor: process_podcast tests
# ---------------------------------------------------------------------------


class TestProcessPodcast:
    """Test podcast composition entry point."""

    @patch("modules.compositor.podcast._atomic_ffmpeg")
    @patch("modules.compositor.podcast.os.path.exists", return_value=False)
    @patch("modules.compositor._helpers.os.makedirs")
    def test_speaker_crop_composition(self, mock_mkdirs, mock_exists, mock_ffmpeg):
        """Compositor applies speaker_crop plan from strategy."""
        from contracts.strategies import PodcastFramePlan
        from modules.compositor.podcast import process_podcast

        plan = PodcastFramePlan(
            crop_x=100, crop_y=0, crop_width=608, crop_height=1080,
            speaker_face_id="face_0", layout="speaker_crop",
        )

        clip = _make_clip()
        face_result = _make_face_result()
        ingestion = _make_ingestion_result()
        config = _podcast_config()

        result = process_podcast(clip, face_result, ingestion, config, plan)

        assert isinstance(result, CompositeStream)
        assert result.layout == "speaker_crop"
        assert result.resolution == (1080, 1920)
        assert result.has_face is True
        mock_ffmpeg.assert_called_once()

    @patch("modules.compositor.podcast._atomic_ffmpeg")
    @patch("modules.compositor.podcast.os.path.exists", return_value=False)
    @patch("modules.compositor._helpers.os.makedirs")
    def test_center_crop_when_no_faces(self, mock_mkdirs, mock_exists, mock_ffmpeg):
        """When strategy returns center_crop plan, compositor uses it (no face)."""
        from contracts.strategies import PodcastFramePlan
        from modules.compositor.podcast import process_podcast

        plan = PodcastFramePlan(
            crop_x=660, crop_y=0, crop_width=608, crop_height=1080,
            speaker_face_id=None, layout="center_crop",
        )

        clip = _make_clip()
        face_result = FaceDetectionResult(
            video_id=VIDEO_ID,
            scene_data=(),
            average_visibility=0.0,
            faceless_scene_count=1,
            estimated_pip_bbox=None,
        )
        ingestion = _make_ingestion_result()
        config = _podcast_config()

        result = process_podcast(clip, face_result, ingestion, config, plan)

        assert result.layout == "center_crop"
        assert result.has_face is False
        mock_ffmpeg.assert_called_once()

    @patch("modules.compositor.podcast.os.path.exists", return_value=True)
    @patch("modules.compositor._helpers.os.makedirs")
    def test_idempotency_returns_cached(self, mock_mkdirs, mock_exists):
        """When composite file exists, return cached result without calling FFmpeg."""
        from contracts.strategies import PodcastFramePlan
        from modules.compositor.podcast import process_podcast

        plan = PodcastFramePlan(
            crop_x=100, crop_y=0, crop_width=608, crop_height=1080,
            speaker_face_id="face_0", layout="speaker_crop",
        )

        clip = _make_clip()
        face_result = _make_face_result()
        ingestion = _make_ingestion_result()
        config = _podcast_config()

        result = process_podcast(clip, face_result, ingestion, config, plan)

        assert isinstance(result, CompositeStream)
        assert result.clip_id == CLIP_ID

    @patch("modules.compositor.podcast._compose_center_fallback")
    @patch("modules.compositor.podcast._compose_with_plan", side_effect=RuntimeError("FFmpeg failed"))
    @patch("modules.compositor.podcast.os.path.exists", return_value=False)
    @patch("modules.compositor._helpers.os.makedirs")
    def test_plan_crop_fallback_to_center(self, mock_mkdirs, mock_exists, mock_compose, mock_center):
        """When _compose_with_plan raises RuntimeError, fallback to center crop."""
        from contracts.strategies import PodcastFramePlan
        from modules.compositor.podcast import process_podcast

        plan = PodcastFramePlan(
            crop_x=100, crop_y=0, crop_width=608, crop_height=1080,
            speaker_face_id="face_0", layout="speaker_crop",
        )

        clip = _make_clip()
        face_result = _make_face_result()
        ingestion = _make_ingestion_result()
        config = _podcast_config()

        result = process_podcast(clip, face_result, ingestion, config, plan)

        assert result.layout == "center_crop"
        mock_center.assert_called_once()


# ---------------------------------------------------------------------------
# Dispatcher test: compose.py routes podcast to process_podcast
# ---------------------------------------------------------------------------


class TestCompositorDispatch:
    """Test that compose.process() dispatches podcast videos correctly."""

    @patch("modules.compositor.compose.process_podcast")
    def test_podcast_dispatched(self, mock_podcast):
        from modules.compositor.compose import process

        mock_podcast.return_value = CompositeStream(
            clip_id=CLIP_ID,
            video_id=VIDEO_ID,
            composite_path="/tmp/out.mp4",
            source_audio_path="/tmp/src.mp4",
            resolution=(1080, 1920),
            layout="podcast_face_follow",
            duration_seconds=35.0,
            has_face=True,
            source_fps=30.0,
            start_time_ms=0,
        )

        clip = _make_clip()
        face_result = _make_face_result()
        ingestion = _make_ingestion_result()
        config = _podcast_config()

        result = process(clip, face_result, ingestion, config)

        mock_podcast.assert_called_once_with(clip, face_result, ingestion, config, None)
        assert result.layout == "podcast_face_follow"

    @patch("modules.compositor.compose._atomic_ffmpeg")
    @patch("modules.compositor.compose.os.path.exists", return_value=False)
    @patch("modules.compositor._helpers.os.makedirs")
    def test_gameplay_not_dispatched_to_podcast(self, mock_mkdirs, mock_exists, mock_ffmpeg):
        from modules.compositor.compose import process

        clip = _make_clip()
        face_result = _make_face_result()
        ingestion = _make_ingestion_result()
        config = _gameplay_config()

        result = process(clip, face_result, ingestion, config)

        # Should produce gameplay layout, NOT podcast
        assert "podcast" not in result.layout


# ---------------------------------------------------------------------------
# Gameplay isolation tests — ensure podcast changes didn't break gameplay
# ---------------------------------------------------------------------------


class TestGameplayIsolation:
    """Ensure existing gameplay compositor logic is completely untouched."""

    @patch("modules.compositor.compose._atomic_ffmpeg")
    @patch("modules.compositor.compose.os.path.exists", return_value=False)
    @patch("modules.compositor._helpers.os.makedirs")
    def test_gameplay_split_layout_unchanged(self, mock_mkdirs, mock_exists, mock_ffmpeg):
        from modules.compositor.compose import process

        clip = _make_clip()
        face_result = _make_face_result()
        ingestion = _make_ingestion_result()
        config = _gameplay_config()

        result = process(clip, face_result, ingestion, config)

        assert result.layout == "face_gameplay_split"
        assert result.has_face is True

    @patch("modules.compositor.compose._atomic_ffmpeg")
    @patch("modules.compositor.compose.os.path.exists", return_value=False)
    @patch("modules.compositor._helpers.os.makedirs")
    def test_gameplay_only_layout_unchanged(self, mock_mkdirs, mock_exists, mock_ffmpeg):
        from modules.compositor.compose import process

        clip = _make_clip()
        face_result = _make_face_result()
        ingestion = _make_ingestion_result()
        config = _gameplay_config()
        config["compositor"]["default_layout"] = "gameplay_only"

        result = process(clip, face_result, ingestion, config)

        assert result.layout == "gameplay_only_zoom"
        assert result.has_face is False


# ---------------------------------------------------------------------------
# CLI argument tests
# ---------------------------------------------------------------------------


class TestCLIVideoType:
    """Test --video-type CLI argument parsing."""

    def test_video_type_gameplay(self):
        from run_pipeline import parse_args

        args = parse_args(["--video-type", "gameplay", "/tmp/test.mp4"])
        assert args.video_type == "gameplay"

    def test_video_type_podcast(self):
        from run_pipeline import parse_args

        args = parse_args(["--video-type", "podcast", "/tmp/test.mp4"])
        assert args.video_type == "podcast"

    def test_video_type_default_none(self):
        from run_pipeline import parse_args

        args = parse_args(["/tmp/test.mp4"])
        assert args.video_type is None

    def test_invalid_video_type_rejected(self):
        from run_pipeline import parse_args

        with pytest.raises(SystemExit):
            parse_args(["--video-type", "invalid", "/tmp/test.mp4"])


# ---------------------------------------------------------------------------
# Podcast strategy: largest-face fallback (no transcript)
# ---------------------------------------------------------------------------


class TestPodcastLargestFaceFallback:
    """Test generate_plan selects largest-area face when no transcript is given."""

    def test_largest_face_selected_by_area(self):
        from modules.strategies.podcast_strategy import generate_plan

        small_bbox = FaceBBox(x=0.1, y=0.1, width=0.1, height=0.1, confidence=0.9, timestamp_ms=500)
        large_bbox = FaceBBox(x=0.5, y=0.1, width=0.3, height=0.4, confidence=0.8, timestamp_ms=1000)

        scene = _make_scored_scene(start_ms=0, end_ms=5000)
        clip = ClipDefinition(
            clip_id=CLIP_ID, video_id=VIDEO_ID,
            scenes=(scene,), start_time=0, end_time=5000,
            duration=5.0, average_score=0.5, clip_index=0,
        )
        sfd = SceneFaceData(
            scene_id=f"{VIDEO_ID}_0_5000",
            face_visible_ratio=0.8,
            bounding_boxes=(small_bbox, large_bbox),
            average_bbox=large_bbox,
            sample_count=2,
        )
        face_result = _make_face_result(scene_data=(sfd,))
        ingestion = _make_ingestion_result()

        plan = generate_plan(clip, None, face_result, ingestion, _podcast_config())
        assert plan.layout == "center_face_crop"
        # large_bbox is x=0.5, small is x=0.1 → after clustering:
        # face_0 = left (x=0.1), face_1 = right (x=0.5)
        # largest area = face_1
        assert plan.speaker_face_id == "face_1"

    def test_no_faces_returns_center_crop(self):
        from modules.strategies.podcast_strategy import generate_plan

        scene = _make_scored_scene()
        clip = ClipDefinition(
            clip_id=CLIP_ID, video_id=VIDEO_ID,
            scenes=(scene,), start_time=0, end_time=35000,
            duration=35.0, average_score=0.5, clip_index=0,
        )
        face_result = FaceDetectionResult(
            video_id=VIDEO_ID, scene_data=(),
            average_visibility=0.0, faceless_scene_count=1,
        )
        ingestion = _make_ingestion_result()

        plan = generate_plan(clip, None, face_result, ingestion, _podcast_config())
        assert plan.layout == "center_crop"
        assert plan.speaker_face_id is None


# ---------------------------------------------------------------------------
# CompositeStream DTO layout values
# ---------------------------------------------------------------------------


class TestCompositeStreamLayouts:
    """Verify new podcast layout values are valid strings in the DTO."""

    def test_speaker_crop_layout(self):
        cs = CompositeStream(
            clip_id=CLIP_ID, video_id=VIDEO_ID,
            composite_path="/tmp/out.mp4", source_audio_path="/tmp/src.mp4",
            resolution=(1080, 1920), layout="speaker_crop",
            duration_seconds=35.0, has_face=True, source_fps=30.0,
        )
        assert cs.layout == "speaker_crop"

    def test_center_face_crop_layout(self):
        cs = CompositeStream(
            clip_id=CLIP_ID, video_id=VIDEO_ID,
            composite_path="/tmp/out.mp4", source_audio_path="/tmp/src.mp4",
            resolution=(1080, 1920), layout="center_face_crop",
            duration_seconds=35.0, has_face=True, source_fps=30.0,
        )
        assert cs.layout == "center_face_crop"

    def test_center_crop_layout(self):
        cs = CompositeStream(
            clip_id=CLIP_ID, video_id=VIDEO_ID,
            composite_path="/tmp/out.mp4", source_audio_path="/tmp/src.mp4",
            resolution=(1080, 1920), layout="center_crop",
            duration_seconds=35.0, has_face=False, source_fps=30.0,
        )
        assert cs.layout == "center_crop"
