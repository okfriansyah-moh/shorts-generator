"""StorageRecord DTO for Shorts Factory.

Produced by the storage module. Consumed by scheduler and publisher modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StorageRecord:
    """Frozen DTO representing a fully stored clip with all artifacts.

    Fields:
        clip_id: Primary identifier. 16 lowercase hex chars.
        video_id: Parent video reference. 16 lowercase hex chars.
        status: Lifecycle state. One of: generated, queued, scheduled,
                published, failed.
        composite_score: Clip average score for scheduling priority. 0.0-1.0.
        file_paths: Paths to all stored artifacts. Required keys: video,
                    thumbnail, metadata, subtitles, narration.
        title: Stored title from MetadataResult. 40-60 characters.
        description: Stored description. 150-300 characters.
        tags: Stored tags. 10-15 tags.
        category: YouTube category. "Gaming" or "Entertainment".
        created_at: Record creation timestamp. ISO 8601 format.
        scheduled_at: Assigned publish timestamp. ISO 8601 or None.
        published_at: Actual publish timestamp. ISO 8601 or None.
        youtube_id: YouTube video ID after upload. None until published.
        tiktok_id: TikTok video ID after upload. None until published.
        instagram_id: Instagram media ID after upload. None until published.
        facebook_id: Facebook video ID after upload. None until published.
        error_message: Failure description. None unless status is "failed".
        retry_count: Number of publish retries attempted. 0-3.
    """

    clip_id: str
    video_id: str
    status: str
    composite_score: float
    file_paths: dict[str, str]
    title: str
    description: str
    tags: tuple[str, ...]
    category: str
    created_at: str
    scheduled_at: str | None = field(default=None)
    published_at: str | None = field(default=None)
    youtube_id: str | None = field(default=None)
    tiktok_id: str | None = field(default=None)
    instagram_id: str | None = field(default=None)
    facebook_id: str | None = field(default=None)
    error_message: str | None = field(default=None)
    retry_count: int = field(default=0)
