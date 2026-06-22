"""Unit tests for modules/strategies/sports_strategy.py and sports compositor dispatch.

Tests cover:
  1. Face centroid cascade — used when faces present above threshold
  2. Center fallback — used when all cascade methods return None
  3. Pose/motion cascade skipped when mediapipe/PIL unavailable
  4. Crop rect within source dimensions
  5. Determinism — same inputs → same SportsFramePlan every time
  6. SportsFramePlan DTO — frozen, correct field types
  7. compose.process dispatch — sports_tennis/football/padel routes correctly
  8. Filter builders — letterbox/center_crop/action_crop produce correct vf strings
  9. sports_utils.process_sports source_fps uses output_framerate not ingestion fps
  10. sports_utils.process_sports reads output_width/height from compositor config
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from contracts.clip import ClipDefinition
from contracts.compositor import CompositeStream
from contracts.face import FaceBBox, FaceDetectionResult, SceneFaceData
from contracts.ingestion import IngestionResult
from contracts.scoring import ScoredScene
from contracts.strategies import SportsFramePlan

from modules.strategies.sports_strategy import (
    _anchor_to_crop_rect,
    _center_crop_rect,
    _try_face_centroid,
    generate_plan,
)
from modules.compositor.sports_utils import (
    build_sports_letterbox_filter,
    build_sports_center_crop_filter,
    build_sports_action_crop_filter,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIDEO_ID = "a1b2c3d4e5f67890"
CLIP_ID = "c1d2e3f4a5b6c7d8"
SRC_WIDTH = 1920
SRC_HEIGHT = 1080


# ---------------------------------------------------------------------------
# Fixture factories
# ---------------------------------------------------------------------------


def _make_bbox(
    x: float = 0.4,
    y: float = 0.2,
    width: float = 0.15,
    height: float = 0.25,
    ts_ms: int = 0,
) -> FaceBBox:
    return FaceBBox(
        x=x, y=y, width=width, height=height,
        confidence=0.9, timestamp_ms=ts_ms,
    )


def _make_scored_scene(start_ms: int = 0, end_ms: int = 35000) -> ScoredScene:
    return ScoredScene(
        scene_id=f"{VIDEO_ID}_{start_ms}_{end_ms}",
        video_id=VIDEO_ID,
        start_time=start_ms,
        end_time=end_ms,
        duration=(end_ms - start_ms) / 1000.0,
        keyword_score=0.5,
        audio_energy_score=0.5,
        face_presence_score=0.5,
        scene_activity_score=0.5,
        sentence_density_score=0.5,
        composite_score=0.5,
        rank=1,
    )


def _make_clip(start_ms: int = 0, end_ms: int = 35000) -> ClipDefinition:
    scene = _make_scored_scene(start_ms=start_ms, end_ms=end_ms)
    return ClipDefinition(
        clip_id=CLIP_ID,
        video_id=VIDEO_ID,
        scenes=(scene,),
        start_time=start_ms,
        end_time=end_ms,
        duration=(end_ms - start_ms) / 1000.0,
        average_score=0.5,
        clip_index=0,
    )


def _make_scene_face_data(
    start_ms: int = 0,
    end_ms: int = 35000,
    avg_bbox: FaceBBox | None = None,
    visibility: float = 0.8,
) -> SceneFaceData:
    bboxes = (avg_bbox,) if avg_bbox is not None else ()
    return SceneFaceData(
        scene_id=f"{VIDEO_ID}_{start_ms}_{end_ms}",
        face_visible_ratio=visibility,
        bounding_boxes=bboxes,
        average_bbox=avg_bbox,
        sample_count=max(len(bboxes), 1),
    )


def _make_face_result(
    scene_data: tuple[SceneFaceData, ...] = (),
) -> FaceDetectionResult:
    avg_vis = (
        sum(s.face_visible_ratio for s in scene_data) / len(scene_data)
        if scene_data else 0.0
    )
    return FaceDetectionResult(
        video_id=VIDEO_ID,
        scene_data=scene_data,
        average_visibility=avg_vis,
        faceless_scene_count=sum(1 for s in scene_data if s.face_visible_ratio == 0.0),
    )


def _make_ingestion(
    width: int = SRC_WIDTH,
    height: int = SRC_HEIGHT,
    fps: float = 60.0,
) -> IngestionResult:
    return IngestionResult(
        video_id=VIDEO_ID,
        path="/tmp/sports_test.mp4",
        duration_seconds=120.0,
        resolution=(width, height),
        fps=fps,
        codec="h264",
        has_audio=True,
        file_size_bytes=50_000_000,
        audio_codec="aac",
    )


def _make_config(
    video_type: str = "sports_football",
    out_fps: int = 30,
) -> dict:
    return {
        "video_type": video_type,
        "pipeline": {"output_framerate": out_fps, "ffmpeg_timeout": 60},
        "compositor": {"default_layout": "sports_action_crop"},
        "sports_strategy": {
            "min_face_visibility": 0.2,
            "pose_sample_fps": 1,
            "motion_sample_fps": 2,
            "motion_thumb_width": 64,
            "motion_thumb_height": 36,
        },
    }


def _make_sports_plan(
    sport: str = "football",
    tracking_method: str = "center",
) -> SportsFramePlan:
    crop_x, crop_y, crop_w, crop_h = _center_crop_rect(SRC_WIDTH, SRC_HEIGHT)
    return SportsFramePlan(
        layout="sports_action_crop",
        sport=sport,
        tracking_method=tracking_method,
        crop_x=crop_x,
        crop_y=crop_y,
        crop_width=crop_w,
        crop_height=crop_h,
    )


# ---------------------------------------------------------------------------
# 1. Face centroid cascade
# ---------------------------------------------------------------------------


class TestFaceCentroid:
    def test_face_centroid_returns_anchor_when_faces_visible(self) -> None:
        bbox = _make_bbox(x=0.4, y=0.2, width=0.15, height=0.25)
        scene_data = (_make_scene_face_data(avg_bbox=bbox, visibility=0.8),)
        face_result = _make_face_result(scene_data)
        clip = _make_clip()

        anchor = _try_face_centroid(clip, face_result, SRC_WIDTH, SRC_HEIGHT, 0.2)

        assert anchor is not None
        anchor_x, anchor_y = anchor
        # anchor_x should be center of bbox: 0.4 + 0.15/2 = 0.475
        assert abs(anchor_x - 0.475) < 1e-6
        assert anchor_y == 0.5

    def test_face_centroid_returns_none_below_visibility_threshold(self) -> None:
        bbox = _make_bbox()
        scene_data = (_make_scene_face_data(avg_bbox=bbox, visibility=0.1),)
        face_result = _make_face_result(scene_data)
        clip = _make_clip()

        anchor = _try_face_centroid(clip, face_result, SRC_WIDTH, SRC_HEIGHT, 0.2)
        assert anchor is None

    def test_face_centroid_returns_none_when_no_faces(self) -> None:
        face_result = _make_face_result(())
        clip = _make_clip()

        anchor = _try_face_centroid(clip, face_result, SRC_WIDTH, SRC_HEIGHT, 0.2)
        assert anchor is None

    def test_face_centroid_only_uses_clip_scenes(self) -> None:
        # Scene in clip
        bbox_in = _make_bbox(x=0.6)
        scene_in = _make_scene_face_data(start_ms=0, end_ms=35000, avg_bbox=bbox_in, visibility=0.9)
        # Scene NOT in clip
        bbox_out = _make_bbox(x=0.1)
        scene_out = _make_scene_face_data(start_ms=50000, end_ms=85000, avg_bbox=bbox_out, visibility=0.9)

        face_result = _make_face_result((scene_in, scene_out))
        clip = _make_clip(start_ms=0, end_ms=35000)

        anchor = _try_face_centroid(clip, face_result, SRC_WIDTH, SRC_HEIGHT, 0.2)

        assert anchor is not None
        # Should use only scene_in's bbox center, not scene_out's
        expected_x = bbox_in.x + bbox_in.width * 0.5  # 0.6 + 0.075
        assert abs(anchor[0] - expected_x) < 1e-6


# ---------------------------------------------------------------------------
# 2. Center fallback
# ---------------------------------------------------------------------------


class TestCenterFallback:
    def test_generate_plan_falls_back_to_center_when_no_faces_no_deps(self) -> None:
        face_result = _make_face_result(())
        ingestion = _make_ingestion()
        clip = _make_clip()
        config = _make_config()

        # Patch pose and motion to return None (simulating missing deps)
        with (
            patch("modules.strategies.sports_strategy._try_pose_centroid", return_value=None),
            patch("modules.strategies.sports_strategy._try_motion_energy", return_value=None),
        ):
            plan = generate_plan(clip, face_result, ingestion, config)

        assert plan.tracking_method == "center"
        assert plan.layout == "sports_action_crop"

    def test_generate_plan_center_fallback_crops_within_bounds(self) -> None:
        face_result = _make_face_result(())
        ingestion = _make_ingestion()
        clip = _make_clip()
        config = _make_config()

        with (
            patch("modules.strategies.sports_strategy._try_pose_centroid", return_value=None),
            patch("modules.strategies.sports_strategy._try_motion_energy", return_value=None),
        ):
            plan = generate_plan(clip, face_result, ingestion, config)

        assert plan.crop_x >= 0
        assert plan.crop_y >= 0
        assert plan.crop_x + plan.crop_width <= SRC_WIDTH
        assert plan.crop_y + plan.crop_height <= SRC_HEIGHT
        assert plan.crop_width > 0
        assert plan.crop_height > 0


# ---------------------------------------------------------------------------
# 3. Cascade priority order
# ---------------------------------------------------------------------------


class TestCascadeOrder:
    def test_face_centroid_used_when_faces_present(self) -> None:
        bbox = _make_bbox(x=0.3)
        scene_data = (_make_scene_face_data(avg_bbox=bbox, visibility=0.9),)
        face_result = _make_face_result(scene_data)
        ingestion = _make_ingestion()
        clip = _make_clip()
        config = _make_config()

        with (
            patch("modules.strategies.sports_strategy._try_pose_centroid") as mock_pose,
            patch("modules.strategies.sports_strategy._try_motion_energy") as mock_motion,
        ):
            plan = generate_plan(clip, face_result, ingestion, config)
            # Pose and motion should NOT be called when face centroid succeeds
            mock_pose.assert_not_called()
            mock_motion.assert_not_called()

        assert plan.tracking_method == "face_centroid"

    def test_pose_used_when_no_faces(self) -> None:
        face_result = _make_face_result(())
        ingestion = _make_ingestion()
        clip = _make_clip()
        config = _make_config()

        with (
            patch(
                "modules.strategies.sports_strategy._try_pose_centroid",
                return_value=(0.55, 0.5),
            ) as mock_pose,
            patch("modules.strategies.sports_strategy._try_motion_energy") as mock_motion,
        ):
            plan = generate_plan(clip, face_result, ingestion, config)
            mock_pose.assert_called_once()
            mock_motion.assert_not_called()

        assert plan.tracking_method == "pose"

    def test_motion_used_when_no_faces_and_no_pose(self) -> None:
        face_result = _make_face_result(())
        ingestion = _make_ingestion()
        clip = _make_clip()
        config = _make_config()

        with (
            patch("modules.strategies.sports_strategy._try_pose_centroid", return_value=None),
            patch(
                "modules.strategies.sports_strategy._try_motion_energy",
                return_value=(0.6, 0.5),
            ) as mock_motion,
        ):
            plan = generate_plan(clip, face_result, ingestion, config)
            mock_motion.assert_called_once()

        assert plan.tracking_method == "motion_energy"


# ---------------------------------------------------------------------------
# 4. SportsFramePlan DTO
# ---------------------------------------------------------------------------


class TestSportsFramePlanDTO:
    def test_plan_is_frozen(self) -> None:
        plan = _make_sports_plan()
        with pytest.raises((AttributeError, TypeError)):
            plan.sport = "tennis"  # type: ignore[misc]

    def test_plan_has_correct_field_types(self) -> None:
        face_result = _make_face_result(())
        ingestion = _make_ingestion()
        clip = _make_clip()
        config = _make_config()

        with (
            patch("modules.strategies.sports_strategy._try_pose_centroid", return_value=None),
            patch("modules.strategies.sports_strategy._try_motion_energy", return_value=None),
        ):
            plan = generate_plan(clip, face_result, ingestion, config)

        assert isinstance(plan.layout, str)
        assert isinstance(plan.sport, str)
        assert isinstance(plan.tracking_method, str)
        assert isinstance(plan.crop_x, int)
        assert isinstance(plan.crop_y, int)
        assert isinstance(plan.crop_width, int)
        assert isinstance(plan.crop_height, int)

    def test_plan_sport_derived_from_video_type(self) -> None:
        face_result = _make_face_result(())
        ingestion = _make_ingestion()
        clip = _make_clip()
        config = _make_config(video_type="sports_tennis")

        with (
            patch("modules.strategies.sports_strategy._try_pose_centroid", return_value=None),
            patch("modules.strategies.sports_strategy._try_motion_energy", return_value=None),
        ):
            plan = generate_plan(clip, face_result, ingestion, config)

        assert plan.sport == "tennis"


# ---------------------------------------------------------------------------
# 5. Crop bounds within source dimensions
# ---------------------------------------------------------------------------


class TestCropBounds:
    def test_anchor_to_crop_rect_clamped(self) -> None:
        # Anchor at far right edge — crop_x should clamp so crop stays in bounds
        crop_x, crop_y, crop_w, crop_h = _anchor_to_crop_rect(0.99, 0.5, SRC_WIDTH, SRC_HEIGHT)
        assert crop_x >= 0
        assert crop_x + crop_w <= SRC_WIDTH

    def test_anchor_to_crop_rect_center(self) -> None:
        crop_x, crop_y, crop_w, crop_h = _anchor_to_crop_rect(0.5, 0.5, SRC_WIDTH, SRC_HEIGHT)
        # Expected crop width: SRC_HEIGHT * 9/16
        expected_w = int(round(SRC_HEIGHT * 9 / 16))
        assert crop_w == expected_w
        assert crop_h == SRC_HEIGHT

    def test_center_crop_rect_within_source(self) -> None:
        crop_x, crop_y, crop_w, crop_h = _center_crop_rect(SRC_WIDTH, SRC_HEIGHT)
        assert crop_x >= 0
        assert crop_y == 0
        assert crop_x + crop_w <= SRC_WIDTH
        assert crop_y + crop_h <= SRC_HEIGHT


# ---------------------------------------------------------------------------
# 6. Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_generate_plan_deterministic_center_fallback(self) -> None:
        face_result = _make_face_result(())
        ingestion = _make_ingestion()
        clip = _make_clip()
        config = _make_config()

        with (
            patch("modules.strategies.sports_strategy._try_pose_centroid", return_value=None),
            patch("modules.strategies.sports_strategy._try_motion_energy", return_value=None),
        ):
            plan1 = generate_plan(clip, face_result, ingestion, config)
            plan2 = generate_plan(clip, face_result, ingestion, config)

        assert plan1 == plan2

    def test_generate_plan_deterministic_face_centroid(self) -> None:
        bbox = _make_bbox(x=0.45)
        scene_data = (_make_scene_face_data(avg_bbox=bbox, visibility=0.85),)
        face_result = _make_face_result(scene_data)
        ingestion = _make_ingestion()
        clip = _make_clip()
        config = _make_config()

        plan1 = generate_plan(clip, face_result, ingestion, config)
        plan2 = generate_plan(clip, face_result, ingestion, config)

        assert plan1 == plan2


# ---------------------------------------------------------------------------
# 7. compose.process dispatch for sports types
# ---------------------------------------------------------------------------


class TestCompositorDispatch:
    def _make_composite(self) -> CompositeStream:
        return CompositeStream(
            clip_id=CLIP_ID,
            video_id=VIDEO_ID,
            composite_path="/tmp/composite.mp4",
            source_audio_path="/tmp/source.mp4",
            resolution=(1080, 1920),
            layout="sports_center_crop",
            duration_seconds=35.0,
            has_face=False,
            source_fps=30.0,
            start_time_ms=0,
        )

    def test_dispatch_sports_tennis(self) -> None:
        from modules.compositor.compose import process

        clip = _make_clip()
        ingestion = _make_ingestion()
        face_result = _make_face_result(())
        config = _make_config(video_type="sports_tennis")
        expected = self._make_composite()

        with patch("modules.compositor.sports_tennis.process_sports_tennis", return_value=expected) as mock:
            result = process(clip, face_result, ingestion, config)

        mock.assert_called_once_with(clip, face_result, ingestion, config, None)
        assert result is expected

    def test_dispatch_sports_football(self) -> None:
        from modules.compositor.compose import process

        clip = _make_clip()
        ingestion = _make_ingestion()
        face_result = _make_face_result(())
        config = _make_config(video_type="sports_football")
        expected = self._make_composite()

        with patch("modules.compositor.sports_football.process_sports_football", return_value=expected) as mock:
            result = process(clip, face_result, ingestion, config)

        mock.assert_called_once_with(clip, face_result, ingestion, config, None)
        assert result is expected

    def test_dispatch_sports_padel(self) -> None:
        from modules.compositor.compose import process

        clip = _make_clip()
        ingestion = _make_ingestion()
        face_result = _make_face_result(())
        config = _make_config(video_type="sports_padel")
        expected = self._make_composite()

        with patch("modules.compositor.sports_padel.process_sports_padel", return_value=expected) as mock:
            result = process(clip, face_result, ingestion, config)

        mock.assert_called_once_with(clip, face_result, ingestion, config, None)
        assert result is expected

    def test_gameplay_path_unaffected(self) -> None:
        """Sports dispatch must not run for gameplay video_type."""
        from modules.compositor.compose import process

        clip = _make_clip()
        ingestion = _make_ingestion()
        face_result = _make_face_result(())
        config = _make_config(video_type="gameplay")
        expected = self._make_composite()

        with (
            patch("modules.compositor.sports_tennis.process_sports_tennis") as tennis_mock,
            patch("modules.compositor.sports_football.process_sports_football") as football_mock,
            patch("modules.compositor.compose._get_output_path", return_value="/tmp/composite.mp4"),
            patch("os.path.exists", return_value=True),
        ):
            process(clip, face_result, ingestion, config)
            tennis_mock.assert_not_called()
            football_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Filter builders
# ---------------------------------------------------------------------------


class TestFilterBuilders:
    def test_letterbox_contains_pad_and_scale(self) -> None:
        vf = build_sports_letterbox_filter(1920, 1080)
        assert "scale=1080" in vf
        assert "pad=1080:1920" in vf
        assert "setsar=1" in vf

    def test_center_crop_contains_crop_and_scale(self) -> None:
        vf = build_sports_center_crop_filter(1920, 1080)
        assert "crop=" in vf
        assert "scale=1080:1920" in vf
        assert "setsar=1" in vf

    def test_action_crop_uses_plan_coordinates(self) -> None:
        plan = SportsFramePlan(
            layout="sports_action_crop",
            sport="football",
            tracking_method="center",
            crop_x=100,
            crop_y=0,
            crop_width=607,
            crop_height=1080,
        )
        vf = build_sports_action_crop_filter(1920, 1080, plan)
        assert "crop=607:1080:100:0" in vf
        assert "scale=1080:1920" in vf

    def test_letterbox_respects_custom_output_dimensions(self) -> None:
        vf = build_sports_letterbox_filter(1920, 1080, out_width=720, out_height=1280)
        assert "scale=720" in vf
        assert "pad=720:1280" in vf

    def test_center_crop_respects_custom_output_dimensions(self) -> None:
        vf = build_sports_center_crop_filter(1920, 1080, out_width=720, out_height=1280)
        assert "scale=720:1280" in vf

    def test_action_crop_respects_custom_output_dimensions(self) -> None:
        plan = _make_sports_plan()
        vf = build_sports_action_crop_filter(1920, 1080, plan, out_width=720, out_height=1280)
        assert "scale=720:1280" in vf


# ---------------------------------------------------------------------------
# 9. source_fps uses pipeline output_framerate not ingestion fps
# ---------------------------------------------------------------------------


class TestSourceFpsContract:
    def test_source_fps_uses_pipeline_framerate_not_ingestion(self) -> None:
        from modules.compositor.sports_utils import process_sports

        clip = _make_clip()
        # ingestion has fps=60 (source), pipeline output is 30
        ingestion = _make_ingestion(fps=60.0)
        face_result = _make_face_result(())
        plan = _make_sports_plan()
        config = _make_config(out_fps=30)

        with (
            patch("modules.compositor.sports_utils._get_output_path", return_value="/tmp/c.mp4"),
            patch("os.path.exists", return_value=True),
        ):
            result = process_sports(clip, face_result, ingestion, config, plan, "football", "sports_action_crop")

        # CompositeStream.source_fps must be 30.0 (pipeline output FPS), not 60.0 (source FPS)
        assert result.source_fps == 30.0
        assert result.source_fps != 60.0


# ---------------------------------------------------------------------------
# 10. output_width/height read from compositor config
# ---------------------------------------------------------------------------


class TestOutputDimensionsFromConfig:
    def test_resolution_uses_compositor_config_values(self) -> None:
        from modules.compositor.sports_utils import process_sports

        clip = _make_clip()
        ingestion = _make_ingestion()
        face_result = _make_face_result(())
        plan = _make_sports_plan()
        config = {
            "video_type": "sports_football",
            "pipeline": {"output_framerate": 30, "ffmpeg_timeout": 60},
            "compositor": {
                "default_layout": "sports_center_crop",
                "output_width": 720,
                "output_height": 1280,
            },
        }

        with (
            patch("modules.compositor.sports_utils._get_output_path", return_value="/tmp/c.mp4"),
            patch("os.path.exists", return_value=True),
        ):
            result = process_sports(clip, face_result, ingestion, config, plan, "football", "sports_action_crop")

        assert result.resolution == (720, 1280)

    def test_resolution_falls_back_to_default_when_not_in_config(self) -> None:
        from modules.compositor.sports_utils import process_sports

        clip = _make_clip()
        ingestion = _make_ingestion()
        face_result = _make_face_result(())
        plan = _make_sports_plan()
        config = {
            "video_type": "sports_football",
            "pipeline": {"output_framerate": 30, "ffmpeg_timeout": 60},
            "compositor": {"default_layout": "sports_center_crop"},
        }

        with (
            patch("modules.compositor.sports_utils._get_output_path", return_value="/tmp/c.mp4"),
            patch("os.path.exists", return_value=True),
        ):
            result = process_sports(clip, face_result, ingestion, config, plan, "football", "sports_action_crop")

        assert result.resolution == (1080, 1920)
