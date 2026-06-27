"""Unit tests for database/adapter.py — DatabaseAdapter operations."""

from __future__ import annotations

from contracts.face import FaceDetectionResult, SceneFaceData
from contracts.scoring import ScoredScene, ScoredSceneList
from contracts.transcript import Transcript, TranscriptSegment, Word
from contracts.scene import SceneSegment
from database.adapter import DatabaseAdapter


class TestVideoOperations:
    """Tests for video CRUD operations."""

    def test_insert_and_get_video(self, test_db):
        """Insert a video and retrieve it."""
        adapter = DatabaseAdapter(test_db)
        adapter.insert_video(
            video_id="abc123def4567890",
            file_path="/tmp/test.mp4",
            duration_seconds=3600.0,
            width=1920,
            height=1080,
            fps=30.0,
            has_audio=True,
            file_size_bytes=500_000_000,
        )

        video = adapter.get_video("abc123def4567890")
        assert video is not None
        assert video["video_id"] == "abc123def4567890"
        assert video["file_path"] == "/tmp/test.mp4"
        assert video["duration_seconds"] == 3600.0

    def test_insert_video_idempotent(self, test_db):
        """Inserting same video twice does not raise or duplicate."""
        adapter = DatabaseAdapter(test_db)
        kwargs = dict(
            video_id="abc123def4567890",
            file_path="/tmp/test.mp4",
            duration_seconds=3600.0,
            width=1920,
            height=1080,
            fps=30.0,
            has_audio=True,
            file_size_bytes=500_000_000,
        )
        adapter.insert_video(**kwargs)
        adapter.insert_video(**kwargs)  # Should not raise

        count = test_db.execute(
            "SELECT COUNT(*) FROM videos WHERE video_id = ?",
            ("abc123def4567890",),
        ).fetchone()[0]
        assert count == 1

    def test_get_nonexistent_video(self, test_db):
        """Getting a non-existent video returns None."""
        adapter = DatabaseAdapter(test_db)
        assert adapter.get_video("nonexistent") is None


class TestSceneOperations:
    """Tests for scene CRUD operations."""

    def test_insert_and_get_scenes(self, test_db):
        """Insert scenes and retrieve them sorted by start_time."""
        adapter = DatabaseAdapter(test_db)
        # Insert parent video first
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )

        scenes = [
            SceneSegment(scene_id="vid1_5000_10000", video_id="vid1", start_time=5000, end_time=10000, duration=5.0),
            SceneSegment(scene_id="vid1_0_5000", video_id="vid1", start_time=0, end_time=5000, duration=5.0),
        ]
        adapter.insert_scenes(scenes)

        result = adapter.get_scenes_for_video("vid1")
        assert len(result) == 2
        assert isinstance(result[0], SceneSegment)
        assert result[0].start_time == 0  # Sorted by start_time (ms)
        assert result[1].start_time == 5000

    def test_insert_scenes_idempotent(self, test_db):
        """Inserting same scenes twice doesn't duplicate."""
        adapter = DatabaseAdapter(test_db)
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )

        scenes = [
            SceneSegment(scene_id="vid1_0_5000", video_id="vid1", start_time=0, end_time=5000, duration=5.0),
        ]
        adapter.insert_scenes(scenes)
        adapter.insert_scenes(scenes)  # Should not raise

        count = test_db.execute("SELECT COUNT(*) FROM scenes").fetchone()[0]
        assert count == 1


class TestClipOperations:
    """Tests for clip CRUD operations."""

    def test_insert_and_get_clip(self, test_db):
        """Insert a clip and retrieve it."""
        adapter = DatabaseAdapter(test_db)
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )

        adapter.insert_clip(
            clip_id="clip1234567890ab",
            video_id="vid1",
            start_time=0.0,
            end_time=45.0,
            duration=45.0,
            composite_score=0.85,
        )

        clips = adapter.get_clips_for_video("vid1")
        assert len(clips) == 1
        assert clips[0]["clip_id"] == "clip1234567890ab"
        assert clips[0]["status"] == "generated"

    def test_update_clip_status(self, test_db):
        """Clip status transitions from valid source states."""
        adapter = DatabaseAdapter(test_db)
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_clip(
            clip_id="clip1", video_id="vid1",
            start_time=0.0, end_time=45.0, duration=45.0,
        )

        # Valid transition: generated → queued
        assert adapter.update_clip_status("clip1", "queued", ("generated",)) is True

        # Invalid transition: generated → published (current state is queued)
        assert adapter.update_clip_status("clip1", "published", ("generated",)) is False

    def test_update_clip_platform_ids_writes_non_none(self, test_db):
        """update_clip_platform_ids only writes non-None platform IDs."""
        adapter = DatabaseAdapter(test_db)
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_clip(
            clip_id="clip1", video_id="vid1",
            start_time=0.0, end_time=45.0, duration=45.0,
        )

        # Write only youtube_id — other platform IDs stay NULL
        adapter.update_clip_platform_ids(
            clip_id="clip1",
            youtube_id="yt_abc123",
            published_at="2026-06-19T08:00:00Z",
        )
        row = test_db.execute(
            "SELECT youtube_id, tiktok_id, instagram_id, facebook_id FROM clips WHERE clip_id = ?",
            ("clip1",),
        ).fetchone()
        assert row["youtube_id"] == "yt_abc123"
        assert row["tiktok_id"] is None
        assert row["instagram_id"] is None
        assert row["facebook_id"] is None

    def test_update_clip_platform_ids_does_not_overwrite_existing(self, test_db):
        """update_clip_platform_ids never overwrites an existing platform ID with None."""
        adapter = DatabaseAdapter(test_db)
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_clip(
            clip_id="clip1", video_id="vid1",
            start_time=0.0, end_time=45.0, duration=45.0,
        )

        # First partial upload — only YouTube succeeded
        adapter.update_clip_platform_ids(clip_id="clip1", youtube_id="yt_abc123")

        # Second partial upload — only TikTok succeeded; youtube_id must be preserved
        adapter.update_clip_platform_ids(clip_id="clip1", tiktok_id="tt_xyz789")

        row = test_db.execute(
            "SELECT youtube_id, tiktok_id FROM clips WHERE clip_id = ?",
            ("clip1",),
        ).fetchone()
        assert row["youtube_id"] == "yt_abc123"   # preserved from first call
        assert row["tiktok_id"] == "tt_xyz789"    # set by second call

    def test_update_clip_platform_ids_noop_when_all_none(self, test_db):
        """update_clip_platform_ids with all-None args is a safe no-op."""
        adapter = DatabaseAdapter(test_db)
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_clip(
            clip_id="clip1", video_id="vid1",
            start_time=0.0, end_time=45.0, duration=45.0,
        )
        # Should not raise
        adapter.update_clip_platform_ids(clip_id="clip1")


