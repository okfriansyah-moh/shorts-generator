"""MetadataResult DTO for Shorts Factory.

Produced by the metadata module. Consumed by the storage module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetadataResult:
    """Frozen DTO representing YouTube-ready metadata for a clip.

    Fields:
        clip_id: Reference to parent clip. 16 lowercase hex chars.
        video_id: Parent video reference. 16 lowercase hex chars.
        title: Video title. 40–60 characters.
        description: Video description. 150–300 characters.
        tags: YouTube tags. Tuple of 10–15 lowercase strings. Sorted ASC.
    """

    clip_id: str
    video_id: str
    title: str
    description: str
    tags: tuple[str, ...]
