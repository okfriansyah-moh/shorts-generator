"""Pipeline orchestrator for Shorts Factory.

Defines the 16-stage pipeline sequence and wires the first two stages:
ingestion → scene_splitter.

The orchestrator is the ONLY component that calls modules and writes to
the database (via DatabaseAdapter). Modules never call each other and
never access the database directly.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contracts.ingestion import IngestionResult
    from contracts.scene import SceneList
    from database.adapter import DatabaseAdapter

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


class Orchestrator:
    """Pipeline orchestrator: calls modules, manages checkpoints and DB writes.

    The orchestrator is the single execution controller. It:
    - Calls modules (which are pure functions returning DTOs)
    - Writes results to the database via DatabaseAdapter
    - Checkpoints progress after each completed stage
    - Supports resume from the last completed stage

    Only the entry point (run_pipeline.py) should instantiate this class.
    """

    def __init__(
        self,
        config: dict[str, Any],
        adapter: "DatabaseAdapter",
        video_path: str,
    ) -> None:
        self._config = config
        self._adapter = adapter
        self._video_path = video_path
        self._run_id: str = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Stage: ingestion
    # ------------------------------------------------------------------

    def run_ingestion(self) -> "IngestionResult":
        """Execute the ingestion stage.

        Checks the database for a cached result. If a video with the same
        content fingerprint already exists, returns the cached IngestionResult
        without re-processing.

        Returns:
            IngestionResult DTO.
        """
        from modules.ingestion.ingest import ingest

        result = ingest(self._video_path, self._config)

        existing = self._adapter.get_video(result.video_id)
        if existing is None:
            self._adapter.insert_video(
                video_id=result.video_id,
                file_path=result.path,
                duration_seconds=result.duration_seconds,
                width=result.resolution[0],
                height=result.resolution[1],
                fps=result.fps,
                has_audio=result.has_audio,
                file_size_bytes=result.file_size_bytes,
            )
            logger.info(
                "Ingestion stage complete",
                extra={"stage": "ingestion", "video_id": result.video_id},
            )
        else:
            logger.info(
                "Ingestion stage skipped — video already processed",
                extra={"stage": "ingestion", "video_id": result.video_id},
            )

        self._adapter.update_checkpoint(self._run_id, "ingestion")
        return result

    # ------------------------------------------------------------------
    # Stage: scene_splitter
    # ------------------------------------------------------------------

    def run_scene_splitter(self, ingestion_result: "IngestionResult") -> "SceneList":
        """Execute the scene_splitter stage.

        Checks the database for cached scenes. If scenes already exist for
        this video_id, returns the cached SceneList without re-processing.

        Args:
            ingestion_result: Output of the ingestion stage.

        Returns:
            SceneList DTO.
        """
        from contracts.scene import SceneList, SceneSegment
        from modules.scene_splitter.split import split_scenes

        video_id = ingestion_result.video_id
        existing_scenes = self._adapter.get_scenes_for_video(video_id)

        if existing_scenes:
            logger.info(
                "Scene splitter stage skipped — scenes already in database",
                extra={
                    "stage": "scene_splitter",
                    "video_id": video_id,
                    "scene_count": len(existing_scenes),
                },
            )
            segments = tuple(
                SceneSegment(
                    scene_id=row["scene_id"],
                    video_id=row["video_id"],
                    start_time=int(row["start_time"]),
                    end_time=int(row["end_time"]),
                    duration=float(row["duration"]),
                )
                for row in sorted(existing_scenes, key=lambda r: r["start_time"])
            )
            total_duration = round(sum(s.duration for s in segments), 6)
            self._adapter.update_checkpoint(self._run_id, "scene_splitter")
            return SceneList(
                video_id=video_id,
                scenes=segments,
                total_duration=total_duration,
            )

        scene_list = split_scenes(ingestion_result, self._config)

        self._adapter.insert_scenes(
            [
                {
                    "scene_id": s.scene_id,
                    "video_id": s.video_id,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "duration": s.duration,
                }
                for s in scene_list.scenes
            ]
        )

        logger.info(
            "Scene splitter stage complete",
            extra={
                "stage": "scene_splitter",
                "video_id": video_id,
                "scene_count": len(scene_list.scenes),
            },
        )
        self._adapter.update_checkpoint(self._run_id, "scene_splitter")
        return scene_list

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> "SceneList | None":
        """Execute the pipeline up to the end of currently implemented stages.

        Runs ingestion first to obtain the video_id, then creates the pipeline
        run record (FK constraint requires video to exist), then proceeds with
        scene splitting. Supports resume from checkpoint.

        Returns:
            SceneList from the scene_splitter stage, or None on fatal error.
        """
        import json as _json

        config_snapshot = _json.dumps(
            {k: v for k, v in self._config.items() if k != "channel"},
            default=str,
        )

        try:
            ingestion_result = self.run_ingestion()

            self._adapter.create_pipeline_run(
                run_id=self._run_id,
                video_id=ingestion_result.video_id,
                config_snapshot=config_snapshot,
            )
            self._adapter.update_pipeline_status(self._run_id, "analyzing")

            scene_list = self.run_scene_splitter(ingestion_result)
            self._adapter.update_pipeline_status(
                self._run_id,
                "completed",
                clips_generated=0,
            )
            return scene_list
        except Exception as exc:
            logger.critical(
                "Pipeline failed",
                extra={
                    "stage": "orchestrator",
                    "video_id": "",
                    "error": str(exc),
                },
            )
            return None
