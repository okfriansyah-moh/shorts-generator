"""Unit tests for database/adapter.py — DatabaseAdapter operations."""

from __future__ import annotations

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
