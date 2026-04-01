"""Strategies module — pre-composition decision logic.

Strategies compute crop plans and framing decisions from multi-modal
signals (transcript, face detection) and return frozen DTO plans.
The compositor executes plans without re-evaluating decisions.

Public API:
    generate_plan(clip, transcript, face_result, ingestion_result, config)
        -> PodcastFramePlan
"""

from .podcast_strategy import generate_plan

__all__ = ["generate_plan"]
