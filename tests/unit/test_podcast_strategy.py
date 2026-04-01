"""Unit tests for modules/strategies/podcast_strategy.py.

Tests cover:
  1. Speaker detection — correct face selected when speaking
  2. Determinism — same inputs → same plan every time
  3. Silent speaker — non-speaking face not selected over speaking face
  4. Multi-speaker — primary speaker chosen by highest weighted score
  5. No transcript fallback — largest face selected (area rank)
  6. No face fallback — center crop returned
  7. Gameplay regression — strategy is never called for gameplay (isolation)
  8. PodcastFramePlan DTO — frozen, correct field types
  9. Crop bounds — produced crop_x/y/width/height are within source dimensions
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from contracts.clip import ClipDefinition
from contracts.face import FaceBBox, FaceDetectionResult, SceneFaceData
from contracts.ingestion import IngestionResult
from contracts.scoring import ScoredScene
from contracts.strategies import PodcastFramePlan
from contracts.transcript import Transcript, TranscriptSegment, Word

# Internal helpers exercised directly in unit tests
from modules.strategies.podcast_strategy import (
    _build_time_buckets,
    _cluster_faces,
    _collect_clip_bboxes,
    _face_presence_per_bucket,
    _median_bbox,
    _select_primary_speaker,
    _text_activity_per_bucket,
    generate_plan,
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
    x: float,
    y: float,
    width: float = 0.12,
    height: float = 0.20,
    ts_ms: int = 0,
    confidence: float = 0.9,
) -> FaceBBox:
    return FaceBBox(
        x=x, y=y, width=width, height=height,
        confidence=confidence, timestamp_ms=ts_ms,
    )


def _make_scored_scene(
    start_ms: int = 0,
    end_ms: int = 35000,
) -> ScoredScene:
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
    bboxes: tuple[FaceBBox, ...] = (),
    avg_bbox: FaceBBox | None = None,
) -> SceneFaceData:
    visibility = 0.8 if bboxes else 0.0
    return SceneFaceData(
        scene_id=f"{VIDEO_ID}_{start_ms}_{end_ms}",
        face_visible_ratio=visibility,
        bounding_boxes=bboxes,
        average_bbox=avg_bbox,
        sample_count=max(len(bboxes), 1),
    )


def _make_face_result(scene_data: tuple[SceneFaceData, ...]) -> FaceDetectionResult:
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


def _make_ingestion(width: int = SRC_WIDTH, height: int = SRC_HEIGHT) -> IngestionResult:
    return IngestionResult(
        video_id=VIDEO_ID,
        path="/tmp/podcast_test.mp4",
        duration_seconds=120.0,
        resolution=(width, height),
        fps=30.0,
        codec="h264",
        has_audio=True,
        file_size_bytes=50_000_000,
        audio_codec="aac",
    )


def _make_transcript(segments: list[tuple[int, int, str]]) -> Transcript:
    """Build a Transcript from (start_ms, end_ms, text) tuples."""
    segs = tuple(
        TranscriptSegment(
            text=text,
            start_time=start,
            end_time=end,
            words=(),
            confidence=0.9,
        )
        for start, end, text in segments
    )
    total_words = sum(len(s.text.split()) for s in segs)
    return Transcript(
        video_id=VIDEO_ID,
        segments=segs,
        total_words=total_words,
        language="en",
    )


def _strategy_config() -> dict:
    return {
        "podcast_strategy": {
            "window_seconds": 1.0,
            "text_weight": 0.7,
            "face_weight": 0.3,
            "cluster_threshold": 0.20,
            "bbox_expand_scale": 1.4,
        }
    }


# ---------------------------------------------------------------------------
# 1. Time bucket building
# ---------------------------------------------------------------------------


class TestBuildTimeBuckets:
    def test_exact_division(self):
        buckets = _build_time_buckets(0, 3000, 1000)
        assert buckets == [(0, 1000), (1000, 2000), (2000, 3000)]

    def test_partial_last_bucket(self):
        buckets = _build_time_buckets(0, 2500, 1000)
        assert buckets == [(0, 1000), (1000, 2000), (2000, 2500)]

    def test_empty_range(self):
        buckets = _build_time_buckets(5000, 5000, 1000)
        assert buckets == []

    def test_single_bucket(self):
        buckets = _build_time_buckets(1000, 1800, 1000)
        assert buckets == [(1000, 1800)]


# ---------------------------------------------------------------------------
# 2. Text activity scoring
# ---------------------------------------------------------------------------


class TestTextActivityPerBucket:
    def test_segment_fully_in_bucket(self):
        transcript = _make_transcript([(0, 1000, "hello world")])
        buckets = [(0, 1000)]
        scores = _text_activity_per_bucket(transcript, buckets)
        assert scores == [len("hello world")]

    def test_segment_split_across_buckets(self):
        # 2000ms segment, 50% in each bucket
        transcript = _make_transcript([(0, 2000, "ab")])
        buckets = [(0, 1000), (1000, 2000)]
        scores = _text_activity_per_bucket(transcript, buckets)
        assert scores[0] == 1  # round(2 * 0.5)
        assert scores[1] == 1

    def test_no_speech_bucket_scores_zero(self):
        transcript = _make_transcript([])
        buckets = [(0, 1000), (1000, 2000)]
        scores = _text_activity_per_bucket(transcript, buckets)
        assert scores == [0, 0]

    def test_segment_outside_bucket_ignored(self):
        transcript = _make_transcript([(5000, 6000, "outside")])
        buckets = [(0, 1000)]
        scores = _text_activity_per_bucket(transcript, buckets)
        assert scores == [0]


# ---------------------------------------------------------------------------
# 3. Face clustering
# ---------------------------------------------------------------------------


class TestClusterFaces:
    def test_empty_returns_empty(self):
        assert _cluster_faces([], 0.20) == {}

    def test_single_bbox_single_cluster(self):
        bbox = _make_bbox(0.5, 0.5)
        clusters = _cluster_faces([bbox], 0.20)
        assert len(clusters) == 1
        assert "face_0" in clusters

    def test_two_close_bboxes_merge(self):
        b1 = _make_bbox(0.5, 0.5)
        b2 = _make_bbox(0.51, 0.51)  # distance ≈ 0.014 < threshold 0.20
        clusters = _cluster_faces([b1, b2], 0.20)
        assert len(clusters) == 1

    def test_two_distant_bboxes_separate(self):
        b_left = _make_bbox(0.1, 0.5)   # left face
        b_right = _make_bbox(0.8, 0.5)  # right face (distance = 0.7 > 0.20)
        clusters = _cluster_faces([b_left, b_right], 0.20)
        assert len(clusters) == 2
        # face_0 should be the left-most cluster
        assert clusters["face_0"][0].x < clusters["face_1"][0].x

    def test_ids_assigned_left_to_right(self):
        bl = _make_bbox(0.1, 0.5)
        br = _make_bbox(0.9, 0.5)
        clusters = _cluster_faces([bl, br], 0.20)
        # face_0 is leftmost
        f0_cx = clusters["face_0"][0].x + clusters["face_0"][0].width / 2
        f1_cx = clusters["face_1"][0].x + clusters["face_1"][0].width / 2
        assert f0_cx < f1_cx

    def test_deterministic_same_output(self):
        bboxes = [
            _make_bbox(0.1, 0.5, ts_ms=0),
            _make_bbox(0.9, 0.5, ts_ms=500),
            _make_bbox(0.11, 0.5, ts_ms=1000),
            _make_bbox(0.89, 0.5, ts_ms=1500),
        ]
        c1 = _cluster_faces(bboxes, 0.20)
        c2 = _cluster_faces(bboxes, 0.20)
        assert list(c1.keys()) == list(c2.keys())
        for fid in c1:
            assert [b.x for b in c1[fid]] == [b.x for b in c2[fid]]


# ---------------------------------------------------------------------------
# 4. Face presence per bucket
# ---------------------------------------------------------------------------


class TestFacePresencePerBucket:
    def test_bbox_in_correct_bucket(self):
        bbox = _make_bbox(0.5, 0.5, ts_ms=1500)
        clusters = {"face_0": [bbox]}
        buckets = [(0, 1000), (1000, 2000), (2000, 3000)]
        presence = _face_presence_per_bucket(clusters, buckets)
        assert presence["face_0"] == [0, 1, 0]

    def test_bbox_in_first_bucket(self):
        bbox = _make_bbox(0.5, 0.5, ts_ms=0)
        clusters = {"face_0": [bbox]}
        buckets = [(0, 1000), (1000, 2000)]
        presence = _face_presence_per_bucket(clusters, buckets)
        assert presence["face_0"] == [1, 0]

    def test_bbox_after_last_bucket_ignored(self):
        bbox = _make_bbox(0.5, 0.5, ts_ms=9000)
        clusters = {"face_0": [bbox]}
        buckets = [(0, 1000), (1000, 2000)]
        presence = _face_presence_per_bucket(clusters, buckets)
        assert presence["face_0"] == [0, 0]


# ---------------------------------------------------------------------------
# 5. Primary speaker selection
# ---------------------------------------------------------------------------


class TestSelectPrimarySpeaker:
    def test_speaking_face_wins(self):
        # face_0 is present during high text activity
        text_scores = [0, 100, 100, 0]
        face_presence = {
            "face_0": [0, 2, 2, 0],   # present during speech
            "face_1": [2, 0, 0, 2],   # present during silence
        }
        winner = _select_primary_speaker(text_scores, face_presence, 0.7, 0.3)
        assert winner == "face_0"

    def test_no_faces_returns_none(self):
        result = _select_primary_speaker([100, 100], {}, 0.7, 0.3)
        assert result is None

    def test_silent_clip_selects_most_visible(self):
        # No speech → normalised text = 0 everywhere → pure face presence
        text_scores = [0, 0, 0]
        face_presence = {
            "face_0": [0, 1, 0],   # 1 frame
            "face_1": [2, 2, 2],   # 6 frames — wins
        }
        winner = _select_primary_speaker(text_scores, face_presence, 0.7, 0.3)
        assert winner == "face_1"

    def test_tiebreak_by_lower_face_index(self):
        text_scores = [0]
        face_presence = {
            "face_0": [1],
            "face_1": [1],
        }
        winner = _select_primary_speaker(text_scores, face_presence, 0.7, 0.3)
        assert winner == "face_0"

    def test_deterministic_two_calls(self):
        text_scores = [50, 80, 30]
        face_presence = {
            "face_0": [1, 2, 0],
            "face_1": [2, 1, 2],
        }
        w1 = _select_primary_speaker(text_scores, face_presence, 0.7, 0.3)
        w2 = _select_primary_speaker(text_scores, face_presence, 0.7, 0.3)
        assert w1 == w2


# ---------------------------------------------------------------------------
# 6. Median bbox
# ---------------------------------------------------------------------------


class TestMedianBbox:
    def test_single_bbox(self):
        bbox = _make_bbox(0.3, 0.4, width=0.15, height=0.25)
        med = _median_bbox([bbox])
        assert med.x == 0.3
        assert med.y == 0.4

    def test_odd_count_exact_median(self):
        bboxes = [
            _make_bbox(0.1, 0.1),
            _make_bbox(0.5, 0.5),
            _make_bbox(0.9, 0.9),
        ]
        med = _median_bbox(bboxes)
        assert med.x == 0.5
        assert med.y == 0.5

    def test_even_count_lower_middle(self):
        bboxes = [_make_bbox(0.1, 0.1), _make_bbox(0.9, 0.9)]
        # mid = (2-1)//2 = 0, so lower middle element
        med = _median_bbox(bboxes)
        assert med.x == 0.1

    def test_synthetic_confidence_is_zero(self):
        bboxes = [_make_bbox(0.5, 0.5, confidence=0.9)]
        med = _median_bbox(bboxes)
        assert med.confidence == 0.0


# ---------------------------------------------------------------------------
# 7. generate_plan — integration (no FFmpeg, pure calculation)
# ---------------------------------------------------------------------------


class TestGeneratePlan:
    """Integration tests for generate_plan covering all decision paths."""

    def test_no_face_returns_center_crop(self):
        clip = _make_clip()
        face_result = _make_face_result(
            (_make_scene_face_data(bboxes=(), avg_bbox=None),)
        )
        ingestion = _make_ingestion()
        plan = generate_plan(clip, None, face_result, ingestion, _strategy_config())
        assert plan.layout == "center_crop"
        assert plan.speaker_face_id is None

    def test_no_transcript_returns_center_face_crop(self):
        bbox = _make_bbox(0.4, 0.3, ts_ms=500)
        scene_data = _make_scene_face_data(bboxes=(bbox,), avg_bbox=bbox)
        face_result = _make_face_result((scene_data,))
        clip = _make_clip()
        ingestion = _make_ingestion()
        plan = generate_plan(clip, None, face_result, ingestion, _strategy_config())
        assert plan.layout == "center_face_crop"
        assert plan.speaker_face_id == "face_0"

    def test_empty_transcript_returns_center_face_crop(self):
        bbox = _make_bbox(0.4, 0.3, ts_ms=500)
        scene_data = _make_scene_face_data(bboxes=(bbox,), avg_bbox=bbox)
        face_result = _make_face_result((scene_data,))
        clip = _make_clip()
        ingestion = _make_ingestion()
        empty_transcript = Transcript(
            video_id=VIDEO_ID, segments=(), total_words=0, language="en"
        )
        plan = generate_plan(
            clip, empty_transcript, face_result, ingestion, _strategy_config()
        )
        assert plan.layout == "center_face_crop"

    def test_speaker_detection_selects_speaking_face(self):
        """face_0 (left) speaks. face_1 (right) is silent. face_0 must win."""
        clip = _make_clip(start_ms=0, end_ms=4000)

        # face_0 is visible during speech buckets (1000-3000ms)
        # face_1 is visible during silence buckets (0-1000ms and 3000-4000ms)
        bboxes_speaking = tuple(
            _make_bbox(0.1, 0.3, ts_ms=ms)
            for ms in [1000, 1500, 2000, 2500]
        )
        bboxes_silent = tuple(
            _make_bbox(0.7, 0.3, ts_ms=ms)
            for ms in [0, 500, 3000, 3500]
        )
        all_bboxes = bboxes_speaking + bboxes_silent
        scene_data = _make_scene_face_data(start_ms=0, end_ms=4000, bboxes=all_bboxes, avg_bbox=all_bboxes[0])
        face_result = _make_face_result((scene_data,))

        # Transcript covers 1000-3000ms (same as face_0 presence)
        transcript = _make_transcript([(1000, 3000, "hello world how are you doing")])

        ingestion = _make_ingestion()
        plan = generate_plan(clip, transcript, face_result, ingestion, _strategy_config())

        assert plan.layout == "speaker_crop"
        # face_0 is left-most → face_0
        assert plan.speaker_face_id == "face_0"

    def test_determinism_same_inputs_same_plan(self):
        """Running generate_plan twice with identical inputs must produce identical plans."""
        bbox = _make_bbox(0.4, 0.3, ts_ms=500)
        scene_data = _make_scene_face_data(bboxes=(bbox,), avg_bbox=bbox)
        face_result = _make_face_result((scene_data,))
        clip = _make_clip()
        ingestion = _make_ingestion()
        transcript = _make_transcript([(0, 1000, "test sentence")])

        plan1 = generate_plan(clip, transcript, face_result, ingestion, _strategy_config())
        plan2 = generate_plan(clip, transcript, face_result, ingestion, _strategy_config())

        assert plan1 == plan2

    def test_crop_bounds_within_source(self):
        """Crop rectangle must fit inside source dimensions."""
        bbox = _make_bbox(0.5, 0.3, ts_ms=0)
        scene_data = _make_scene_face_data(bboxes=(bbox,), avg_bbox=bbox)
        face_result = _make_face_result((scene_data,))
        clip = _make_clip()
        ingestion = _make_ingestion(width=SRC_WIDTH, height=SRC_HEIGHT)
        transcript = _make_transcript([(0, 5000, "speaking content")])

        plan = generate_plan(clip, transcript, face_result, ingestion, _strategy_config())

        assert plan.crop_x >= 0
        assert plan.crop_y >= 0
        assert plan.crop_x + plan.crop_width <= SRC_WIDTH
        assert plan.crop_y + plan.crop_height <= SRC_HEIGHT
        assert plan.crop_width > 0
        assert plan.crop_height > 0

    def test_multi_speaker_primary_selected_by_score(self):
        """When two speakers talk, the one with the most speaking time wins."""
        clip = _make_clip(start_ms=0, end_ms=6000)

        # Speaker A (left, x=0.1): visible 0-3000ms
        # Speaker B (right, x=0.8): visible 3000-6000ms
        bboxes_a = tuple(
            _make_bbox(0.1, 0.3, ts_ms=ms)
            for ms in [0, 500, 1000, 1500, 2000, 2500]
        )
        bboxes_b = tuple(
            _make_bbox(0.8, 0.3, ts_ms=ms)
            for ms in [3000, 3500, 4000, 4500, 5000, 5500]
        )
        all_bboxes = bboxes_a + bboxes_b
        scene_data = _make_scene_face_data(start_ms=0, end_ms=6000, bboxes=all_bboxes, avg_bbox=all_bboxes[0])
        face_result = _make_face_result((scene_data,))

        # More speech in speaker A's time window (0-3000ms = 4x more chars)
        transcript = _make_transcript([
            (0, 3000, "a" * 200),      # speaker A window, high text activity
            (3000, 6000, "a" * 50),    # speaker B window, lower text activity
        ])

        ingestion = _make_ingestion()
        plan = generate_plan(clip, transcript, face_result, ingestion, _strategy_config())

        assert plan.layout == "speaker_crop"
        # Speaker A should win (face_0 = left-most)
        assert plan.speaker_face_id == "face_0"

    def test_silent_speaker_ignored(self):
        """A face that is never visible during speech should not be selected."""
        clip = _make_clip(start_ms=0, end_ms=4000)

        # face_0 (left, x=0.1): visible ONLY during silence (3000-4000ms)
        # face_1 (right, x=0.8): visible ONLY during speech (0-2000ms)
        bboxes_silent = tuple(
            _make_bbox(0.1, 0.3, ts_ms=ms)
            for ms in [3000, 3500]
        )
        bboxes_speaking = tuple(
            _make_bbox(0.8, 0.3, ts_ms=ms)
            for ms in [0, 500, 1000, 1500]
        )
        all_bboxes = bboxes_silent + bboxes_speaking
        scene_data = _make_scene_face_data(start_ms=0, end_ms=4000, bboxes=all_bboxes, avg_bbox=all_bboxes[0])
        face_result = _make_face_result((scene_data,))

        # Speech only in 0-2000ms window
        transcript = _make_transcript([(0, 2000, "speaking here actively")])

        ingestion = _make_ingestion()
        plan = generate_plan(clip, transcript, face_result, ingestion, _strategy_config())

        assert plan.layout == "speaker_crop"
        # face_0 (left/silent) should NOT win; face_1 (right/speaking) must win
        assert plan.speaker_face_id == "face_1"


# ---------------------------------------------------------------------------
# 8. PodcastFramePlan DTO contract
# ---------------------------------------------------------------------------


class TestPodcastFramePlanDTO:
    def test_is_frozen(self):
        plan = PodcastFramePlan(
            crop_x=0, crop_y=0, crop_width=608, crop_height=1080,
            speaker_face_id="face_0", layout="speaker_crop",
        )
        with pytest.raises((AttributeError, TypeError)):
            plan.crop_x = 99  # type: ignore[misc]

    def test_equality_for_same_values(self):
        p1 = PodcastFramePlan(0, 0, 608, 1080, "face_0", "speaker_crop")
        p2 = PodcastFramePlan(0, 0, 608, 1080, "face_0", "speaker_crop")
        assert p1 == p2

    def test_none_speaker_face_id_allowed(self):
        plan = PodcastFramePlan(0, 0, 608, 1080, None, "center_crop")
        assert plan.speaker_face_id is None


# ---------------------------------------------------------------------------
# 9. Gameplay path isolation
# ---------------------------------------------------------------------------


class TestGameplayIsolation:
    """The strategy module must never be invoked for gameplay video types."""

    def test_strategy_not_called_for_gameplay(self):
        """compose.process() with video_type='gameplay' must NOT call generate_plan."""
        from modules.compositor.compose import process as comp_process
        from unittest.mock import MagicMock, patch

        clip = _make_clip()
        scene_id = f"{VIDEO_ID}_0_35000"
        scene_data = SceneFaceData(
            scene_id=scene_id,
            face_visible_ratio=0.8,
            bounding_boxes=(_make_bbox(0.3, 0.1, width=0.4, height=0.6),),
            average_bbox=_make_bbox(0.3, 0.1, width=0.4, height=0.6),
            sample_count=5,
        )
        face_result = _make_face_result((scene_data,))
        ingestion = _make_ingestion()

        gameplay_config = {
            "video_type": "gameplay",
            "paths": {"output_dir": "/tmp/test_out"},
            "pipeline": {"output_framerate": 30, "ffmpeg_timeout": 300},
            "compositor": {
                "default_layout": "split",
                "face_region": "auto",
                "face_zoom_factor": 1.0,
            },
            "_runtime": {"video_dir_name": "test"},
            "gpu": {"enabled": False},
        }

        # Pre-create the output file so FFmpeg is NOT invoked
        import os, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            gameplay_config["paths"]["output_dir"] = tmp
            # create composite file to trigger idempotency early-return
            out_dir = os.path.join(tmp, "test", "clips", "shorts-1")
            os.makedirs(out_dir, exist_ok=True)
            composite_path = os.path.join(out_dir, "composite.mp4")
            open(composite_path, "w").close()

            with patch(
                "modules.strategies.podcast_strategy.generate_plan"
            ) as mock_generate:
                comp_process(clip, face_result, ingestion, gameplay_config)
                mock_generate.assert_not_called()
