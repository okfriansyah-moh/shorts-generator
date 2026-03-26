"""Unit tests for the publisher module."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from contracts.storage import StorageRecord
from modules.publisher import process
from modules.publisher.publish import publish_single
from modules.publisher.visibility import check_visibility_transitions
from modules.publisher.youtube_client import (
    ThumbnailUploadResult,
    UploadResult,
    VisibilityUpdateResult,
    YouTubeClient,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_storage_record(
    clip_id: str = "abcdef0123456789",
    video_id: str = "a1b2c3d4e5f67890",
    composite_score: float = 0.75,
    status: str = "scheduled",
    scheduled_at: str | None = "2026-03-27T10:00:00Z",
    published_at: str | None = None,
    youtube_id: str | None = None,
    error_message: str | None = None,
    retry_count: int = 0,
    **overrides,
) -> StorageRecord:
    """Create a StorageRecord with sensible defaults for publisher tests."""
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
            "gaming", "shorts", "highlights", "epic", "gameplay",
            "viral", "amazing", "clutch", "insane", "moment",
        ),
        "category": "Gaming",
        "created_at": "2026-03-26T08:00:00Z",
        "scheduled_at": scheduled_at,
        "published_at": published_at,
        "youtube_id": youtube_id,
        "error_message": error_message,
        "retry_count": retry_count,
    }
    defaults.update(overrides)
    return StorageRecord(**defaults)


def _make_publisher_config(tmp_dir: str | None = None) -> dict:
    """Create test publisher config."""
    output_dir = tmp_dir or tempfile.mkdtemp()
    return {
        "paths": {"output_dir": output_dir},
        "publisher": {
            "platform": "youtube",
            "max_retries": 3,
            "retry_delays": [60, 300, 900],
            "initial_visibility": "unlisted",
            "public_delay_minutes": 30,
            "credentials_path": "",
        },
    }


def _make_mock_client(
    upload_success: bool = True,
    youtube_id: str = "yt_video_12345",
    thumbnail_success: bool = True,
    visibility_success: bool = True,
    upload_error: str | None = None,
    quota_exceeded: bool = False,
) -> MagicMock:
    """Create a mock YouTubeClient with configurable behavior."""
    client = MagicMock(spec=YouTubeClient)
    client.upload_video.return_value = UploadResult(
        success=upload_success,
        youtube_id=youtube_id if upload_success else None,
        error_message=upload_error if not upload_success else None,
        quota_exceeded=quota_exceeded,
    )
    client.set_thumbnail.return_value = ThumbnailUploadResult(
        success=thumbnail_success,
    )
    client.update_visibility.return_value = VisibilityUpdateResult(
        success=visibility_success,
    )
    return client


def _fixed_now() -> datetime:
    """Fixed datetime for deterministic testing."""
    return datetime(2026, 3, 27, 12, 0, 0, tzinfo=timezone.utc)


def _no_sleep(seconds: float) -> None:
    """No-op sleep for testing."""
    pass


# ---------------------------------------------------------------------------
# Tests — YouTube Client
# ---------------------------------------------------------------------------


class TestYouTubeClient:
    """Tests for YouTubeClient authentication and validation."""

    def test_authenticate_missing_credentials_path(self) -> None:
        """Raise ValueError when credentials_path is not set."""
        client = YouTubeClient({"credentials_path": ""})
        with pytest.raises(ValueError, match="credentials_path not configured"):
            client.authenticate()

    def test_authenticate_missing_credentials_file(self, tmp_path) -> None:
        """Raise FileNotFoundError when credentials file doesn't exist."""
        client = YouTubeClient(
            {"credentials_path": str(tmp_path / "missing.json")}
        )
        with pytest.raises(FileNotFoundError):
            client.authenticate()

    def test_authenticate_missing_fields(self, tmp_path) -> None:
        """Raise ValueError when required fields are missing."""
        creds_file = tmp_path / "creds.json"
        creds_file.write_text('{"client_id": "test"}')
        client = YouTubeClient(
            {"credentials_path": str(creds_file)}
        )
        with pytest.raises(ValueError, match="Missing required credential fields"):
            client.authenticate()

    def test_upload_without_auth_fails(self) -> None:
        """Upload fails when client is not authenticated."""
        client = YouTubeClient({})
        result = client.upload_video(
            "/fake/video.mp4", "title", "desc", ("tag",), "Gaming"
        )
        assert not result.success
        assert "Not authenticated" in (result.error_message or "")

    def test_set_thumbnail_without_auth_fails(self) -> None:
        """Thumbnail upload fails when client is not authenticated."""
        client = YouTubeClient({})
        result = client.set_thumbnail("yt123", "/fake/thumb.jpg")
        assert not result.success

    def test_update_visibility_without_auth_fails(self) -> None:
        """Visibility update fails when client is not authenticated."""
        client = YouTubeClient({})
        result = client.update_visibility("yt123", "public")
        assert not result.success


