"""Unit tests for scripts/upload_scheduler.py."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from scripts import upload_scheduler
from database.adapter import DatabaseAdapter


def _insert_video(adapter: DatabaseAdapter, video_id: str = "vid1") -> None:
    adapter.insert_video(
        video_id=video_id,
        file_path=f"/tmp/{video_id}.mp4",
        duration_seconds=60.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
        file_size_bytes=100,
    )


def _insert_clip(
    adapter: DatabaseAdapter,
    clip_id: str,
    *,
    video_id: str = "vid1",
    account_name: str = "mrkimbum12",
    status: str = "scheduled",
    scheduled_at: str | None = None,
) -> None:
    adapter.insert_clip(
        clip_id=clip_id,
        video_id=video_id,
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        composite_score=0.9,
        video_path="clips/final.mp4",
        thumbnail_path="clips/thumbnail.jpg",
        account_name=account_name,
    )
    adapter.connection.execute(
        """UPDATE clips
           SET status = ?, scheduled_at = ?, title = ?, description = ?, tags = ?
           WHERE clip_id = ?""",
        (status, scheduled_at, f"title-{clip_id}", "desc", "[]", clip_id),
    )
    adapter.connection.commit()


class TestUploadSchedulerQueueFiltering:
    def test_next_due_record_ignores_null_scheduled_at(self, test_db, monkeypatch) -> None:
        adapter = DatabaseAdapter(test_db)
        _insert_video(adapter)
        _insert_clip(adapter, "clip_null", scheduled_at=None)
        _insert_clip(adapter, "clip_future", scheduled_at="2026-06-29T02:00:00Z")
        _insert_clip(adapter, "clip_due", scheduled_at="2026-06-28T02:00:00Z")

        class _FixedDatetime:
            @staticmethod
            def now(_tz):
                return datetime(2026, 6, 28, 6, 0, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(upload_scheduler, "datetime", _FixedDatetime)

        record = upload_scheduler._next_due_record(
            adapter,
            "mrkimbum12",
            "/tmp/output",
        )

        assert record is not None
        assert record.clip_id == "clip_due"

    def test_remaining_scheduled_count_excludes_null_scheduled_at(self, test_db) -> None:
        adapter = DatabaseAdapter(test_db)
        _insert_video(adapter)
        _insert_clip(adapter, "clip_null", scheduled_at=None)
        _insert_clip(adapter, "clip_future_1", scheduled_at="2026-06-29T02:00:00Z")
        _insert_clip(adapter, "clip_future_2", scheduled_at="2026-06-30T02:00:00Z")

        assert upload_scheduler._remaining_scheduled_count(adapter, "mrkimbum12") == 2
        assert len(upload_scheduler._invalid_scheduled_rows(adapter, "mrkimbum12")) == 1


class TestUploadSchedulerPathResolution:
    def test_row_to_storage_record_prefers_final_mp4_for_legacy_clip_name(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output" / "mrkimbum12"
        clip_dir = output_dir / "video-1" / "clips" / "shorts-1"
        clip_dir.mkdir(parents=True)
        final_path = clip_dir / "final.mp4"
        thumb_path = clip_dir / "thumbnail.jpg"
        final_path.write_bytes(b"video")
        thumb_path.write_bytes(b"thumb")

        row = {
            "clip_id": "clip-1",
            "video_id": "video-1",
            "status": "scheduled",
            "video_path": "video-1/clips/shorts-1/clip.mp4",
            "thumbnail_path": "video-1/clips/shorts-1/thumbnail.jpg",
            "title": "Title",
            "description": "Desc",
            "tags": "[]",
        }

        record = upload_scheduler._row_to_storage_record(row, str(output_dir))

        assert record.file_paths["video"] == str(final_path)
        assert record.file_paths["thumbnail"] == str(thumb_path)

    def test_upload_validation_rejects_temporary_video_artifact(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output" / "mrkimbum12"
        clip_dir = output_dir / "video-1" / "clips" / "shorts-2"
        clip_dir.mkdir(parents=True)
        tmp_video = clip_dir / "final.tmp.mp4"
        tmp_video.write_bytes(b"video")

        row = {
            "clip_id": "clip-2",
            "video_id": "video-1",
            "status": "scheduled",
            "video_path": "video-1/clips/shorts-2/clip.mp4",
            "thumbnail_path": "video-1/clips/shorts-2/thumbnail.jpg",
            "title": "Title",
            "description": "Desc",
            "tags": "[]",
        }

        record = upload_scheduler._row_to_storage_record(row, str(output_dir))
        error = upload_scheduler._upload_validation_error(record)

        assert error is not None
        assert "temporary artefacts present" in error