class TestClipAccountFiltering:
    """Tests for account_name column and get_clips_by_status account filtering."""

    def _insert_video(self, adapter, video_id="vid1"):
        adapter.insert_video(
            video_id=video_id, file_path=f"/tmp/{video_id}.mp4",
            duration_seconds=60.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )

    def test_insert_clip_stores_account_name(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video(adapter)
        adapter.insert_clip(
            clip_id="clip_acct1", video_id="vid1",
            start_time=0.0, end_time=30.0, duration=30.0,
            account_name="ch-alpha",
        )
        row = test_db.execute(
            "SELECT account_name FROM clips WHERE clip_id = ?", ("clip_acct1",)
        ).fetchone()
        assert row["account_name"] == "ch-alpha"

    def test_insert_clip_defaults_account_name_to_empty(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video(adapter)
        adapter.insert_clip(
            clip_id="clip_noname", video_id="vid1",
            start_time=0.0, end_time=30.0, duration=30.0,
        )
        row = test_db.execute(
            "SELECT account_name FROM clips WHERE clip_id = ?", ("clip_noname",)
        ).fetchone()
        assert row["account_name"] == ""

    def test_get_clips_by_status_filters_by_account(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video(adapter, "vid1")
        self._insert_video(adapter, "vid2")
        adapter.insert_clip(
            clip_id="clip_alpha", video_id="vid1",
            start_time=0.0, end_time=30.0, duration=30.0,
            account_name="ch-alpha",
        )
        adapter.insert_clip(
            clip_id="clip_beta", video_id="vid2",
            start_time=0.0, end_time=30.0, duration=30.0,
            account_name="ch-beta",
        )
        result = adapter.get_clips_by_status(["generated"], account_name="ch-alpha")
        assert len(result) == 1
        assert result[0]["clip_id"] == "clip_alpha"

    def test_get_clips_by_status_no_account_returns_all(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video(adapter, "vid1")
        self._insert_video(adapter, "vid2")
        adapter.insert_clip(
            clip_id="clip_alpha", video_id="vid1",
            start_time=0.0, end_time=30.0, duration=30.0,
            account_name="ch-alpha",
        )
        adapter.insert_clip(
            clip_id="clip_beta", video_id="vid2",
            start_time=0.0, end_time=30.0, duration=30.0,
            account_name="ch-beta",
        )
        result = adapter.get_clips_by_status(["generated"])
        assert len(result) == 2

    def test_get_clips_by_status_account_none_is_backward_compat(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video(adapter, "vid1")
        adapter.insert_clip(
            clip_id="clip1", video_id="vid1",
            start_time=0.0, end_time=30.0, duration=30.0,
            account_name="ch-alpha",
        )
        result_none = adapter.get_clips_by_status(["generated"], account_name=None)
        result_omit = adapter.get_clips_by_status(["generated"])
        assert result_none == result_omit

    def test_get_clips_by_status_wrong_account_returns_empty(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video(adapter, "vid1")
        adapter.insert_clip(
            clip_id="clip1", video_id="vid1",
            start_time=0.0, end_time=30.0, duration=30.0,
            account_name="ch-alpha",
        )
        result = adapter.get_clips_by_status(["generated"], account_name="nonexistent")
        assert result == []

    def test_get_clips_by_status_empty_statuses_returns_empty(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video(adapter, "vid1")
        adapter.insert_clip(
            clip_id="clip1", video_id="vid1",
            start_time=0.0, end_time=30.0, duration=30.0,
            account_name="ch-alpha",
        )
        assert adapter.get_clips_by_status([], account_name="ch-alpha") == []


class TestPipelineRunOperations:
    """Tests for pipeline run CRUD operations."""

    def test_create_and_get_run(self, test_db):
        """Create a pipeline run and retrieve it."""
        adapter = DatabaseAdapter(test_db)
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )

        adapter.create_pipeline_run("run1", "vid1", '{"test": true}')
        run = adapter.get_active_run("vid1")
        assert run is not None
        assert run["run_id"] == "run1"
        assert run["status"] == "started"

    def test_checkpoint_and_resume(self, test_db):
        """Checkpoint updates last_completed_stage for resume."""
        adapter = DatabaseAdapter(test_db)
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.create_pipeline_run("run1", "vid1")

        adapter.update_checkpoint("run1", "ingestion")
        assert adapter.get_last_completed_stage("vid1") == "ingestion"

        adapter.update_checkpoint("run1", "scene_splitter")
        assert adapter.get_last_completed_stage("vid1") == "scene_splitter"

    def test_update_pipeline_status(self, test_db):
        """Pipeline status can be updated with counters."""
        adapter = DatabaseAdapter(test_db)
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.create_pipeline_run("run1", "vid1")

        adapter.update_pipeline_status(
            "run1", "completed", clips_generated=12, clips_failed=0,
        )

        row = test_db.execute(
            "SELECT * FROM pipeline_runs WHERE run_id = ?", ("run1",)
        ).fetchone()
        assert row["status"] == "completed"
        assert row["clips_generated"] == 12
        assert row["completed_at"] is not None

    def test_no_active_run_returns_none(self, test_db):
        """No active run returns None."""
        adapter = DatabaseAdapter(test_db)
        assert adapter.get_active_run("nonexistent") is None
        assert adapter.get_last_completed_stage("nonexistent") is None


class TestSignalCacheOperations:
    def _insert_video_and_scenes(self, adapter: DatabaseAdapter) -> None:
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_scenes(
            [
                SceneSegment("vid1_0_5000", "vid1", 0, 5000, 5.0),
                SceneSegment("vid1_5000_10000", "vid1", 5000, 10000, 5.0),
            ]
        )

    def test_stage_state_round_trip(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video_and_scenes(adapter)
        adapter.upsert_stage_state(
            "vid1", "transcription", "completed", "v1", "hash1",
            units_done=2, units_total=2, checkpoint_token="1",
            payload_json='{"language":"en"}',
        )
        row = adapter.get_stage_state("vid1", "transcription")
        assert row is not None
        assert row["status"] == "completed"
        assert row["units_done"] == 2

    def test_transcript_round_trip(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video_and_scenes(adapter)
        segments = (
            TranscriptSegment(
                text="hello world",
                start_time=0,
                end_time=1000,
                words=(
                    Word("hello", 0, 400, 0.9),
                    Word("world", 500, 1000, 0.9),
                ),
                confidence=0.9,
            ),
        )
        adapter.upsert_transcript_chunk("vid1", 0, segments)
        transcript = adapter.get_transcript("vid1")
        assert transcript is not None
        assert transcript.total_words == 2
        assert transcript.segments[0].text == "hello world"

    def test_face_cache_round_trip(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video_and_scenes(adapter)
        scene_data = SceneFaceData(
            scene_id="vid1_0_5000",
            face_visible_ratio=0.5,
            bounding_boxes=(),
            average_bbox=None,
            sample_count=10,
        )
        adapter.upsert_face_scene("vid1_0_5000", "vid1", scene_data)
        result = adapter.get_face_detection_result("vid1")
        assert isinstance(result, FaceDetectionResult)
        assert result is not None
        assert result.scene_data[0].scene_id == "vid1_0_5000"

    def test_scored_scene_round_trip(self, test_db):
        adapter = DatabaseAdapter(test_db)
        self._insert_video_and_scenes(adapter)
        scored = ScoredSceneList(
            video_id="vid1",
            scenes=(
                ScoredScene("vid1_0_5000", "vid1", 0, 5000, 5.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 1),
                ScoredScene("vid1_5000_10000", "vid1", 5000, 10000, 5.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 2),
            ),
            min_score=0.7,
            max_score=0.8,
            avg_score=0.75,
        )
        adapter.persist_scored_scenes(scored, {"vid1_0_5000": "a", "vid1_5000_10000": "b"})
        restored = adapter.get_scored_scene_list("vid1")
        assert restored is not None
        assert len(restored.scenes) == 2
        assert restored.scenes[0].composite_score >= restored.scenes[1].composite_score

    def test_scheduler_lock_acquire_and_release(self, test_db):
        adapter = DatabaseAdapter(test_db)
        assert adapter.acquire_scheduler_lock("generation:acct", "owner-1", 900) is True
        assert adapter.acquire_scheduler_lock("generation:acct", "owner-2", 900) is False
        adapter.release_scheduler_lock("generation:acct", "owner-1")
        assert adapter.acquire_scheduler_lock("generation:acct", "owner-2", 900) is True
