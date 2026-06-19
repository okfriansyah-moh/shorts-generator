"""Audio analysis — fast stub (uniform energy, no ffmpeg)."""
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from contracts.audio import AudioEnergyData, SceneAudioEnergy
if TYPE_CHECKING:
    from contracts.ingestion import IngestionResult
    from contracts.scene import SceneList

def analyze_audio(
    ingestion_result: "IngestionResult",
    scene_list: "SceneList",
    config: dict[Any, Any],
) -> AudioEnergyData:
    energies = tuple(
        SceneAudioEnergy(scene_id=s.scene_id, rms_energy=0.5, normalized_energy=0.5)
        for s in scene_list.scenes
    )
    return AudioEnergyData(
        video_id=ingestion_result.video_id,
        scene_energies=energies,
        video_min_rms=0.5,
        video_max_rms=0.5,
        video_mean_rms=0.5,
    )
