"""Compositor module — face-aware video composition.

Combines gameplay footage and face camera region into a single 9:16
vertical frame at 1080×1920. Supports both gameplay (split/fallback
layout) and podcast (smart-crop) video types via dispatcher.

Public API:
    process(clip, face_result, ingestion_result, config) -> CompositeStream
    process_podcast(clip, face_result, ingestion_result, config) -> CompositeStream
"""

from .compose import process
from .podcast import process_podcast

__all__ = ["process", "process_podcast"]
