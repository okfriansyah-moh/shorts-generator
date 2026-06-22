"""Football compositor — thin per-sport wrapper over sports_utils.py.

Handles football-specific defaults and extension points without duplicating
the crop logic, which lives entirely in sports_utils.py.

Default layout: sports_action_crop
  Football is filmed from wide angles; the ball and players move across the
  full width of the frame. Action crop (anchored on tracked motion) produces
  far better results than a static center crop.

Extension points (add here without touching other sports):
  - Wide-field bias (expand crop window horizontally for pitch coverage)
  - Goal celebration safe-zone to keep net in frame
"""

from __future__ import annotations

from typing import Optional

from contracts.clip import ClipDefinition
from contracts.compositor import CompositeStream
from contracts.face import FaceDetectionResult
from contracts.ingestion import IngestionResult
from contracts.strategies import SportsFramePlan

from .sports_utils import process_sports

_SPORT = "football"
_DEFAULT_LAYOUT = "sports_action_crop"


def process_sports_football(
    clip: ClipDefinition,
    face_result: FaceDetectionResult,
    ingestion_result: IngestionResult,
    config: dict,
    plan: Optional[SportsFramePlan] = None,
) -> CompositeStream:
    """Compose a football clip into a silent 9:16 vertical composite.

    Delegates all crop logic to sports_utils.process_sports(). Football-specific
    logic (e.g. wide-field bias, goal celebration framing) can be added here
    before the delegate call without affecting tennis or future sports.

    Args:
        clip:             Clip definition with scene references and timing.
        face_result:      Face detection output for the full video.
        ingestion_result: Source video metadata (path, resolution, fps).
        config:           Full pipeline configuration dict (already overlaid
                          with sports_* and sports_football_* sections).
        plan:             Pre-computed SportsFramePlan from the orchestrator.
                          None for letterbox/center_crop layouts (no strategy needed).
                          For sports_action_crop this is required; sports_utils
                          falls back to center_crop with a warning if absent.

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
