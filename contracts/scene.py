"""SceneSegment and SceneList DTOs for Shorts Factory.

Produced by the scene_splitter module. Consumed by transcription,
face_detection, audio_analysis, scoring, and clip_builder.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SceneSegment:
    """Frozen DTO representing a single detected scene boundary.

    Fields:
        scene_id: "{video_id}_{start_time}_{end_time}". Globally unique per video.
        video_id: Parent video reference. 16 lowercase hex chars.
        start_time: Scene start in milliseconds. >= 0, < end_time.
        end_time: Scene end in milliseconds. > start_time.
        duration: Scene duration in seconds. Range: [3.0, 20.0].
    """

    scene_id: str
    video_id: str
    start_time: int
    end_time: int
    duration: float


@dataclass(frozen=True)
class SceneList:
    """Frozen DTO representing the full ordered scene list for a video.

    Fields:
        video_id: Parent video reference. 16 lowercase hex chars.
        scenes: Ordered tuple of SceneSegment, sorted by start_time ASC. Non-empty.
        total_duration: Sum of all scene durations in seconds.
    """

    video_id: str
    scenes: tuple[SceneSegment, ...]
    total_duration: float