# ---------------------------------------------------------------------------
# Tests — Single Clip Publishing
# ---------------------------------------------------------------------------


class TestPublishSingle:
    """Tests for publish_single function."""

    def test_successful_publish(self) -> None:
        """Clip is published successfully on first attempt."""
        config = _make_publisher_config()
        record = _make_storage_record()
        client = _make_mock_client()

        # Create video file so path validation passes in client mock
        result = publish_single(
            record, client, config,
            sleep_fn=_no_sleep,
            reference_time=_fixed_now(),
        )

        assert result.status == "published"
        assert result.youtube_id == "yt_video_12345"
        assert result.published_at == "2026-03-27T12:00:00Z"
        assert result.retry_count == 0

    def test_idempotent_skip_already_published(self) -> None:
        """Clip with existing youtube_id is not re-uploaded."""
        config = _make_publisher_config()
        record = _make_storage_record(
            status="published",
            youtube_id="existing_yt_id",
            published_at="2026-03-27T10:00:00Z",
        )
        client = _make_mock_client()

        result = publish_single(
            record, client, config,
            sleep_fn=_no_sleep,
            reference_time=_fixed_now(),
        )

        assert result.status == "published"
        assert result.youtube_id == "existing_yt_id"
        # Upload should NOT be called
        client.upload_video.assert_not_called()

    def test_retry_on_failure(self) -> None:
        """Upload retries on failure and succeeds on 2nd attempt."""
        config = _make_publisher_config()
        record = _make_storage_record()

        client = _make_mock_client()
        # First call fails, second succeeds
        client.upload_video.side_effect = [
            UploadResult(success=False, error_message="Temporary error"),
            UploadResult(success=True, youtube_id="yt_retry_ok"),
        ]

        result = publish_single(
            record, client, config,
            sleep_fn=_no_sleep,
            reference_time=_fixed_now(),
        )

        assert result.status == "published"
        assert result.youtube_id == "yt_retry_ok"
        assert result.retry_count == 1
        assert client.upload_video.call_count == 2

    def test_all_retries_exhausted(self) -> None:
        """Clip marked failed after all retries exhausted."""
        config = _make_publisher_config()
        record = _make_storage_record()

        client = _make_mock_client(
            upload_success=False, upload_error="Server error"
        )

        result = publish_single(
            record, client, config,
            sleep_fn=_no_sleep,
            reference_time=_fixed_now(),
        )

        assert result.status == "failed"
        assert result.youtube_id is None
        assert result.error_message == "Server error"
        assert result.retry_count == 3
        assert client.upload_video.call_count == 3

    def test_quota_exceeded_stops_retries(self) -> None:
        """Quota exceeded stops retrying immediately without marking failed."""
        config = _make_publisher_config()
        record = _make_storage_record()

        client = _make_mock_client(
            upload_success=False,
            upload_error="Quota exceeded",
            quota_exceeded=True,
        )

        result = publish_single(
            record, client, config,
            sleep_fn=_no_sleep,
            reference_time=_fixed_now(),
        )

        # Clip stays in its original status (scheduled) — not marked failed
        assert result.status == "scheduled"
        assert result.retry_count == 1
        assert result.error_message == "Quota exceeded"
        # Should only attempt once on quota exceeded
        assert client.upload_video.call_count == 1

    def test_thumbnail_failure_non_fatal(self) -> None:
        """Thumbnail upload failure doesn't prevent publish success."""
        config = _make_publisher_config()
        record = _make_storage_record()

        client = _make_mock_client(thumbnail_success=False)

        result = publish_single(
            record, client, config,
            sleep_fn=_no_sleep,
            reference_time=_fixed_now(),
        )

        assert result.status == "published"
        assert result.youtube_id == "yt_video_12345"

    def test_retry_delay_called(self) -> None:
        """Sleep is called between retries with configured delays."""
        config = _make_publisher_config()
        config["publisher"]["retry_delays"] = [10, 20, 30]
        record = _make_storage_record()

        client = _make_mock_client()
        client.upload_video.side_effect = [
            UploadResult(success=False, error_message="err1"),
            UploadResult(success=False, error_message="err2"),
            UploadResult(success=True, youtube_id="yt_final"),
        ]

        sleep_calls: list[float] = []

        def track_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        result = publish_single(
            record, client, config,
            sleep_fn=track_sleep,
            reference_time=_fixed_now(),
        )

        assert result.status == "published"
        assert sleep_calls == [10, 20]


