"""Compositor module — face-aware video composition.

Combines gameplay footage and face camera region into a single 9:16
vertical frame at 1080×1920.

Public API:
    process(clip, face_result, ingestion_result, config) -> CompositeStream
"""

from .compose import process

__all__ = ["process"]
