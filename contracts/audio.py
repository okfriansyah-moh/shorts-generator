"""Audio energy DTOs for Shorts Factory.

Produced by the audio_analysis module. Consumed by the scoring module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SceneAudioEnergy:
    """Frozen DTO representing audio energy for a single scene.

    Fields:
        scene_id: Reference to parent scene. Matches SceneSegment.scene_id.
        rms_energy: RMS audio energy for this scene. >= 0.0.
        normalized_energy: Energy normalized to [0, 1] within video. 0.0–1.0.
            Computed as (rms_energy - video_min_rms) / (video_max_rms - video_min_rms).
            If video_max_rms == video_min_rms, value is 0.0.
    """

    scene_id: str
    rms_energy: float
    normalized_energy: float


@dataclass(frozen=True)
class AudioEnergyData:
    """Frozen DTO representing audio energy analysis for the entire video.

    Fields:
        video_id: Parent video reference. 16 lowercase hex chars.
        scene_energies: Per-scene energy measurements. One entry per scene.
        video_min_rms: Minimum RMS energy across all scenes. >= 0.0.
        video_max_rms: Maximum RMS energy. >= video_min_rms.
        video_mean_rms: Mean RMS energy. video_min_rms <= video_mean_rms <= video_max_rms.
    """

    video_id: str
    scene_energies: tuple[SceneAudioEnergy, ...]
    video_min_rms: float
    video_max_rms: float
    video_mean_rms: float