# ---------------------------------------------------------------------------
# Tests — Batch Publishing (process function)
# ---------------------------------------------------------------------------


class TestPublishProcess:
    """Tests for the main process function."""

    def test_publish_eligible_clips(self) -> None:
        """Only scheduled clips with scheduled_at <= now are published."""
        config = _make_publisher_config()
        client = _make_mock_client()
        now = _fixed_now()  # 2026-03-27T12:00:00Z

        records = [
            _make_storage_record(
                clip_id="eligible_clip_001",
                scheduled_at="2026-03-27T10:00:00Z",
            ),
            _make_storage_record(
                clip_id="future_clip_00001",
                scheduled_at="2026-03-28T10:00:00Z",
            ),
        ]

        result = process(
            records, client, config,
            sleep_fn=_no_sleep,
            reference_time=now,
        )

        statuses = {r.clip_id: r.status for r in result}
        assert statuses["eligible_clip_001"] == "published"
        assert statuses["future_clip_00001"] == "scheduled"  # Unchanged

    def test_non_scheduled_clips_unchanged(self) -> None:
        """Clips not in 'scheduled' status pass through unchanged."""
        config = _make_publisher_config()
        client = _make_mock_client()

        records = [
            _make_storage_record(
                clip_id="queued_clip_00001",
                status="queued",
                scheduled_at=None,
            ),
            _make_storage_record(
                clip_id="published_clip_01",
                status="published",
                youtube_id="existing_yt",
                published_at="2026-03-26T10:00:00Z",
            ),
        ]

        result = process(
            records, client, config,
            sleep_fn=_no_sleep,
            reference_time=_fixed_now(),
        )

        statuses = {r.clip_id: r.status for r in result}
        assert statuses["queued_clip_00001"] == "queued"
        assert statuses["published_clip_01"] == "published"
        # No uploads should occur
        client.upload_video.assert_not_called()

    def test_empty_records_returns_empty(self) -> None:
        """Empty input returns empty output."""
        config = _make_publisher_config()
        client = _make_mock_client()

        result = process(
            [], client, config,
            sleep_fn=_no_sleep,
            reference_time=_fixed_now(),
        )

        assert result == []

    def test_failed_clip_doesnt_block_next(self) -> None:
        """A failed clip doesn't prevent subsequent clips from publishing."""
        config = _make_publisher_config()
        now = _fixed_now()

        client = _make_mock_client()
        # First clip fails all retries, second succeeds
        fail_result = UploadResult(
            success=False, error_message="Server error"
        )
        success_result = UploadResult(
            success=True, youtube_id="yt_second_ok"
        )
        client.upload_video.side_effect = [
            fail_result, fail_result, fail_result,  # 3 retries for first
            success_result,  # First attempt for second
        ]

        records = [
            _make_storage_record(
                clip_id="first_clip_00001",
                scheduled_at="2026-03-27T08:00:00Z",
            ),
            _make_storage_record(
                clip_id="second_clip_0001",
                scheduled_at="2026-03-27T09:00:00Z",
            ),
        ]

        result = process(
            records, client, config,
            sleep_fn=_no_sleep,
            reference_time=now,
        )

        statuses = {r.clip_id: r.status for r in result}
        assert statuses["first_clip_00001"] == "failed"
        assert statuses["second_clip_0001"] == "published"

    def test_deterministic_output_order(self) -> None:
        """Output is sorted deterministically by scheduled_at then clip_id."""
        config = _make_publisher_config()
        client = _make_mock_client()
        now = _fixed_now()

        records = [
            _make_storage_record(
                clip_id="clip_zzz_00000001",
                scheduled_at="2026-03-27T10:00:00Z",
            ),
            _make_storage_record(
                clip_id="clip_aaa_00000001",
                scheduled_at="2026-03-27T10:00:00Z",
            ),
        ]

        # Assign unique youtube_ids
        client.upload_video.side_effect = [
            UploadResult(success=True, youtube_id="yt_aaa"),
            UploadResult(success=True, youtube_id="yt_zzz"),
        ]

        result = process(
            records, client, config,
            sleep_fn=_no_sleep,
            reference_time=now,
        )

        # Both should be published, sorted by scheduled_at then clip_id
        assert result[0].clip_id == "clip_aaa_00000001"
        assert result[1].clip_id == "clip_zzz_00000001"


