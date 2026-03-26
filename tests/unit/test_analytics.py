"""Unit tests for the analytics module (Phase 10).

Tests are runnable without GPU, network access, or real video files.
All inputs are constructed from frozen DTO fixtures.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from contracts.analytics import PipelineReport, PublishReport, QualityMetrics
from contracts.clip import ClipDefinition, ClipList
from contracts.scoring import ScoredScene, ScoredSceneList
from contracts.storage import StorageRecord
from modules.analytics import process
from modules.analytics.publish_report import compute as compute_publish_report
from modules.analytics.quality_metrics import compute as compute_quality_metrics


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_VIDEO_ID = "a1b2c3d4e5f67890"
_RUN_ID = "run-001"

_DEFAULT_CONFIG: dict = {
    "scoring": {"min_composite_score": 0.2},
    "scheduler": {"posts_per_day": 1},
}


def _make_scored_scene(
    scene_index: int,
    composite_score: float = 0.5,
    face_presence_score: float = 0.6,
) -> ScoredScene:
    start = scene_index * 10_000
    end = start + 10_000
    return ScoredScene(
        scene_id=f"{_VIDEO_ID}_{start}_{end}",
        video_id=_VIDEO_ID,
        start_time=start,
        end_time=end,
        duration=10.0,
        keyword_score=0.5,
        audio_energy_score=0.5,
        face_presence_score=face_presence_score,
        scene_activity_score=0.5,
        sentence_density_score=0.5,
        composite_score=composite_score,
        rank=scene_index + 1,
    )


def _make_scored_scene_list(
    scenes: list[ScoredScene] | None = None,
) -> ScoredSceneList:
    if scenes is None:
        scenes = [_make_scored_scene(i) for i in range(3)]
    sorted_scenes = tuple(sorted(scenes, key=lambda s: s.start_time))
    scores = [s.composite_score for s in sorted_scenes]
    return ScoredSceneList(
        video_id=_VIDEO_ID,
        scenes=sorted_scenes,
        min_score=min(scores) if scores else 0.0,
        max_score=max(scores) if scores else 0.0,
        avg_score=sum(scores) / len(scores) if scores else 0.0,
    )


def _make_clip(
    clip_index: int,
    duration: float = 40.0,
    average_score: float = 0.5,
) -> ClipDefinition:
    start = clip_index * 40_000
    end = start + int(duration * 1000)
    scene = _make_scored_scene(clip_index, composite_score=average_score)
    return ClipDefinition(
        clip_id=f"clip{clip_index:016d}"[:16],
        video_id=_VIDEO_ID,
        scenes=(scene,),
        start_time=start,
        end_time=end,
        duration=duration,
        average_score=average_score,
        clip_index=clip_index,
    )


def _make_clip_list(clips: list[ClipDefinition] | None = None) -> ClipList:
    if clips is None:
        clips = [_make_clip(i) for i in range(3)]
    sorted_clips = tuple(sorted(clips, key=lambda c: c.start_time))
    return ClipList(
        video_id=_VIDEO_ID,
        clips=sorted_clips,
        total_clips=len(sorted_clips),
        clips_rejected=0,
    )


def _make_storage_record(
    clip_id: str,
    status: str = "generated",
    composite_score: float = 0.5,
) -> StorageRecord:
    return StorageRecord(
        clip_id=clip_id,
        video_id=_VIDEO_ID,
        status=status,
        composite_score=composite_score,
        file_paths={
            "video": f"/output/{clip_id}.mp4",
            "thumbnail": f"/output/{clip_id}.jpg",
            "metadata": f"/output/{clip_id}.json",
            "subtitles": f"/output/{clip_id}.ass",
            "narration": f"/output/{clip_id}.mp3",
        },
        title="A Game Highlight That Will Blow Your Mind Right Now",
        description="Watch the most epic gaming moment ever captured on screen. "
        "Subscribe and hit the bell for daily shorts. #Gaming #Clips #Shorts.",
        tags=tuple(f"tag{i}" for i in range(10)),
        category="Gaming",
        created_at="2026-03-26T10:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# quality_metrics tests
# ---------------------------------------------------------------------------


class TestQualityMetrics:
    def test_basic_three_scenes(self) -> None:
        scene_list = _make_scored_scene_list()
        result = compute_quality_metrics(scene_list, _DEFAULT_CONFIG)

        assert isinstance(result, QualityMetrics)
        assert result.video_id == _VIDEO_ID
        assert result.total_scenes_scored == 3
        assert 0.0 <= result.avg_composite_score <= 1.0
        assert len(result.score_distribution) == 10
        assert len(result.face_visibility_distribution) == 10
        assert all(b.count >= 0 for b in result.score_distribution)
        assert result.rejection_count >= 0
        assert 0.0 <= result.rejection_rate <= 1.0

    def test_empty_scene_list(self) -> None:
        empty_list = _make_scored_scene_list(scenes=[])
        result = compute_quality_metrics(empty_list, _DEFAULT_CONFIG)

        assert result.total_scenes_scored == 0
        assert result.avg_composite_score == 0.0
        assert result.rejection_count == 0
        assert result.rejection_rate == 0.0
        assert all(b.count == 0 for b in result.score_distribution)

    def test_rejection_count(self) -> None:
        # 2 scenes below threshold (0.2), 1 above
        scenes = [
            _make_scored_scene(0, composite_score=0.1),
            _make_scored_scene(1, composite_score=0.15),
            _make_scored_scene(2, composite_score=0.8),
        ]
        scene_list = _make_scored_scene_list(scenes=scenes)
        result = compute_quality_metrics(scene_list, _DEFAULT_CONFIG)

        assert result.rejection_count == 2
        assert abs(result.rejection_rate - 2 / 3) < 1e-4

    def test_score_distribution_bins(self) -> None:
        # All scores at exactly 0.5 → should land in bin [0.5, 0.6)
        scenes = [_make_scored_scene(i, composite_score=0.5) for i in range(4)]
        scene_list = _make_scored_scene_list(scenes=scenes)
        result = compute_quality_metrics(scene_list, _DEFAULT_CONFIG)

        # Find bin containing 0.5
        bin_05 = next(b for b in result.score_distribution if b.bin_start == 0.5)
        assert bin_05.count == 4

        # All other bins should be 0
        for b in result.score_distribution:
            if b.bin_start != 0.5:
                assert b.count == 0

    def test_bins_cover_full_range(self) -> None:
        scene_list = _make_scored_scene_list()
        result = compute_quality_metrics(scene_list, _DEFAULT_CONFIG)

        starts = sorted(b.bin_start for b in result.score_distribution)
        assert starts[0] == pytest.approx(0.0)
        assert starts[-1] == pytest.approx(0.9)

    def test_all_rejected(self) -> None:
        scenes = [_make_scored_scene(i, composite_score=0.05) for i in range(5)]
        scene_list = _make_scored_scene_list(scenes=scenes)
        result = compute_quality_metrics(scene_list, _DEFAULT_CONFIG)

        assert result.rejection_count == 5
        assert result.rejection_rate == pytest.approx(1.0)

    def test_face_visibility_average(self) -> None:
        scenes = [
            _make_scored_scene(0, face_presence_score=0.0),
            _make_scored_scene(1, face_presence_score=1.0),
        ]
        scene_list = _make_scored_scene_list(scenes=scenes)
        result = compute_quality_metrics(scene_list, _DEFAULT_CONFIG)

        assert result.avg_face_visibility == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# publish_report tests
# ---------------------------------------------------------------------------


class TestPublishReport:
    def test_basic_mixed_statuses(self) -> None:
        records = (
            _make_storage_record("clip0000000000001", status="published"),
            _make_storage_record("clip0000000000002", status="scheduled"),
            _make_storage_record("clip0000000000003", status="queued"),
            _make_storage_record("clip0000000000004", status="generated"),
            _make_storage_record("clip0000000000005", status="failed"),
        )
        result = compute_publish_report(_VIDEO_ID, records, _DEFAULT_CONFIG)

        assert isinstance(result, PublishReport)
        assert result.total_clips == 5
        assert result.published_count == 1
        assert result.scheduled_count == 1
        assert result.queued_count == 1
        assert result.generated_count == 1
        assert result.failed_count == 1
        assert result.upload_success_rate == pytest.approx(1 / 5)
        assert result.queue_depth_days == pytest.approx(2.0)  # queued+scheduled=2, posts_per_day=1

    def test_empty_records(self) -> None:
        result = compute_publish_report(_VIDEO_ID, (), _DEFAULT_CONFIG)

        assert result.total_clips == 0
        assert result.upload_success_rate == 0.0
        assert result.queue_depth_days == 0.0

    def test_all_published(self) -> None:
        records = tuple(
            _make_storage_record(f"clip{i:016d}"[:16], status="published")
            for i in range(10)
        )
        result = compute_publish_report(_VIDEO_ID, records, _DEFAULT_CONFIG)

        assert result.published_count == 10
        assert result.upload_success_rate == pytest.approx(1.0)
        assert result.queue_depth_days == pytest.approx(0.0)

    def test_queue_depth_multiple_posts_per_day(self) -> None:
        records = tuple(
            _make_storage_record(f"clip{i:016d}"[:16], status="queued")
            for i in range(6)
        )
        config = {"scheduler": {"posts_per_day": 2}}
        result = compute_publish_report(_VIDEO_ID, records, config)

        assert result.queue_depth_days == pytest.approx(3.0)

    def test_deterministic_output(self) -> None:
        records = (
            _make_storage_record("clip0000000000002", status="published"),
            _make_storage_record("clip0000000000001", status="queued"),
        )
        result_a = compute_publish_report(_VIDEO_ID, records, _DEFAULT_CONFIG)
        result_b = compute_publish_report(_VIDEO_ID, records, _DEFAULT_CONFIG)
        assert result_a == result_b


# ---------------------------------------------------------------------------
# process (full pipeline_report) tests
# ---------------------------------------------------------------------------


class TestPipelineReport:
    def test_basic_report(self) -> None:
        clips = [_make_clip(i, duration=40.0 + i * 5) for i in range(3)]
        clip_list = _make_clip_list(clips=clips)
        scene_list = _make_scored_scene_list()
        records = tuple(
            _make_storage_record(f"clip{i:016d}"[:16], status="generated")
            for i in range(3)
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = process(
                video_id=_VIDEO_ID,
                run_id=_RUN_ID,
                clip_list=clip_list,
                scored_scenes=scene_list,
                storage_records=records,
                output_dir=tmp_dir,
                config=_DEFAULT_CONFIG,
            )

        assert isinstance(report, PipelineReport)
        assert report.video_id == _VIDEO_ID
        assert report.run_id == _RUN_ID
        assert report.total_clips_generated == 3
        assert report.total_clips_stored == 3
        assert report.min_duration_seconds == pytest.approx(40.0)
        assert report.max_duration_seconds == pytest.approx(50.0)
        assert report.mean_duration_seconds == pytest.approx(45.0)
        assert isinstance(report.quality, QualityMetrics)
        assert isinstance(report.publishing, PublishReport)
        assert report.generated_at  # non-empty ISO timestamp

    def test_json_report_written(self) -> None:
        clip_list = _make_clip_list()
        scene_list = _make_scored_scene_list()
        records = (_make_storage_record("clip0000000000001"),)

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = process(
                video_id=_VIDEO_ID,
                run_id=_RUN_ID,
                clip_list=clip_list,
                scored_scenes=scene_list,
                storage_records=records,
                output_dir=tmp_dir,
                config=_DEFAULT_CONFIG,
            )

            assert report.report_path
            assert os.path.isfile(report.report_path)

            with open(report.report_path, encoding="utf-8") as fh:
                data = json.load(fh)

            assert data["video_id"] == _VIDEO_ID
            assert data["run_id"] == _RUN_ID
            assert "quality" in data
            assert "publishing" in data

    def test_json_write_failure_does_not_raise(self) -> None:
        clip_list = _make_clip_list()
        scene_list = _make_scored_scene_list()

        # Pass a non-writable directory path that doesn't exist at root
        report = process(
            video_id=_VIDEO_ID,
            run_id=_RUN_ID,
            clip_list=clip_list,
            scored_scenes=scene_list,
            storage_records=(),
            output_dir="/non_existent_root_dir_for_test",
            config=_DEFAULT_CONFIG,
        )

        # Report should still be returned; report_path may be empty or partial
        assert isinstance(report, PipelineReport)

    def test_empty_clips_and_records(self) -> None:
        empty_clip_list = ClipList(
            video_id=_VIDEO_ID,
            clips=(),
            total_clips=0,
            clips_rejected=0,
        )
        empty_scene_list = _make_scored_scene_list(scenes=[])

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = process(
                video_id=_VIDEO_ID,
                run_id="",
                clip_list=empty_clip_list,
                scored_scenes=empty_scene_list,
                storage_records=(),
                output_dir=tmp_dir,
                config=_DEFAULT_CONFIG,
            )

        assert report.total_clips_generated == 0
        assert report.total_clips_stored == 0
        assert report.min_duration_seconds == 0.0
        assert report.max_duration_seconds == 0.0
        assert report.mean_duration_seconds == 0.0
        assert report.avg_composite_score == 0.0
        assert report.quality.total_scenes_scored == 0
        assert report.publishing.total_clips == 0

    def test_deterministic_output(self) -> None:
        clip_list = _make_clip_list()
        scene_list = _make_scored_scene_list()
        records = (
            _make_storage_record("clip0000000000001", status="published"),
            _make_storage_record("clip0000000000002", status="queued"),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            report_a = process(
                video_id=_VIDEO_ID,
                run_id=_RUN_ID,
                clip_list=clip_list,
                scored_scenes=scene_list,
                storage_records=records,
                output_dir=tmp_dir,
                config=_DEFAULT_CONFIG,
            )

        with tempfile.TemporaryDirectory() as tmp_dir2:
            report_b = process(
                video_id=_VIDEO_ID,
                run_id=_RUN_ID,
                clip_list=clip_list,
                scored_scenes=scene_list,
                storage_records=records,
                output_dir=tmp_dir2,
                config=_DEFAULT_CONFIG,
            )

        # All fields except report_path (different tmp dirs) and generated_at should match
        assert report_a.total_clips_generated == report_b.total_clips_generated
        assert report_a.avg_composite_score == report_b.avg_composite_score
        assert report_a.quality == report_b.quality
        assert report_a.publishing == report_b.publishing

    def test_avg_clip_score_computed_from_storage_records(self) -> None:
        clip_list = _make_clip_list()
        scene_list = _make_scored_scene_list()
        records = (
            _make_storage_record("clip0000000000001", composite_score=0.3),
            _make_storage_record("clip0000000000002", composite_score=0.7),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            report = process(
                video_id=_VIDEO_ID,
                run_id=_RUN_ID,
                clip_list=clip_list,
                scored_scenes=scene_list,
                storage_records=records,
                output_dir=tmp_dir,
                config=_DEFAULT_CONFIG,
            )

        assert report.avg_composite_score == pytest.approx(0.5)
