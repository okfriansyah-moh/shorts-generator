"""CompositeStream DTO for Shorts Factory.

Produced by the compositor module. Consumed by the renderer module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompositeStream:
    """Frozen DTO representing a composited video stream ready for rendering.

    Fields:
        clip_id: Reference to parent clip. 16 lowercase hex chars.
        video_id: Parent video reference. 16 lowercase hex chars.
        composite_path: Absolute path to the composite MP4 file. Non-empty.
        source_audio_path: Absolute path to original gameplay audio. Non-empty.
        resolution: (width, height) in pixels. Must be (1080, 1920).
        layout: Layout type used. One of "face_gameplay_split", "gameplay_only_zoom".
        duration_seconds: Composite video duration in seconds. > 0.0.
    """

    clip_id: str
    video_id: str
    composite_path: str
    source_audio_path: str
    resolution: tuple[int, int]
    layout: str
    duration_seconds: float