# ---------------------------------------------------------------------------
# Tests — Determinism
# ---------------------------------------------------------------------------


class TestPublishDeterminism:
    """Tests that publisher output is deterministic."""

    def test_same_input_same_output(self) -> None:
        """Running publisher twice with same input produces same result."""
        config = _make_publisher_config()
        now = _fixed_now()

        records = [
            _make_storage_record(
                clip_id="clip_aaa_00000001",
                scheduled_at="2026-03-27T08:00:00Z",
            ),
            _make_storage_record(
                clip_id="clip_bbb_00000001",
                scheduled_at="2026-03-27T09:00:00Z",
            ),
        ]

        for _ in range(2):
            client = _make_mock_client()
            client.upload_video.side_effect = [
                UploadResult(success=True, youtube_id="yt_aaa"),
                UploadResult(success=True, youtube_id="yt_bbb"),
            ]

            result = process(
                records, client, config,
                sleep_fn=_no_sleep,
                reference_time=now,
            )

            assert len(result) == 2
            assert result[0].clip_id == "clip_aaa_00000001"
            assert result[0].youtube_id == "yt_aaa"
            assert result[1].clip_id == "clip_bbb_00000001"
            assert result[1].youtube_id == "yt_bbb"


# ---------------------------------------------------------------------------
# Tests — Visibility Transition
# ---------------------------------------------------------------------------


class TestVisibilityTransition:
    """Tests for the visibility transition logic."""

    def test_eligible_clip_transitions_to_public(self) -> None:
        """Published clip past delay is updated to public."""
        config = _make_publisher_config()
        config["publisher"]["public_delay_minutes"] = 30

        # Published 60 minutes ago
        record = _make_storage_record(
            status="published",
            youtube_id="yt_vis_test_01",
            published_at="2026-03-27T11:00:00Z",
        )

        client = _make_mock_client(visibility_success=True)
        now = datetime(2026, 3, 27, 12, 0, 0, tzinfo=timezone.utc)

        result = check_visibility_transitions(
            [record], client, config, reference_time=now
        )

        assert len(result) == 1
        client.update_visibility.assert_called_once_with(
            "yt_vis_test_01", "public"
        )

    def test_clip_not_yet_eligible(self) -> None:
        """Published clip within delay window is not transitioned."""
        config = _make_publisher_config()
        config["publisher"]["public_delay_minutes"] = 30

        # Published only 10 minutes ago
        record = _make_storage_record(
            status="published",
            youtube_id="yt_vis_test_02",
            published_at="2026-03-27T11:50:00Z",
        )

        client = _make_mock_client()
        now = datetime(2026, 3, 27, 12, 0, 0, tzinfo=timezone.utc)

        result = check_visibility_transitions(
            [record], client, config, reference_time=now
        )

        assert len(result) == 1
        client.update_visibility.assert_not_called()

    def test_non_published_clips_skipped(self) -> None:
        """Non-published clips are not checked for visibility transition."""
        config = _make_publisher_config()

        records = [
            _make_storage_record(status="scheduled"),
            _make_storage_record(
                clip_id="failed_clip_00001",
                status="failed",
            ),
        ]

        client = _make_mock_client()
        now = _fixed_now()

        result = check_visibility_transitions(
            records, client, config, reference_time=now
        )

        assert len(result) == 2
        client.update_visibility.assert_not_called()

    def test_visibility_failure_logged_not_fatal(self) -> None:
        """Visibility update failure doesn't crash the function."""
        config = _make_publisher_config()
        config["publisher"]["public_delay_minutes"] = 30

        record = _make_storage_record(
            status="published",
            youtube_id="yt_vis_fail_01",
            published_at="2026-03-27T11:00:00Z",
        )

        client = _make_mock_client(visibility_success=False)
        client.update_visibility.return_value = VisibilityUpdateResult(
            success=False, error_message="API error"
        )
        now = datetime(2026, 3, 27, 12, 0, 0, tzinfo=timezone.utc)

        result = check_visibility_transitions(
            [record], client, config, reference_time=now
        )

        # Should still return the record, not crash
        assert len(result) == 1
        client.update_visibility.assert_called_once()

    def test_no_youtube_id_skipped(self) -> None:
        """Published clip without youtube_id is skipped."""
        config = _make_publisher_config()

        record = _make_storage_record(
            status="published",
            youtube_id=None,
            published_at="2026-03-27T11:00:00Z",
        )

        client = _make_mock_client()
        now = _fixed_now()

        result = check_visibility_transitions(
            [record], client, config, reference_time=now
        )

        assert len(result) == 1
        client.update_visibility.assert_not_called()

    def test_invalid_published_at_skipped(self) -> None:
        """Invalid published_at timestamp is handled gracefully."""
        config = _make_publisher_config()

        record = _make_storage_record(
            status="published",
            youtube_id="yt_invalid_time",
            published_at="not-a-date",
        )

        client = _make_mock_client()
        now = _fixed_now()

        result = check_visibility_transitions(
            [record], client, config, reference_time=now
        )

        assert len(result) == 1
        client.update_visibility.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — Idempotency
