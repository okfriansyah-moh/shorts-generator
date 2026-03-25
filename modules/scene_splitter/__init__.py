"""Scene splitter module for Shorts Factory.

Public interface: split_scenes(ingestion_result, config) -> SceneList
"""

from .split import split_scenes, SceneSplitterError

__all__ = ["split_scenes", "SceneSplitterError"]
