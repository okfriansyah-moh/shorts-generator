"""Pipeline orchestrator for Shorts Factory.

Defines the 16-stage pipeline sequence and wires the first two stages:
ingestion → scene_splitter.

The orchestrator is the ONLY component that calls modules and writes to
the database (via DatabaseAdapter). Modules never call each other and
never access the database directly.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from contracts.errors import classify_error

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
        retry_cfg = config.get("retry", {})
        self._max_stage_attempts: int = int(retry_cfg.get("per_stage_max", 2))

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------

    def _run_stage_with_retry(
        self,
        stage_name: str,
        stage_fn: Any,
        *args: Any,
    ) -> Any:
        """Execute a stage function with bounded retries.

        Deterministic: no randomness, no jitter, fixed attempt count.
        Logs each attempt with full observability fields.

        Returns:
            The stage function's return value on success.

        Raises:
            The last exception if all attempts fail.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self._max_stage_attempts + 1):
            start_time = time.monotonic()
            try:
                result = stage_fn(*args)
                elapsed_ms = round((time.monotonic() - start_time) * 1000)
                logger.info(
                    f"Stage {stage_name} completed",
                    extra={
                        "stage": stage_name,
                        "status": "success",
                        "video_id": getattr(self, "_current_video_id", ""),
                        "run_id": self._run_id,
                        "stage_attempt": attempt,
                        "stage_duration_ms": elapsed_ms,
                    },
                )
                return result
            except Exception as exc:
                elapsed_ms = round((time.monotonic() - start_time) * 1000)
                error_type = classify_error(exc)
                last_exc = exc
                logger.error(
                    f"Stage {stage_name} failed (attempt {attempt}/{self._max_stage_attempts})",
                    extra={
                        "stage": stage_name,
                        "status": "failed",
                        "video_id": getattr(self, "_current_video_id", ""),
                        "run_id": self._run_id,
                        "stage_attempt": attempt,
                        "retry_count": attempt - 1,
                        "stage_duration_ms": elapsed_ms,
                        "error": str(exc),
                        "error_type": error_type.value,
                    },
                )
                if attempt >= self._max_stage_attempts:
                    raise
        # Unreachable but makes type checker happy
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Stage: ingestion
    # ------------------------------------------------------------------

    def run_ingestion(self) -> "IngestionResult":
        """Execute the ingestion stage.

        Always runs ffprobe + hashing to compute video_id. If the video
        record already exists in the database, skips the INSERT. The
        checkpoint is written by run() after the pipeline_run record exists.

        Returns:
            IngestionResult DTO.
        """
        from modules.ingestion.ingest import ingest

        result = ingest(self._video_path, self._config)
        self._current_video_id = result.video_id

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
                codec_video=result.codec,
                codec_audio=result.audio_codec,
            )

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
        from contracts.scene import SceneList
        from modules.scene_splitter.split import split_scenes

        video_id = ingestion_result.video_id
        existing_scenes = self._adapter.get_scenes_for_video(video_id)

        if existing_scenes:
            logger.info(
                "Scene splitter stage skipped — scenes already in database",
                extra={
                    "stage": "scene_splitter",
                    "status": "skipped",
                    "video_id": video_id,
                    "scene_count": len(existing_scenes),
                },
            )
            segments = tuple(
                sorted(existing_scenes, key=lambda s: s.start_time)
            )
            total_duration = round(sum(s.duration for s in segments), 6)
            return SceneList(
                video_id=video_id,
                scenes=segments,
                total_duration=total_duration,
            )

        scene_list = split_scenes(ingestion_result, self._config)
        self._adapter.insert_scenes(scene_list.scenes)

        return scene_list

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> "SceneList | None":
        """Execute the pipeline up to the end of currently implemented stages.

        Creates the pipeline run record after ingestion (FK constraint requires
        video to exist), then checkpoints and proceeds with scene splitting.
        Supports resume from checkpoint. Uses bounded retries per stage.

        Returns:
            SceneList from the scene_splitter stage, or None on fatal error.
        """
        import json as _json

        config_snapshot = _json.dumps(
            {k: v for k, v in self._config.items() if k != "channel"},
            default=str,
        )
        self._current_video_id = ""

        try:
            # Stage 1: ingestion (with retry)
            ingestion_result = self._run_stage_with_retry(
                "ingestion", self.run_ingestion,
            )
            self._current_video_id = ingestion_result.video_id

            # Create pipeline_run (requires video to exist for FK)
            self._adapter.create_pipeline_run(
                run_id=self._run_id,
                video_id=ingestion_result.video_id,
                config_snapshot=config_snapshot,
            )
            self._adapter.update_pipeline_status(self._run_id, "analyzing")
            # Checkpoint AFTER pipeline_run record exists
            self._adapter.update_checkpoint(self._run_id, "ingestion")

            # Reconfigure logging with per-run log file
            _reconfigure_logging_for_run(
                self._config, ingestion_result.video_id,
            )

            # Stage 2: scene_splitter (with retry)
            scene_list = self._run_stage_with_retry(
                "scene_splitter", self.run_scene_splitter, ingestion_result,
            )
            self._adapter.update_checkpoint(self._run_id, "scene_splitter")

            # Mark partial — not all 16 stages implemented yet
            self._adapter.update_pipeline_status(
                self._run_id,
                "partial",
                clips_generated=0,
            )
            return scene_list
        except Exception as exc:
            error_message = str(exc)
            error_type = classify_error(exc)
            try:
                self._adapter.update_pipeline_status(
                    self._run_id,
                    "failed",
                    error_message=error_message,
                )
            except Exception:
                pass
            logger.critical(
                "Pipeline failed",
                extra={
                    "stage": "orchestrator",
                    "video_id": self._current_video_id,
                    "run_id": self._run_id,
                    "error": error_message,
                    "error_type": error_type.value,
                },
            )
            return None


def _reconfigure_logging_for_run(config: dict[str, Any], video_id: str) -> None:
    """Add a per-run file handler after video_id is known."""
    import os

    from core.logging import JSONFormatter

    output_dir = config.get("paths", {}).get("output_dir", "output")
    log_dir = os.path.join(output_dir, video_id)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "pipeline.log")

    root_logger = logging.getLogger()
    # Only add if not already present (idempotent on resume)
    for h in root_logger.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(log_path):
            return

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(file_handler)