# ---------------------------------------------------------------------------


class TestPublishIdempotency:
    """Tests that publisher is idempotent."""

    def test_already_published_not_reuploaded(self) -> None:
        """Record with youtube_id is never re-uploaded."""
        config = _make_publisher_config()
        client = _make_mock_client()

        record = _make_storage_record(
            status="scheduled",
            youtube_id="already_uploaded",
        )

        result = publish_single(
            record, client, config,
            sleep_fn=_no_sleep,
            reference_time=_fixed_now(),
        )

        assert result.status == "published"
        assert result.youtube_id == "already_uploaded"
        client.upload_video.assert_not_called()

    def test_process_idempotent_on_rerun(self) -> None:
        """Running process twice: second run skips already-published clips."""
        config = _make_publisher_config()
        now = _fixed_now()

        records = [
            _make_storage_record(
                clip_id="clip_idem_000001",
                scheduled_at="2026-03-27T10:00:00Z",
            ),
        ]

        # First run: publishes
        client1 = _make_mock_client()
        result1 = process(
            records, client1, config,
            sleep_fn=_no_sleep,
            reference_time=now,
        )

        assert result1[0].status == "published"

        # Second run: pass published records back
        client2 = _make_mock_client()
        result2 = process(
            result1, client2, config,
            sleep_fn=_no_sleep,
            reference_time=now,
        )

        # Should not re-upload
        client2.upload_video.assert_not_called()
        assert result2[0].status == "published"


# ---------------------------------------------------------------------------
# Tests — Edge Cases
# ---------------------------------------------------------------------------


class TestPublishEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_scheduled_at_exactly_now(self) -> None:
        """Clip with scheduled_at exactly at reference time is eligible."""
        config = _make_publisher_config()
        client = _make_mock_client()
        now = datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc)

        record = _make_storage_record(
            scheduled_at="2026-03-27T10:00:00Z",
        )

        result = process(
            [record], client, config,
            sleep_fn=_no_sleep,
            reference_time=now,
        )

        assert result[0].status == "published"

    def test_mixed_statuses_batch(self) -> None:
        """Batch with mixed statuses handles each correctly."""
        config = _make_publisher_config()
        now = _fixed_now()

        records = [
            _make_storage_record(
                clip_id="sched_eligible_01",
                status="scheduled",
                scheduled_at="2026-03-27T10:00:00Z",
            ),
            _make_storage_record(
                clip_id="already_pubbed_01",
                status="published",
                youtube_id="yt_existing",
                published_at="2026-03-27T08:00:00Z",
            ),
            _make_storage_record(
                clip_id="queued_not_ready",
                status="queued",
                scheduled_at=None,
            ),
            _make_storage_record(
                clip_id="failed_previous1",
                status="failed",
                error_message="prev error",
            ),
        ]

        client = _make_mock_client()

        result = process(
            records, client, config,
            sleep_fn=_no_sleep,
            reference_time=now,
        )

        statuses = {r.clip_id: r.status for r in result}
        assert statuses["sched_eligible_01"] == "published"
        assert statuses["already_pubbed_01"] == "published"
        assert statuses["queued_not_ready"] == "queued"
        assert statuses["failed_previous1"] == "failed"

        # Only the scheduled-eligible clip should trigger upload
        assert client.upload_video.call_count == 1
