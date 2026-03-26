"""MetadataResult DTO for Shorts Factory.

Produced by the metadata module. Consumed by the storage module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetadataResult:
    """Frozen DTO representing generated metadata for a clip.

    Fields:
        clip_id: Reference to parent clip. 16 lowercase hex chars.
        title: Video title. 40-60 characters. Contains 1-2 emojis.
        description: Video description. 150-300 characters. Contains hashtags.
        tags: Video tags. 10-15 tags. Total characters <= 500.
        category: YouTube category. One of: "Gaming", "Entertainment".
    """

    clip_id: str
    title: str
    description: str
    tags: tuple[str, ...]
    category: str
