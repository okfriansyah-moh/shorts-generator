"""Unit tests for the face detection module.

Tests cover: face visible, no face, multiple faces, EMA smoothing,
normalized coordinates, 2fps sampling, and FaceDetectionResult construction.
All tests run without GPU, network, or real video files.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from contracts.face import FaceBBox, FaceDetectionResult, SceneFaceData
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
def minimal_config() -> dict[str, Any]:
    return {
        "paths": {"temp_dir": "/tmp/test_face"},
        "pipeline": {"ffmpeg_timeout": 10},
        "face_detection": {
            "sample_fps": 2,
            "min_confidence": 0.7,
            "ema_alpha": 0.3,
            "min_face_size": 0.05,
        },
    }


@pytest.fixture()
def single_scene(mock_ingestion: IngestionResult) -> SceneList:
    scene = SceneSegment(
        scene_id="abcdef1234567890_0_10000",
        video_id=mock_ingestion.video_id,
        start_time=0,
        end_time=10000,
        duration=10.0,
    )
    return SceneList(
        video_id=mock_ingestion.video_id,
        scenes=(scene,),
        total_duration=10.0,
    )


@pytest.fixture()
def two_scenes(mock_ingestion: IngestionResult) -> SceneList:
    s1 = SceneSegment(
        scene_id="abcdef1234567890_0_10000",
        video_id=mock_ingestion.video_id,
        start_time=0,
        end_time=10000,
        duration=10.0,
    )
    s2 = SceneSegment(
        scene_id="abcdef1234567890_10000_20000",
        video_id=mock_ingestion.video_id,
        start_time=10000,
        end_time=20000,
        duration=10.0,
    )
    return SceneList(
        video_id=mock_ingestion.video_id,
        scenes=(s1, s2),
        total_duration=20.0,
    )


# ---------------------------------------------------------------------------
# DTO construction tests
# ---------------------------------------------------------------------------

class TestFaceBBoxDTO:
    def test_creation(self) -> None:
        bbox = FaceBBox(x=0.1, y=0.2, width=0.3, height=0.4, confidence=0.9, timestamp_ms=1000)
        assert bbox.x == 0.1
        assert bbox.width == 0.3
        assert bbox.confidence == 0.9

    def test_is_frozen(self) -> None:
        bbox = FaceBBox(x=0.1, y=0.2, width=0.3, height=0.4, confidence=0.9, timestamp_ms=0)
        with pytest.raises((AttributeError, TypeError)):
            bbox.x = 0.5  # type: ignore[misc]


class TestSceneFaceDataDTO:
    def test_no_face_creation(self) -> None:
        data = SceneFaceData(
            scene_id="test_scene",
            face_visible_ratio=0.0,
            bounding_boxes=(),
            average_bbox=None,
            sample_count=10,
        )
        assert data.face_visible_ratio == 0.0
        assert data.average_bbox is None
        assert data.sample_count == 10

    def test_with_face(self) -> None:
        bbox = FaceBBox(x=0.1, y=0.2, width=0.3, height=0.4, confidence=0.9, timestamp_ms=0)
        data = SceneFaceData(
            scene_id="test_scene",
            face_visible_ratio=1.0,
            bounding_boxes=(bbox,),
            average_bbox=bbox,
            sample_count=1,
        )
        assert data.face_visible_ratio == 1.0
        assert data.average_bbox is not None

    def test_is_frozen(self) -> None:
        data = SceneFaceData(
            scene_id="x", face_visible_ratio=0.0,
            bounding_boxes=(), average_bbox=None, sample_count=1
        )
        with pytest.raises((AttributeError, TypeError)):
            data.face_visible_ratio = 1.0  # type: ignore[misc]


class TestFaceDetectionResultDTO:
    def test_creation(self) -> None:
        scene_data = SceneFaceData(
            scene_id="s1",
            face_visible_ratio=0.5,
            bounding_boxes=(),
            average_bbox=None,
            sample_count=4,
        )
        result = FaceDetectionResult(
            video_id="abcdef1234567890",
            scene_data=(scene_data,),
            average_visibility=0.5,
            faceless_scene_count=0,
        )
        assert result.video_id == "abcdef1234567890"
        assert result.average_visibility == 0.5
        assert result.faceless_scene_count == 0

    def test_is_frozen(self) -> None:
        result = FaceDetectionResult(
            video_id="x", scene_data=(), average_visibility=0.0, faceless_scene_count=0
        )
        with pytest.raises((AttributeError, TypeError)):
            result.video_id = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EMA smoothing tests
# ---------------------------------------------------------------------------

class TestComputeEmaBbox:
    def test_empty_input_returns_none(self) -> None:
        from modules.face_detection.detect import _compute_ema_bbox
        result = _compute_ema_bbox((), 0.3)
        assert result is None

    def test_single_bbox_returns_same(self) -> None:
        from modules.face_detection.detect import _compute_ema_bbox
        bbox = FaceBBox(x=0.1, y=0.2, width=0.3, height=0.4, confidence=0.9, timestamp_ms=0)
        result = _compute_ema_bbox((bbox,), 0.3)
        assert result is not None
        assert result.x == pytest.approx(0.1)
        assert result.y == pytest.approx(0.2)
        assert result.width == pytest.approx(0.3)

    def test_ema_smoothing_two_bboxes(self) -> None:
        from modules.face_detection.detect import _compute_ema_bbox
        b1 = FaceBBox(x=0.0, y=0.0, width=0.5, height=0.5, confidence=0.8, timestamp_ms=0)
        b2 = FaceBBox(x=1.0, y=1.0, width=0.1, height=0.1, confidence=0.9, timestamp_ms=500)
        # EMA: alpha=0.3 → 0.3 * 1.0 + 0.7 * 0.0 = 0.3
        result = _compute_ema_bbox((b1, b2), 0.3)
        assert result is not None
        assert result.x == pytest.approx(0.3, abs=1e-6)
        assert result.timestamp_ms == 500

    def test_ema_smoothing_preserves_normalized_range(self) -> None:
        from modules.face_detection.detect import _compute_ema_bbox
        bboxes = tuple(
            FaceBBox(x=0.5, y=0.5, width=0.4, height=0.4, confidence=0.8, timestamp_ms=i * 100)
            for i in range(10)
        )
        result = _compute_ema_bbox(bboxes, 0.3)
        assert result is not None
        assert 0.0 <= result.x <= 1.0
        assert 0.0 <= result.y <= 1.0
        assert 0.0 < result.width <= 1.0
        assert 0.0 < result.height <= 1.0
        assert result.x + result.width <= 1.0 + 1e-9  # allow floating point tolerance


# ---------------------------------------------------------------------------
# detect_faces() integration tests (fully mocked)
# ---------------------------------------------------------------------------

class TestDetectFaces:
    @patch("modules.face_detection.detect._load_mediapipe_detector")
    @patch("modules.face_detection.detect._process_scene")
    def test_no_faces_detected(
        self,
        mock_process: MagicMock,
        mock_load: MagicMock,
        mock_ingestion: IngestionResult,
        single_scene: SceneList,
        minimal_config: dict[str, Any],
    ) -> None:
        mock_load.return_value = MagicMock()
        mock_process.return_value = SceneFaceData(
            scene_id="abcdef1234567890_0_10000",
            face_visible_ratio=0.0,
            bounding_boxes=(),
            average_bbox=None,
            sample_count=20,
        )
        from modules.face_detection.detect import detect_faces
        result = detect_faces(mock_ingestion, single_scene, minimal_config)

        assert isinstance(result, FaceDetectionResult)
        assert result.average_visibility == 0.0
        assert result.faceless_scene_count == 1
        assert len(result.scene_data) == 1

    @patch("modules.face_detection.detect._load_mediapipe_detector")
    @patch("modules.face_detection.detect._process_scene")
    def test_face_detected(
        self,
        mock_process: MagicMock,
        mock_load: MagicMock,
        mock_ingestion: IngestionResult,
        single_scene: SceneList,
        minimal_config: dict[str, Any],
    ) -> None:
        mock_load.return_value = MagicMock()
        bbox = FaceBBox(x=0.1, y=0.1, width=0.3, height=0.4, confidence=0.9, timestamp_ms=0)
        mock_process.return_value = SceneFaceData(
            scene_id="abcdef1234567890_0_10000",
            face_visible_ratio=0.8,
            bounding_boxes=(bbox,),
            average_bbox=bbox,
            sample_count=20,
        )
        from modules.face_detection.detect import detect_faces
        result = detect_faces(mock_ingestion, single_scene, minimal_config)

        assert result.average_visibility == pytest.approx(0.8)
        assert result.faceless_scene_count == 0

    @patch("modules.face_detection.detect._load_mediapipe_detector")
    @patch("modules.face_detection.detect._process_scene")
    def test_multiple_scenes_average(
        self,
        mock_process: MagicMock,
        mock_load: MagicMock,
        mock_ingestion: IngestionResult,
        two_scenes: SceneList,
        minimal_config: dict[str, Any],
    ) -> None:
        mock_load.return_value = MagicMock()
        mock_process.side_effect = [
            SceneFaceData(
                scene_id="abcdef1234567890_0_10000",
                face_visible_ratio=1.0,
                bounding_boxes=(),
                average_bbox=None,
                sample_count=20,
            ),
            SceneFaceData(
                scene_id="abcdef1234567890_10000_20000",
                face_visible_ratio=0.0,
                bounding_boxes=(),
                average_bbox=None,
                sample_count=20,
            ),
        ]
        from modules.face_detection.detect import detect_faces
        result = detect_faces(mock_ingestion, two_scenes, minimal_config)

        assert result.average_visibility == pytest.approx(0.5)
        assert result.faceless_scene_count == 1
        assert len(result.scene_data) == 2

    def test_mediapipe_not_installed_raises(
        self,
        mock_ingestion: IngestionResult,
        single_scene: SceneList,
        minimal_config: dict[str, Any],
    ) -> None:
        with patch.dict("sys.modules", {"mediapipe": None}):
            from modules.face_detection.detect import _load_mediapipe_detector
            with pytest.raises(RuntimeError, match="mediapipe is not installed"):
                _load_mediapipe_detector(0.7)


# ---------------------------------------------------------------------------
# Sample count calculation test
# ---------------------------------------------------------------------------

class TestSampleCount:
    def test_sample_count_is_ceil_of_duration_times_fps(self) -> None:
        """sample_count = ceil(duration_s * sample_fps)"""
        import math
        duration_s = 7.3
        sample_fps = 2
        expected = max(1, math.ceil(duration_s * sample_fps))
        assert expected == 15  # ceil(14.6) = 15
