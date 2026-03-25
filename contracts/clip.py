"""ClipDefinition and ClipList DTOs for Shorts Factory.

Produced by the clip_builder module. ClipDefinition consumed by
hook_generator, subtitle, compositor, thumbnail, metadata, storage.
ClipList consumed by the orchestrator to drive per-clip processing.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.scoring import ScoredScene


@dataclass(frozen=True)
class ClipDefinition:
    """Frozen DTO representing a single clip built from contiguous scenes.

    Fields:
        clip_id: Deterministic identifier. SHA256(video_id + str(start_time) + str(end_time))[:16].
        video_id: Parent video reference. 16 lowercase hex chars.
        scenes: Constituent scenes in temporal order. Non-empty, sorted by start_time ASC.
        start_time: Clip start in milliseconds. Equals first scene's start_time.
        end_time: Clip end in milliseconds. Equals last scene's end_time.
        duration: Clip duration in seconds. 30.0 <= duration <= 60.0.
        average_score: Mean composite_score of constituent scenes. 0.0-1.0.
        clip_index: Position in the batch (0-based). Unique within a ClipList.
    """

    clip_id: str
    video_id: str
    scenes: tuple[ScoredScene, ...]
    start_time: int
    end_time: int
    duration: float
    average_score: float
    clip_index: int


@dataclass(frozen=True)
class ClipList:
    """Frozen DTO representing all selected clips for a video.

    Fields:
        video_id: Parent video reference. 16 lowercase hex chars.
        clips: All selected clips sorted by start_time ASC. Non-empty.
        total_clips: Number of clips selected. 1 <= total_clips <= 20.
        clips_rejected: Number of candidate clips rejected. >= 0.
    """

    video_id: str
    clips: tuple[ClipDefinition, ...]
    total_clips: int
    clips_rejected: int
