"""Pipeline orchestrator skeleton for Shorts Factory.

Defines the 16-stage pipeline sequence and orchestration interface.
Full implementation deferred to later phases.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 16 stages in strict sequential order — never reorder, skip, or parallelize
PIPELINE_STAGES: tuple[str, ...] = (
    "ingestion",
    "scene_splitter",
    "transcription",
    "face_detection",
    "scoring",
    "clip_builder",
    "hook_generator",
    "tts",
    "subtitle",
    "compositor",
    "renderer",
    "thumbnail",
    "metadata",
    "storage",
    "scheduler",
    "publisher",
)

# Stages 0-5 run once per video, 6-13 per clip, 14-15 per batch
VIDEO_LEVEL_STAGES: tuple[str, ...] = PIPELINE_STAGES[:6]
CLIP_LEVEL_STAGES: tuple[str, ...] = PIPELINE_STAGES[6:14]
BATCH_LEVEL_STAGES: tuple[str, ...] = PIPELINE_STAGES[14:]

# Valid pipeline run states
PIPELINE_STATES: tuple[str, ...] = (
    "started",
    "analyzing",
    "building",
    "completed",
    "partial",
    "failed",
)

# Valid clip states
CLIP_STATES: tuple[str, ...] = (
    "generated",
    "queued",
    "scheduled",
    "published",
    "failed",
)


def get_stage_index(stage_name: str) -> int:
    """Get the zero-based index of a pipeline stage.

    Args:
        stage_name: Name of the stage.

    Returns:
        Zero-based index.

    Raises:
        ValueError: If stage_name is not a valid stage.
    """
    try:
        return PIPELINE_STAGES.index(stage_name)
    except ValueError:
        raise ValueError(
            f"Unknown pipeline stage: {stage_name!r}. "
            f"Valid stages: {', '.join(PIPELINE_STAGES)}"
        )


def get_resume_stage_index(last_completed_stage: str | None) -> int:
    """Get the index of the next stage to execute after resume.

    Args:
        last_completed_stage: Name of the last completed stage, or None for fresh run.

    Returns:
        Index of the next stage to execute.
    """
    if last_completed_stage is None:
        return 0
    return get_stage_index(last_completed_stage) + 1
