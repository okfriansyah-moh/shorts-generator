"""Unit tests for the storage module."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from contracts.metadata import MetadataResult
from contracts.render import RenderedClip
from contracts.storage import StorageRecord
from contracts.thumbnail import ThumbnailResult
from modules.storage import process
from modules.storage.store import cleanup_orphaned_temp_files


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_rendered_clip(
    tmp_dir: str, clip_id: str = "abcdef0123456789", **overrides
) -> RenderedClip:
    """Create a RenderedClip with a real temp file."""
    video_path = os.path.join(tmp_dir, "rendered", f"{clip_id}.mp4")
    os.makedirs(os.path.dirname(video_path), exist_ok=True)
    with open(video_path, "wb") as f:
        f.write(b"\x00" * 1024)

    defaults = {
        "clip_id": clip_id,
        "video_id": "a1b2c3d4e5f67890",
        "output_path": video_path,
        "duration_seconds": 45.0,
        "resolution": (1080, 1920),
        "codec": "h264",
        "fps": 30,
        "file_size_bytes": 1024,
        "has_narration": True,
        "has_subtitles": True,
    }
    defaults.update(overrides)
    return RenderedClip(**defaults)


def _make_thumbnail_result(
    tmp_dir: str, clip_id: str = "abcdef0123456789", **overrides
) -> ThumbnailResult:
    """Create a ThumbnailResult with a real temp file."""
    image_path = os.path.join(tmp_dir, "thumbnails", f"{clip_id}.jpg")
    os.makedirs(os.path.dirname(image_path), exist_ok=True)
    with open(image_path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # Minimal JPEG header

    defaults = {
        "clip_id": clip_id,
        "image_path": image_path,
        "resolution": (1280, 720),
        "text_overlay": "Epic Play",
        "face_visible": True,
        "frame_timestamp_ms": 5000,
        "frame_score": 0.85,
    }
    defaults.update(overrides)
    return ThumbnailResult(**defaults)


def _make_metadata_result(
    clip_id: str = "abcdef0123456789", **overrides
) -> MetadataResult:
    """Create a MetadataResult with valid field values."""
    defaults = {
        "clip_id": clip_id,
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
    }
    defaults.update(overrides)
    return MetadataResult(**defaults)


def _make_config(tmp_dir: str) -> dict:
    """Create test config pointing to tmp_dir."""
    output_dir = os.path.join(tmp_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    return {
        "paths": {
            "output_dir": output_dir,
            "temp_dir": os.path.join(tmp_dir, "temp"),
            "database": os.path.join(tmp_dir, "test.db"),
        },
        "scheduler": {
            "publish_time_utc": "10:00",
        },
    }


# ---------------------------------------------------------------------------
# Tests — Normal Operation
# ---------------------------------------------------------------------------


class TestStorageProcess:
    """Tests for the storage process function."""

    def test_normal_storage(self) -> None:
        """Storing a clip returns a StorageRecord with status 'queued'."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            rendered = _make_rendered_clip(tmp_dir)
            thumbnail = _make_thumbnail_result(tmp_dir)
            metadata = _make_metadata_result()

            result = process(rendered, thumbnail, metadata, config, composite_score=0.75)

            assert isinstance(result, StorageRecord)
            assert result.clip_id == "abcdef0123456789"
            assert result.video_id == "a1b2c3d4e5f67890"
            assert result.status == "queued"
            assert result.composite_score == 0.75
            assert result.title == metadata.title
            assert result.description == metadata.description
            assert result.tags == metadata.tags
            assert result.category == "Gaming"
            assert result.scheduled_at is None
            assert result.published_at is None
            assert result.youtube_id is None
            assert result.error_message is None
            assert result.retry_count == 0

    def test_file_paths_are_relative(self) -> None:
        """All file paths in StorageRecord are relative."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            rendered = _make_rendered_clip(tmp_dir)
            thumbnail = _make_thumbnail_result(tmp_dir)
            metadata = _make_metadata_result()

            result = process(rendered, thumbnail, metadata, config)

            for key in ("video", "thumbnail", "metadata"):
                path = result.file_paths[key]
                assert path, f"file_paths[{key!r}] should not be empty"
                assert not os.path.isabs(path), (
                    f"file_paths[{key!r}] = {path!r} should be relative"
                )

    def test_file_paths_has_all_required_keys(self) -> None:
        """file_paths dict contains all required keys."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            rendered = _make_rendered_clip(tmp_dir)
            thumbnail = _make_thumbnail_result(tmp_dir)
            metadata = _make_metadata_result()

            result = process(rendered, thumbnail, metadata, config)

            required_keys = {"video", "thumbnail", "metadata", "subtitles", "narration"}
            assert set(result.file_paths.keys()) == required_keys

    def test_metadata_json_written(self) -> None:
        """A metadata.json file is created in the clip directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            output_dir = config["paths"]["output_dir"]
            rendered = _make_rendered_clip(tmp_dir)
            thumbnail = _make_thumbnail_result(tmp_dir)
            metadata = _make_metadata_result()

            process(rendered, thumbnail, metadata, config)

            metadata_path = os.path.join(
                output_dir,
                "a1b2c3d4e5f67890",
                "clips",
                "abcdef0123456789",
                "metadata.json",
            )
            assert os.path.isfile(metadata_path)

            with open(metadata_path) as f:
                data = json.load(f)
            assert data["title"] == metadata.title
            assert data["category"] == "Gaming"
            assert "tags" in data

    def test_video_and_thumbnail_copied(self) -> None:
        """Rendered video and thumbnail are copied to the clip directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            output_dir = config["paths"]["output_dir"]
            rendered = _make_rendered_clip(tmp_dir)
            thumbnail = _make_thumbnail_result(tmp_dir)
            metadata = _make_metadata_result()

            process(rendered, thumbnail, metadata, config)

            clip_dir = os.path.join(
                output_dir, "a1b2c3d4e5f67890", "clips", "abcdef0123456789"
            )
            assert os.path.isfile(os.path.join(clip_dir, "final.mp4"))
            assert os.path.isfile(os.path.join(clip_dir, "thumbnail.jpg"))

    def test_created_at_is_iso_format(self) -> None:
        """created_at field is in ISO 8601 format."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            rendered = _make_rendered_clip(tmp_dir)
            thumbnail = _make_thumbnail_result(tmp_dir)
            metadata = _make_metadata_result()

            result = process(rendered, thumbnail, metadata, config)

            # Should not raise
            assert result.created_at.endswith("Z")
            assert "T" in result.created_at


# ---------------------------------------------------------------------------
# Tests — Idempotency
# ---------------------------------------------------------------------------


class TestStorageIdempotency:
    """Tests that storage is idempotent (safe to call twice)."""

    def test_double_store_produces_same_result(self) -> None:
        """Calling process twice returns equivalent records."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            rendered = _make_rendered_clip(tmp_dir)
            thumbnail = _make_thumbnail_result(tmp_dir)
            metadata = _make_metadata_result()

            result1 = process(rendered, thumbnail, metadata, config)
            result2 = process(rendered, thumbnail, metadata, config)

            assert result1.clip_id == result2.clip_id
            assert result1.video_id == result2.video_id
            assert result1.status == result2.status
            assert result1.file_paths == result2.file_paths
            assert result1.title == result2.title

    def test_metadata_not_overwritten_on_rerun(self) -> None:
        """metadata.json is not rewritten on second call."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            output_dir = config["paths"]["output_dir"]
            rendered = _make_rendered_clip(tmp_dir)
            thumbnail = _make_thumbnail_result(tmp_dir)
            metadata = _make_metadata_result()

            process(rendered, thumbnail, metadata, config)

            metadata_path = os.path.join(
                output_dir,
                "a1b2c3d4e5f67890",
                "clips",
                "abcdef0123456789",
                "metadata.json",
            )
            mtime1 = os.path.getmtime(metadata_path)

            # Small delay not needed — file already exists, skip-if-exists
            process(rendered, thumbnail, metadata, config)
            mtime2 = os.path.getmtime(metadata_path)

            assert mtime1 == mtime2


# ---------------------------------------------------------------------------
# Tests — Error Cases
# ---------------------------------------------------------------------------


class TestStorageErrors:
    """Tests for error handling in storage."""

    def test_missing_rendered_video_raises(self) -> None:
        """FileNotFoundError if rendered video doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            thumbnail = _make_thumbnail_result(tmp_dir)
            metadata = _make_metadata_result()

            # Create a RenderedClip pointing to nonexistent file
            rendered = RenderedClip(
                clip_id="abcdef0123456789",
                video_id="a1b2c3d4e5f67890",
                output_path="/nonexistent/video.mp4",
                duration_seconds=45.0,
                resolution=(1080, 1920),
                codec="h264",
                fps=30,
                file_size_bytes=1024,
                has_narration=True,
                has_subtitles=True,
            )

            with pytest.raises(FileNotFoundError, match="rendered video"):
                process(rendered, thumbnail, metadata, config)

    def test_missing_thumbnail_raises(self) -> None:
        """FileNotFoundError if thumbnail doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            rendered = _make_rendered_clip(tmp_dir)
            metadata = _make_metadata_result()

            thumbnail = ThumbnailResult(
                clip_id="abcdef0123456789",
                image_path="/nonexistent/thumb.jpg",
                resolution=(1280, 720),
                text_overlay="Epic",
                face_visible=True,
                frame_timestamp_ms=5000,
                frame_score=0.85,
            )

            with pytest.raises(FileNotFoundError, match="thumbnail"):
                process(rendered, thumbnail, metadata, config)


# ---------------------------------------------------------------------------
# Tests — Orphaned File Cleanup
# ---------------------------------------------------------------------------


class TestOrphanedCleanup:
    """Tests for orphaned .tmp file cleanup."""

    def test_removes_tmp_files(self) -> None:
        """cleanup_orphaned_temp_files removes .tmp files."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create some .tmp files
            tmp1 = os.path.join(tmp_dir, "metadata.json.tmp")
            tmp2 = os.path.join(tmp_dir, "subdir", "video.mp4.tmp")
            os.makedirs(os.path.join(tmp_dir, "subdir"), exist_ok=True)
            for path in (tmp1, tmp2):
                with open(path, "w") as f:
                    f.write("orphaned")

            # And a non-tmp file that should survive
            real_file = os.path.join(tmp_dir, "real_file.json")
            with open(real_file, "w") as f:
                f.write("keep me")

            removed = cleanup_orphaned_temp_files(tmp_dir)

            assert removed == 2
            assert not os.path.exists(tmp1)
            assert not os.path.exists(tmp2)
            assert os.path.exists(real_file)

    def test_nonexistent_dir_returns_zero(self) -> None:
        """Cleanup on nonexistent dir returns 0 without error."""
        removed = cleanup_orphaned_temp_files("/nonexistent/dir")
        assert removed == 0

    def test_empty_dir_returns_zero(self) -> None:
        """Cleanup on empty dir returns 0."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            removed = cleanup_orphaned_temp_files(tmp_dir)
            assert removed == 0


# ---------------------------------------------------------------------------
# Tests — Determinism
# ---------------------------------------------------------------------------


class TestStorageDeterminism:
    """Tests that storage output is deterministic."""

    def test_file_paths_keys_sorted(self) -> None:
        """file_paths keys are in sorted order."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            rendered = _make_rendered_clip(tmp_dir)
            thumbnail = _make_thumbnail_result(tmp_dir)
            metadata = _make_metadata_result()

            result = process(rendered, thumbnail, metadata, config)

            keys = list(result.file_paths.keys())
            assert keys == sorted(keys)
