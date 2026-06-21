"""Tennis compositor — thin per-sport wrapper over sports_utils.py.

Handles tennis-specific defaults and extension points without duplicating
the crop logic, which lives entirely in sports_utils.py.

Default layout: sports_center_crop
  Tennis courts are filmed from a centered position; the action is typically
  near the center of the frame, making center crop the best default.

Extension points (add here without touching other sports):
  - Court-aware crop (split frame at net position)
  - Scoreboard safe-zone exclusion
"""

from __future__ import annotations

from typing import Optional

from contracts.clip import ClipDefinition
from contracts.compositor import CompositeStream
from contracts.face import FaceDetectionResult
from contracts.ingestion import IngestionResult
from contracts.strategies import SportsFramePlan

from .sports_utils import process_sports

_SPORT = "tennis"
_DEFAULT_LAYOUT = "sports_center_crop"


def process_sports_tennis(
    clip: ClipDefinition,
    face_result: FaceDetectionResult,
    ingestion_result: IngestionResult,
    config: dict,
    plan: Optional[SportsFramePlan] = None,
) -> CompositeStream:
    """Compose a tennis clip into a silent 9:16 vertical composite.

    Delegates all crop logic to sports_utils.process_sports(). Tennis-specific
    logic (e.g. court-aware crop, scoreboard safe zones) can be added here
    before the delegate call without affecting football or future sports.

    Args:
        clip:             Clip definition with scene references and timing.
        face_result:      Face detection output for the full video.
        ingestion_result: Source video metadata (path, resolution, fps).
        config:           Full pipeline configuration dict (already overlaid
                          with sports_* and sports_tennis_* sections).
        plan:             Pre-computed SportsFramePlan from the orchestrator.
                          None for letterbox/center_crop layouts (no strategy needed).

    Returns:
        CompositeStream DTO with composite_path and layout set.
    """
    return process_sports(
        clip=clip,
        face_result=face_result,
        ingestion_result=ingestion_result,
        config=config,
        plan=plan,
        sport=_SPORT,
        default_layout=_DEFAULT_LAYOUT,
    )
