"""ThumbnailResult DTO for Shorts Factory.

Produced by the thumbnail module. Consumed by the storage module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThumbnailResult:
    """Frozen DTO representing a generated thumbnail for a clip.

    Fields:
        clip_id: Reference to parent clip. 16 lowercase hex chars.
        video_id: Parent video reference. 16 lowercase hex chars.
        thumbnail_path: Absolute path to the generated JPEG file. Non-empty.
        width: Thumbnail width in pixels. Must be 1280.
        height: Thumbnail height in pixels. Must be 720.
        text_overlay: Text used as overlay (hook words). Max 3 words.
    """

    clip_id: str
    video_id: str
    thumbnail_path: str
    width: int
    height: int
    text_overlay: str
