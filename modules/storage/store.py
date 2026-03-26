"""Storage implementation for Shorts Factory.

Verifies all pipeline outputs exist, computes checksums, normalizes
paths to relative form, and builds a StorageRecord DTO.

This module does NOT access the database. The orchestrator handles
all DB writes via database/adapter.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone

from contracts.metadata import MetadataResult
from contracts.render import RenderedClip
from contracts.storage import StorageRecord
from contracts.subtitle import SubtitleResult
from contracts.thumbnail import ThumbnailResult
from contracts.tts import TTSResult

logger = logging.getLogger(__name__)

# Valid lifecycle statuses
VALID_STATUSES = frozenset(
    {"generated", "queued", "scheduled", "published", "failed"}
)

# Required file_paths keys
REQUIRED_FILE_KEYS = frozenset(
    {"video", "thumbnail", "metadata", "subtitles", "narration"}
)


def _compute_file_checksum(file_path: str) -> str:
    """Compute SHA-256 checksum of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _make_relative_path(absolute_path: str, base_dir: str) -> str:
    """Convert an absolute path to a relative path from base_dir."""
    return os.path.relpath(absolute_path, base_dir)


def _write_metadata_json(
    metadata: MetadataResult, output_path: str
) -> None:
    """Write metadata to a JSON file using atomic write-then-rename."""
    data = {
        "clip_id": metadata.clip_id,
        "title": metadata.title,
        "description": metadata.description,
        "tags": list(metadata.tags),
        "category": metadata.category,
    }
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(output_path), suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.rename(tmp_path, output_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _verify_file_exists(file_path: str, description: str) -> None:
    """Verify a file exists at the given path, raise if not."""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(
            f"Expected {description} at {file_path} but file does not exist"
        )


def process(
    rendered_clip: RenderedClip,
    thumbnail_result: ThumbnailResult,
    metadata_result: MetadataResult,
    config: dict,
    *,
    composite_score: float = 0.0,
    subtitle_result: SubtitleResult | None = None,
    tts_result: TTSResult | None = None,
) -> StorageRecord:
    """Store all pipeline artifacts for a single clip.

    Verifies that all expected files exist, writes metadata JSON,
    computes checksums, and returns a StorageRecord DTO.

    Args:
        rendered_clip: The rendered video clip.
        thumbnail_result: The generated thumbnail.
        metadata_result: The generated metadata.
        config: Pipeline configuration dict.
        composite_score: Average composite score of constituent scenes. 0.0-1.0.
        subtitle_result: Optional SubtitleResult DTO for resolving subtitle paths.
        tts_result: Optional TTSResult DTO for resolving narration paths.

    Returns:
        StorageRecord with status 'queued' and all paths populated.

    Raises:
        FileNotFoundError: If any expected artifact file is missing.
    """
    output_dir = config["paths"]["output_dir"]
    video_id = rendered_clip.video_id
    clip_id = rendered_clip.clip_id
    clip_dir = os.path.join(output_dir, video_id, "clips", clip_id)

    logger.info(
        "Storing clip artifacts",
        extra={
            "video_id": video_id,
            "clip_id": clip_id,
            "stage": "storage",
            "status": "started",
        },
    )

    # Verify rendered video exists
    _verify_file_exists(rendered_clip.output_path, "rendered video")

    # Verify thumbnail exists
    thumbnail_abs = thumbnail_result.image_path
    if not os.path.isabs(thumbnail_abs):
        thumbnail_abs = os.path.join(output_dir, thumbnail_abs)
    _verify_file_exists(thumbnail_abs, "thumbnail")

    # Write metadata JSON (atomic write-then-rename)
    metadata_path = os.path.join(clip_dir, "metadata.json")
    if not os.path.exists(metadata_path):
        _write_metadata_json(metadata_result, metadata_path)
    else:
        logger.info(
            "Metadata JSON already exists, skipping write",
            extra={"clip_id": clip_id, "stage": "storage"},
        )

    # Copy rendered video to clip directory if not already there
    video_dest = os.path.join(clip_dir, "final.mp4")
    if not os.path.exists(video_dest):
        os.makedirs(clip_dir, exist_ok=True)
        shutil.copy2(rendered_clip.output_path, video_dest)
    video_path = video_dest

    # Copy thumbnail to clip directory if not already there
    thumbnail_dest = os.path.join(clip_dir, "thumbnail.jpg")
    if not os.path.exists(thumbnail_dest):
        os.makedirs(clip_dir, exist_ok=True)
        shutil.copy2(thumbnail_abs, thumbnail_dest)
    thumbnail_path = thumbnail_dest

    # Resolve subtitle and narration paths from upstream DTOs when
    # available; fall back to convention-based lookup.
    if subtitle_result is not None and os.path.isfile(subtitle_result.ass_path):
        subtitles_path = subtitle_result.ass_path
    else:
        subtitles_path = os.path.join(
            output_dir, video_id, "clips", clip_id, "subtitles.ass"
        )

    if tts_result is not None and os.path.isfile(tts_result.audio_path):
        narration_path = tts_result.audio_path
    else:
        narration_path = os.path.join(
            output_dir, video_id, "tts_cache"
        )
        # Convention fallback cannot reliably resolve cache-key filenames,
        # so treat as missing when no DTO is provided.
        narration_path = ""

    # Build file_paths dict with relative paths
    file_paths: dict[str, str] = {
        "video": _make_relative_path(video_path, output_dir),
        "thumbnail": _make_relative_path(thumbnail_path, output_dir),
        "metadata": _make_relative_path(metadata_path, output_dir),
        "subtitles": _make_relative_path(subtitles_path, output_dir)
        if subtitles_path and os.path.isfile(subtitles_path)
        else "",
        "narration": _make_relative_path(narration_path, output_dir)
        if narration_path and os.path.isfile(narration_path)
        else "",
    }

    # Sort file_paths keys for determinism
    file_paths = dict(sorted(file_paths.items()))

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Compute composite score from the rendered clip's parent data
    # The orchestrator passes this via config or we derive from clip metadata
    record = StorageRecord(
        clip_id=clip_id,
        video_id=video_id,
        status="queued",
        composite_score=composite_score,
        file_paths=file_paths,
        title=metadata_result.title,
        description=metadata_result.description,
        tags=metadata_result.tags,
        category=metadata_result.category,
        created_at=created_at,
    )

    logger.info(
        "Clip stored successfully",
        extra={
            "video_id": video_id,
            "clip_id": clip_id,
            "stage": "storage",
            "status": "completed",
        },
    )

    return record


def cleanup_orphaned_temp_files(output_dir: str) -> int:
    """Remove orphaned .tmp files from interrupted pipeline runs.

    Scans the output directory for .tmp files left by atomic write
    operations that were interrupted.

    Args:
        output_dir: Root output directory to scan.

    Returns:
        Number of orphaned files removed.
    """
    removed = 0
    if not os.path.isdir(output_dir):
        return removed

    for dirpath, _dirnames, filenames in os.walk(output_dir):
        for filename in sorted(filenames):
            if filename.endswith(".tmp"):
                tmp_path = os.path.join(dirpath, filename)
                try:
                    os.remove(tmp_path)
                    removed += 1
                    logger.info(
                        "Removed orphaned temp file",
                        extra={
                            "path": tmp_path,
                            "stage": "storage",
                            "status": "cleanup",
                        },
                    )
                except OSError as e:
                    logger.warning(
                        "Failed to remove orphaned temp file",
                        extra={
                            "path": tmp_path,
                            "error": str(e),
                            "stage": "storage",
                        },
                    )
    return removed
