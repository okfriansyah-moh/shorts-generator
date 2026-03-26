"""Unit tests for the scheduler module."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from contracts.storage import StorageRecord
from modules.scheduler import process


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_storage_record(
    clip_id: str = "abcdef0123456789",
    video_id: str = "a1b2c3d4e5f67890",
    composite_score: float = 0.75,
    status: str = "queued",
    scheduled_at: str | None = None,
    **overrides,
) -> StorageRecord:
    """Create a StorageRecord with sensible defaults."""
    defaults = {
        "clip_id": clip_id,
        "video_id": video_id,
        "status": status,
        "composite_score": composite_score,
        "file_paths": {
            "metadata": f"{video_id}/clips/{clip_id}/metadata.json",
            "narration": f"{video_id}/tts/{clip_id}.wav",
            "subtitles": f"{video_id}/subtitles/{clip_id}.ass",
            "thumbnail": f"{video_id}/clips/{clip_id}/thumbnail.jpg",
            "video": f"{video_id}/clips/{clip_id}/final.mp4",
        },
        "title": "🎮 This Amazing Gaming Moment Will Blow Your Mind!",
        "description": (
            "Watch this incredible gaming highlight from the latest session! "
            "An absolutely insane play that you won't believe happened. "
            "#gaming #shorts #highlights #epic #gameplay #viral"
        ),
        "tags": (
            "gaming",
            "shorts",
            "highlights",
            "epic",
            "gameplay",
            "viral",
            "amazing",
            "clutch",
            "insane",
            "moment",
        ),
        "category": "Gaming",
        "created_at": "2026-03-26T08:00:00Z",
        "scheduled_at": scheduled_at,
    }
    defaults.update(overrides)
    return StorageRecord(**defaults)


def _make_scheduler_config() -> dict:
    """Create test scheduler config."""
    return {
        "scheduler": {
            "publish_time_utc": "10:00",
            "max_daily_uploads": 3,
            "posts_per_day": 1,
        },
    }


def _fixed_now():
    """Return a fixed datetime for deterministic testing."""
    return datetime(2026, 3, 26, 8, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Tests — Normal Operation
# ---------------------------------------------------------------------------


class TestSchedulerProcess:
    """Tests for the scheduler process function."""

    @patch("modules.scheduler.schedule.datetime")
    def test_single_clip_scheduled(self, mock_dt) -> None:
        """A single queued clip gets scheduled for tomorrow."""
        mock_dt.now.return_value = _fixed_now()
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        config = _make_scheduler_config()
        records = [_make_storage_record()]

        result = process(records, [], config)

        assert len(result) == 1
        assert result[0].status == "scheduled"
        assert result[0].scheduled_at is not None
        assert "2026-03-27T10:00:00Z" == result[0].scheduled_at

    @patch("modules.scheduler.schedule.datetime")
    def test_multiple_clips_different_days(self, mock_dt) -> None:
        """Multiple clips get scheduled on consecutive days."""
        mock_dt.now.return_value = _fixed_now()
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        config = _make_scheduler_config()
        records = [
            _make_storage_record(clip_id="clip_aaa_000000001", composite_score=0.9),
            _make_storage_record(clip_id="clip_bbb_000000002", composite_score=0.7),
            _make_storage_record(clip_id="clip_ccc_000000003", composite_score=0.5),
        ]

        result = process(records, [], config)

        assert len(result) == 3
        # All should be scheduled
        assert all(r.status == "scheduled" for r in result)

        # Dates should be unique
        dates = [r.scheduled_at for r in result]
        assert len(set(dates)) == 3

    @patch("modules.scheduler.schedule.datetime")
    def test_best_score_gets_earliest_date(self, mock_dt) -> None:
        """Higher-scored clip is scheduled before lower-scored one."""
        mock_dt.now.return_value = _fixed_now()
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        config = _make_scheduler_config()
        records = [
            _make_storage_record(clip_id="low_score_clip_001", composite_score=0.3),
            _make_storage_record(clip_id="high_score_clip_01", composite_score=0.9),
        ]

        result = process(records, [], config)

        # Result is sorted by scheduled_at, so earliest should be high score
        by_date = sorted(result, key=lambda r: r.scheduled_at or "")
        assert by_date[0].clip_id == "high_score_clip_01"
        assert by_date[1].clip_id == "low_score_clip_001"


# ---------------------------------------------------------------------------
# Tests — Empty Queue
# ---------------------------------------------------------------------------


class TestSchedulerEmptyQueue:
    """Tests for scheduler with no queued clips."""

    def test_empty_list_returns_empty(self) -> None:
        """Empty input returns empty output."""
        config = _make_scheduler_config()
        result = process([], [], config)
        assert result == []

    def test_no_queued_clips_unchanged(self) -> None:
        """Non-queued clips pass through unchanged."""
        config = _make_scheduler_config()
        records = [
            _make_storage_record(status="scheduled", scheduled_at="2026-03-27T10:00:00Z"),
            _make_storage_record(
                clip_id="aaaa1111bbbb2222",
                status="published",
                scheduled_at="2026-03-28T10:00:00Z",
            ),
        ]

        result = process(records, [], config)

        assert len(result) == 2
        # Statuses should remain unchanged
        statuses = {r.clip_id: r.status for r in result}
        assert statuses["abcdef0123456789"] == "scheduled"
        assert statuses["aaaa1111bbbb2222"] == "published"


# ---------------------------------------------------------------------------
# Tests — Conflict Resolution
# ---------------------------------------------------------------------------


class TestSchedulerConflicts:
    """Tests for schedule conflict resolution."""

    @patch("modules.scheduler.schedule.datetime")
    def test_skips_occupied_dates(self, mock_dt) -> None:
        """Scheduler skips dates that already have scheduled clips."""
        mock_dt.now.return_value = _fixed_now()
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        config = _make_scheduler_config()

        # Tomorrow is already occupied by an existing scheduled clip
        existing = [
            _make_storage_record(
                clip_id="existing_clip_0001",
                status="scheduled",
                scheduled_at="2026-03-27T10:00:00Z",
            ),
        ]

        records = [_make_storage_record(clip_id="new_clip_00000001")]

        result = process(records, existing, config)

        # New clip should be on 2026-03-28, skipping 03-27
        assert len(result) == 1
        assert result[0].scheduled_at == "2026-03-28T10:00:00Z"

    @patch("modules.scheduler.schedule.datetime")
    def test_skips_published_dates(self, mock_dt) -> None:
        """Scheduler skips dates with published clips too."""
        mock_dt.now.return_value = _fixed_now()
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        config = _make_scheduler_config()

        existing = [
            _make_storage_record(
                clip_id="published_clip_01",
                status="published",
                scheduled_at="2026-03-27T10:00:00Z",
            ),
        ]

        records = [_make_storage_record(clip_id="new_clip_00000002")]

        result = process(records, existing, config)

        assert result[0].scheduled_at == "2026-03-28T10:00:00Z"


# ---------------------------------------------------------------------------
# Tests — Determinism
# ---------------------------------------------------------------------------


class TestSchedulerDeterminism:
    """Tests that scheduler output is deterministic."""

    @patch("modules.scheduler.schedule.datetime")
    def test_same_input_same_output(self, mock_dt) -> None:
        """Running scheduler twice with same input produces same result."""
        mock_dt.now.return_value = _fixed_now()
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        config = _make_scheduler_config()
        records = [
            _make_storage_record(clip_id="clip_aaa_000000001", composite_score=0.9),
            _make_storage_record(clip_id="clip_bbb_000000002", composite_score=0.7),
            _make_storage_record(clip_id="clip_ccc_000000003", composite_score=0.5),
        ]

        result1 = process(records, [], config)
        result2 = process(records, [], config)

        assert len(result1) == len(result2)
        for r1, r2 in zip(result1, result2):
            assert r1.clip_id == r2.clip_id
            assert r1.scheduled_at == r2.scheduled_at
            assert r1.status == r2.status

    @patch("modules.scheduler.schedule.datetime")
    def test_tiebreaker_by_clip_id(self, mock_dt) -> None:
        """Clips with same score are ordered by clip_id (ascending)."""
        mock_dt.now.return_value = _fixed_now()
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        config = _make_scheduler_config()
        records = [
            _make_storage_record(clip_id="zzz_clip_00000001", composite_score=0.5),
            _make_storage_record(clip_id="aaa_clip_00000001", composite_score=0.5),
        ]

        result = process(records, [], config)

        # aaa sorts before zzz, so aaa should get the earlier date
        by_date = sorted(result, key=lambda r: r.scheduled_at or "")
        assert by_date[0].clip_id == "aaa_clip_00000001"
        assert by_date[1].clip_id == "zzz_clip_00000001"

    @patch("modules.scheduler.schedule.datetime")
    def test_publish_time_from_config(self, mock_dt) -> None:
        """Publish time comes from config, not hardcoded."""
        mock_dt.now.return_value = _fixed_now()
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        config = _make_scheduler_config()
        config["scheduler"]["publish_time_utc"] = "14:30"

        records = [_make_storage_record()]

        result = process(records, [], config)

        assert result[0].scheduled_at == "2026-03-27T14:30:00Z"
