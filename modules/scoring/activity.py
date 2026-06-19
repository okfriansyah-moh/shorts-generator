"""Scene activity scoring — fast stub."""
from __future__ import annotations
from contracts.scene import SceneList

def compute_scene_activities(scene_list: SceneList, file_path: str) -> dict[str, float]:
    return {s.scene_id: 0.5 for s in scene_list.scenes}
