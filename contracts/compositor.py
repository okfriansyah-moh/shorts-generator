"""CompositeStream DTO for Shorts Factory.

Produced by the compositor module. Consumed by the renderer module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompositeStream:
    """Frozen DTO representing a compositor output for a single clip.

    Fields:
        clip_id: Deterministic clip identifier. SHA256(video_id + str(start_time) + str(end_time))[:16].
        video_id: Parent video reference. 16 lowercase hex chars.
        composite_path: Absolute path to the intermediate composite MP4. Silent (no audio).
        source_audio_path: Absolute path to original gameplay audio for audio mixing. Non-empty.
        resolution: Output resolution as (width, height). Always (1080, 1920).
        layout: Composition layout. 'face_gameplay_split' or 'gameplay_only_zoom'.
        duration_seconds: Duration of composite video in seconds. > 0.
        has_face: True if face data was available and visibility >= 0.3.
        source_fps: Frame rate of composite output, as configured (e.g., pipeline.output_framerate; typically 30.0).
        start_time_ms: Clip start offset in the source video, in milliseconds. >= 0.
    """

    clip_id: str
    video_id: str
    composite_path: str
    source_audio_path: str
    resolution: tuple[int, int]
    layout: str
    duration_seconds: float
    has_face: bool
    source_fps: float
    start_time_ms: int = 0
