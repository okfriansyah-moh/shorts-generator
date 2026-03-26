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
        image_path: Absolute filesystem path to thumbnail JPEG.
        resolution: Thumbnail resolution. Must be (1280, 720).
        text_overlay: Text rendered on the thumbnail. Up to max_text_words (default 3). Non-empty.
        face_visible: Whether a face is present in the thumbnail.
        frame_timestamp_ms: Source frame timestamp used. >= clip start_time.
        frame_score: Frame selection score. >= 0.0.
    """

    clip_id: str
    image_path: str
    resolution: tuple[int, int]
    text_overlay: str
    face_visible: bool
    frame_timestamp_ms: int
    frame_score: float
