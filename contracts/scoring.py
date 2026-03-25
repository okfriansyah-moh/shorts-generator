"""Scoring DTOs for Shorts Factory.

Produced by the scoring module. Consumed by the clip_builder module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoredScene:
    """Frozen DTO representing a scene with all five factor scores computed.

    Fields:
        scene_id: References parent SceneSegment.scene_id.
        video_id: Parent video reference. 16 lowercase hex chars.
        start_time: Scene start in milliseconds. >= 0.
        end_time: Scene end in milliseconds. > start_time.
        duration: Scene duration in seconds. Range: [3.0, 20.0].
        keyword_score: Engagement keyword density. 0.0–1.0.
        audio_energy: Normalised RMS audio energy. 0.0–1.0.
        face_presence: Fraction of frames with face detected. 0.0–1.0.
        scene_activity: Normalised inter-frame pixel difference. 0.0–1.0.
        sentence_density: Words-per-second score (optimal 2–4 wps). 0.0–1.0.
        composite_score: Weighted average of all factors, normalised. 0.0–1.0.
    """

    scene_id: str
    video_id: str
    start_time: int
    end_time: int
    duration: float
    keyword_score: float
    audio_energy: float
    face_presence: float
    scene_activity: float
    sentence_density: float
    composite_score: float


@dataclass(frozen=True)
class ScoredSceneList:
    """Frozen DTO representing all scored scenes for a video.

    Fields:
        video_id: Parent video reference. 16 lowercase hex chars.
        scenes: Scored scenes sorted by composite_score DESC, start_time ASC.
                Non-empty.
    """

    video_id: str
    scenes: tuple[ScoredScene, ...]
