"""Pipeline orchestrator for Shorts Factory.

Defines the 16-stage pipeline sequence and wires all stages:
ingestion → scene_splitter → transcription → face_detection →
scoring → clip_builder → [per-clip: hook_generator → tts → subtitle →
compositor → renderer → thumbnail → metadata → storage] →
scheduler → publisher.

The orchestrator is the ONLY component that calls modules and writes to
the database (via DatabaseAdapter). Modules never call each other and
never access the database directly.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any

from dataclasses import dataclass, field

from contracts.errors import classify_error

if TYPE_CHECKING:
    from contracts.audio import AudioEnergyData
    from contracts.clip import ClipDefinition, ClipList
    from contracts.compositor import CompositeStream
    from contracts.face import FaceDetectionResult
    from contracts.hook import HookResult
    from contracts.ingestion import IngestionResult
    from contracts.metadata import MetadataResult
    from contracts.render import RenderedClip
    from contracts.scene import SceneList
    from contracts.scoring import ScoredSceneList
    from contracts.storage import StorageRecord
    from contracts.subtitle import SubtitleResult
    from contracts.thumbnail import ThumbnailResult
    from contracts.transcript import Transcript
    from contracts.tts import TTSResult
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


@dataclass(frozen=True)
class PipelineResult:
    """Aggregated result of a pipeline run.

    Bundles all stage outputs so the return type of ``Orchestrator.run()``
    remains stable. Fields for optional stages use ``None`` defaults.
    """

    video_id: str
    scene_list: "SceneList"
    transcript: "Transcript | None" = None
    face_detection: "FaceDetectionResult | None" = None
    audio_energy: "AudioEnergyData | None" = None
    scored_scenes: "ScoredSceneList | None" = None
    clip_list: "ClipList | None" = None
    storage_records: tuple["StorageRecord", ...] = field(default_factory=tuple)
    clips_generated: int = 0
    clips_failed: int = 0


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
    # Stage: transcription
    # ------------------------------------------------------------------

    def run_transcription(
        self,
        ingestion_result: "IngestionResult",
        scene_list: "SceneList",
    ) -> "Transcript":
        """Execute the transcription stage.

        Transcribes audio from the video using faster-whisper with word-level
        timestamps. Returns an empty Transcript if no speech is detected.

        Args:
            ingestion_result: Output of the ingestion stage.
            scene_list: Output of the scene_splitter stage.

        Returns:
            Transcript DTO.
        """
        from modules.transcription.transcribe import transcribe

        return transcribe(ingestion_result, self._config)

    # ------------------------------------------------------------------
    # Stage: face_detection
    # ------------------------------------------------------------------

    def run_face_detection(
        self,
        ingestion_result: "IngestionResult",
        scene_list: "SceneList",
    ) -> "FaceDetectionResult":
        """Execute the face detection stage.

        Samples frames at 2fps per scene using FFmpeg, detects faces via
        MediaPipe, and applies EMA smoothing to bounding boxes.
        Returns zero visibility ratios if no faces are detected (valid state).

        Args:
            ingestion_result: Output of the ingestion stage.
            scene_list: Output of the scene_splitter stage.

        Returns:
            FaceDetectionResult DTO.
        """
        from modules.face_detection.detect import detect_faces

        return detect_faces(ingestion_result, scene_list, self._config)

    # ------------------------------------------------------------------
    # Stage: audio_analysis
    # ------------------------------------------------------------------

    def run_audio_analysis(
        self,
        ingestion_result: "IngestionResult",
        scene_list: "SceneList",
    ) -> "AudioEnergyData":
        """Execute the audio analysis stage.

        Extracts per-scene RMS energy via FFmpeg astats filter and normalizes
        values across the video to [0, 1].

        Args:
            ingestion_result: Output of the ingestion stage.
            scene_list: Output of the scene_splitter stage.

        Returns:
            AudioEnergyData DTO.
        """
        from modules.audio_analysis.analyze import analyze_audio

        return analyze_audio(ingestion_result, scene_list, self._config)

    # ------------------------------------------------------------------
    # Stage: scoring
    # ------------------------------------------------------------------

    def run_scoring(
        self,
        scene_list: "SceneList",
        transcript: "Transcript",
        face_result: "FaceDetectionResult",
        audio_data: "AudioEnergyData | None",
    ) -> "ScoredSceneList":
        """Execute the scoring stage."""
        from modules.scoring.score import process as score_process

        return score_process(
            scene_list,
            transcript,
            face_result,
            audio_data,
            self._config,
            file_path=self._video_path,
        )

    # ------------------------------------------------------------------
    # Stage: clip_builder
    # ------------------------------------------------------------------

    def run_clip_builder(
        self,
        scored_scene_list: "ScoredSceneList",
    ) -> "ClipList":
        """Execute the clip builder stage."""
        from modules.clip_builder.build import process as cb_process

        return cb_process(scored_scene_list, self._config)

    # ------------------------------------------------------------------
    # Stage: hook_generator (per clip)
    # ------------------------------------------------------------------

    def run_hook_generator(
        self,
        clip: "ClipDefinition",
        transcript: "Transcript",
        used_template_ids: frozenset[int],
    ) -> tuple["HookResult", frozenset[int]]:
        """Execute the hook generator stage for a single clip."""
        from modules.hook_generator.generate import process as hook_process

        return hook_process(clip, transcript, self._config, used_template_ids)

    # ------------------------------------------------------------------
    # Stage: tts (per clip)
    # ------------------------------------------------------------------

    def run_tts(
        self,
        hook_result: "HookResult",
        output_dir: str,
    ) -> "TTSResult":
        """Execute the TTS stage for a single clip."""
        from modules.tts.synthesize import process as tts_process

        return tts_process(hook_result, self._config, output_dir)

    # ------------------------------------------------------------------
    # Stage: subtitle (per clip)
    # ------------------------------------------------------------------

    def run_subtitle(
        self,
        clip: "ClipDefinition",
        transcript: "Transcript",
        tts_result: "TTSResult",
        output_dir: str,
    ) -> "SubtitleResult":
        """Execute the subtitle stage for a single clip."""
        from modules.subtitle.generate import process as sub_process

        return sub_process(clip, transcript, tts_result, self._config, output_dir)

    # ------------------------------------------------------------------
    # Stage: compositor (per clip)
    # ------------------------------------------------------------------

    def run_compositor(
        self,
        clip: "ClipDefinition",
        face_result: "FaceDetectionResult",
        ingestion_result: "IngestionResult",
    ) -> "CompositeStream":
        """Execute the compositor stage for a single clip."""
        from modules.compositor.compose import process as comp_process

        return comp_process(clip, face_result, ingestion_result, self._config)

    # ------------------------------------------------------------------
    # Stage: renderer (per clip)
    # ------------------------------------------------------------------

    def run_renderer(
        self,
        composite: "CompositeStream",
        tts_result: "TTSResult | None",
        subtitle_result: "SubtitleResult | None",
        output_dir: str,
    ) -> "RenderedClip":
        """Execute the renderer stage for a single clip."""
        from modules.renderer.render import process as render_process

        return render_process(
            composite, tts_result, subtitle_result, self._config, output_dir,
        )

    # ------------------------------------------------------------------
    # Stage: thumbnail (per clip)
    # ------------------------------------------------------------------

    def run_thumbnail(
        self,
        clip: "ClipDefinition",
        face_result: "FaceDetectionResult | None",
        hook_result: "HookResult",
        ingestion_result: "IngestionResult",
        output_dir: str,
    ) -> "ThumbnailResult":
        """Execute the thumbnail stage for a single clip."""
        from modules.thumbnail.thumbnail import process as thumb_process

        return thumb_process(
            clip, face_result, hook_result, ingestion_result,
            self._config, output_dir,
        )

    # ------------------------------------------------------------------
    # Stage: metadata (per clip)
    # ------------------------------------------------------------------

    def run_metadata(
        self,
        hook_result: "HookResult",
        transcript: "Transcript",
        clip: "ClipDefinition",
    ) -> "MetadataResult":
        """Execute the metadata stage for a single clip."""
        from modules.metadata.metadata import process as meta_process

        return meta_process(hook_result, transcript, clip, self._config)

    # ------------------------------------------------------------------
    # Stage: storage (per clip)
    # ------------------------------------------------------------------

    def run_storage(
        self,
        rendered_clip: "RenderedClip",
        thumbnail_result: "ThumbnailResult",
        metadata_result: "MetadataResult",
        composite_score: float,
        subtitle_result: "SubtitleResult | None",
        tts_result: "TTSResult | None",
    ) -> "StorageRecord":
        """Execute the storage stage for a single clip."""
        from modules.storage.store import process as store_process

        return store_process(
            rendered_clip,
            thumbnail_result,
            metadata_result,
            self._config,
            composite_score=composite_score,
            subtitle_result=subtitle_result,
            tts_result=tts_result,
        )

    # ------------------------------------------------------------------
    # Stage: scheduler (batch)
    # ------------------------------------------------------------------

    def run_scheduler(
        self,
        records: list["StorageRecord"],
        existing_scheduled: list["StorageRecord"],
    ) -> list["StorageRecord"]:
        """Execute the scheduler stage for the batch."""
        from modules.scheduler.schedule import process as sched_process

        return sched_process(
            records, existing_scheduled, self._config,
        )

    # ------------------------------------------------------------------
    # Stage: analytics (batch)
    # ------------------------------------------------------------------

    def run_analytics(
        self,
        video_id: str,
        clip_list: "ClipList",
        scored_scenes: "ScoredSceneList",
        storage_records: tuple["StorageRecord", ...],
        output_dir: str,
    ) -> None:
        """Generate the pipeline analytics report (non-fatal)."""
        from modules.analytics.pipeline_report import process as analytics_process

        analytics_process(
            video_id,
            self._run_id,
            clip_list,
            scored_scenes,
            storage_records,
            output_dir,
            self._config,
        )

    # ------------------------------------------------------------------
    # Per-clip processing loop (stages 6-13)
    # ------------------------------------------------------------------

    def _process_single_clip(
        self,
        clip: "ClipDefinition",
        transcript: "Transcript",
        face_result: "FaceDetectionResult",
        ingestion_result: "IngestionResult",
        output_dir: str,
        used_template_ids: frozenset[int],
    ) -> tuple["StorageRecord | None", frozenset[int]]:
        """Run stages 6-13 for a single clip.

        Returns (StorageRecord, updated_used_template_ids) on success,
        or (None, updated_used_template_ids) if the clip fails.
        """
        clip_id = clip.clip_id

        # Stage 6: hook_generator
        hook_result, used_template_ids = self._run_stage_with_retry(
            "hook_generator",
            self.run_hook_generator,
            clip, transcript, used_template_ids,
        )

        # Stage 7: tts
        tts_result: TTSResult | None = None
        try:
            tts_result = self._run_stage_with_retry(
                "tts", self.run_tts, hook_result, output_dir,
            )
        except Exception as exc:
            logger.warning(
                f"TTS failed for clip {clip_id}, continuing without narration",
                extra={"clip_id": clip_id, "error": str(exc)[:200]},
            )

        # Stage 8: subtitle
        subtitle_result: SubtitleResult | None = None
        try:
            subtitle_result = self._run_stage_with_retry(
                "subtitle", self.run_subtitle,
                clip, transcript, tts_result if tts_result is not None else _empty_tts_result(clip_id),
                output_dir,
            )
        except Exception as exc:
            logger.warning(
                f"Subtitle failed for clip {clip_id}, continuing without subtitles",
                extra={"clip_id": clip_id, "error": str(exc)[:200]},
            )

        # Stage 9: compositor
        composite = self._run_stage_with_retry(
            "compositor", self.run_compositor,
            clip, face_result, ingestion_result,
        )

        # Stage 10: renderer
        rendered_clip = self._run_stage_with_retry(
            "renderer", self.run_renderer,
            composite, tts_result, subtitle_result, output_dir,
        )

        # Stage 11: thumbnail
        thumbnail_result = self._run_stage_with_retry(
            "thumbnail", self.run_thumbnail,
            clip, face_result, hook_result, ingestion_result, output_dir,
        )

        # Stage 12: metadata
        metadata_result = self._run_stage_with_retry(
            "metadata", self.run_metadata,
            hook_result, transcript, clip,
        )

        # Stage 13: storage
        storage_record = self._run_stage_with_retry(
            "storage", self.run_storage,
            rendered_clip, thumbnail_result, metadata_result,
            clip.average_score, subtitle_result, tts_result,
        )

        return storage_record, used_template_ids

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> PipelineResult | None:
        """Execute the full 16-stage pipeline.

        Creates the pipeline run record after ingestion (FK constraint requires
        video to exist), then proceeds through all stages with checkpointing.
        Supports resume from the last completed checkpoint.
        Uses bounded retries per stage.

        Returns:
            PipelineResult bundling all stage outputs, or None on fatal error.
        """
        import json as _json

        config_snapshot = _json.dumps(
            {k: v for k, v in self._config.items() if k != "channel"},
            default=str,
        )
        self._current_video_id = ""
        output_dir = self._config.get("paths", {}).get("output_dir", "output")

        try:
            # ── Stage 0: ingestion ──────────────────────────────────────
            # Ingestion runs unconditionally: we need video_id (derived from
            # file content hash) to look up any active pipeline run.  The
            # stage is idempotent — duplicate DB inserts are guarded by
            # ON CONFLICT DO NOTHING, and ffprobe + hashing is fast (<10s).
            ingestion_result = self._run_stage_with_retry(
                "ingestion", self.run_ingestion,
            )
            self._current_video_id = ingestion_result.video_id
            video_id = ingestion_result.video_id

            # Check for an active (non-terminal) pipeline run to resume
            active_run = self._adapter.get_active_run(video_id)
            if active_run is not None:
                # Resume existing run
                self._run_id = active_run["run_id"]
                last_completed = active_run.get("last_completed_stage")
                logger.info(
                    "Resuming pipeline run from checkpoint",
                    extra={
                        "video_id": video_id,
                        "run_id": self._run_id,
                        "checkpoint": last_completed,
                    },
                )
            else:
                # Create fresh pipeline run
                self._adapter.create_pipeline_run(
                    run_id=self._run_id,
                    video_id=video_id,
                    config_snapshot=config_snapshot,
                )
                last_completed = None

            self._adapter.update_pipeline_status(self._run_id, "analyzing")
            if last_completed is None:
                self._adapter.update_checkpoint(self._run_id, "ingestion")

            _reconfigure_logging_for_run(self._config, video_id)

            resume_idx = get_resume_stage_index(last_completed)

            # ── Stage 1: scene_splitter ─────────────────────────────────
            if resume_idx <= get_stage_index("scene_splitter"):
                scene_list = self._run_stage_with_retry(
                    "scene_splitter", self.run_scene_splitter, ingestion_result,
                )
                self._adapter.update_checkpoint(self._run_id, "scene_splitter")
            else:
                scene_list = self.run_scene_splitter(ingestion_result)

            # ── Stage 2: transcription ──────────────────────────────────
            if resume_idx <= get_stage_index("transcription"):
                transcript = self._run_stage_with_retry(
                    "transcription", self.run_transcription,
                    ingestion_result, scene_list,
                )
                self._adapter.update_checkpoint(self._run_id, "transcription")
            else:
                transcript = self.run_transcription(ingestion_result, scene_list)

            # ── Stage 3: face_detection ─────────────────────────────────
            if resume_idx <= get_stage_index("face_detection"):
                face_result = self._run_stage_with_retry(
                    "face_detection", self.run_face_detection,
                    ingestion_result, scene_list,
                )
                self._adapter.update_checkpoint(self._run_id, "face_detection")
            else:
                face_result = self.run_face_detection(ingestion_result, scene_list)

            # audio_analysis feeds scoring — runs within the face_detection
            # checkpoint window (not a formal pipeline stage in PIPELINE_STAGES)
            audio_data = self._run_stage_with_retry(
                "audio_analysis", self.run_audio_analysis,
                ingestion_result, scene_list,
            )

            # ── Stage 4: scoring ────────────────────────────────────────
            if resume_idx <= get_stage_index("scoring"):
                scored_scenes = self._run_stage_with_retry(
                    "scoring", self.run_scoring,
                    scene_list, transcript, face_result, audio_data,
                )
                self._adapter.update_checkpoint(self._run_id, "scoring")
            else:
                scored_scenes = self.run_scoring(
                    scene_list, transcript, face_result, audio_data,
                )

            # ── Stage 5: clip_builder ───────────────────────────────────
            if resume_idx <= get_stage_index("clip_builder"):
                clip_list = self._run_stage_with_retry(
                    "clip_builder", self.run_clip_builder, scored_scenes,
                )
                self._adapter.update_checkpoint(self._run_id, "clip_builder")
            else:
                clip_list = self.run_clip_builder(scored_scenes)

            # Insert clip records into the database
            for clip in clip_list.clips:
                self._adapter.insert_clip(
                    clip_id=clip.clip_id,
                    video_id=video_id,
                    start_time=clip.start_time,
                    end_time=clip.end_time,
                    duration=clip.duration,
                    composite_score=clip.average_score,
                )

            # Transition to building phase
            self._adapter.update_pipeline_status(self._run_id, "building")

            # ── Stages 6-13: per-clip processing ────────────────────────
            video_output_dir = os.path.join(output_dir, video_id)
            os.makedirs(video_output_dir, exist_ok=True)

            storage_records: list[StorageRecord] = []
            used_template_ids: frozenset[int] = frozenset()
            clips_failed = 0

            for clip in clip_list.clips:
                try:
                    record, used_template_ids = self._process_single_clip(
                        clip, transcript, face_result, ingestion_result,
                        video_output_dir, used_template_ids,
                    )
                    if record is not None:
                        storage_records.append(record)
                        # Insert record into DB as generated → queued
                        self._adapter.insert_clip(
                            clip_id=record.clip_id,
                            video_id=video_id,
                            start_time=clip.start_time,
                            end_time=clip.end_time,
                            duration=clip.duration,
                            composite_score=record.composite_score,
                        )
                    else:
                        clips_failed += 1
                except Exception as exc:
                    clips_failed += 1
                    logger.error(
                        f"Clip {clip.clip_id} failed entirely, skipping",
                        extra={
                            "clip_id": clip.clip_id,
                            "video_id": video_id,
                            "error": str(exc)[:200],
                        },
                    )

            # Checkpoint after all clips processed
            self._adapter.update_checkpoint(self._run_id, "storage")

            # ── Stage 14: scheduler ─────────────────────────────────────
            existing_clips = self._adapter.get_clips_for_video(video_id)
            existing_scheduled_records: list[StorageRecord] = []
            # Existing scheduled clips are only relevant for date-conflict avoidance;
            # we pass an empty list here since storage_records are all fresh "queued".

            scheduled_records = self._run_stage_with_retry(
                "scheduler", self.run_scheduler,
                storage_records, existing_scheduled_records,
            )
            self._adapter.update_checkpoint(self._run_id, "scheduler")

            # Update scheduled clips in DB
            for rec in scheduled_records:
                if rec.status == "scheduled" and rec.scheduled_at:
                    self._adapter.update_clip_status(
                        clip_id=rec.clip_id,
                        new_status="scheduled",
                        valid_from=("generated", "queued"),
                    )

            # ── Analytics (non-fatal) ───────────────────────────────────
            try:
                self.run_analytics(
                    video_id, clip_list, scored_scenes,
                    tuple(storage_records), output_dir,
                )
            except Exception as exc:
                logger.warning(
                    "Analytics report generation failed (non-fatal)",
                    extra={"video_id": video_id, "error": str(exc)[:200]},
                )

            # ── Stage 15: publisher is handled externally ───────────────
            # The publisher stage runs via scripts/publish_cron.py, not
            # as part of the main pipeline execution.

            # ── Finalize ────────────────────────────────────────────────
            clips_generated = len(storage_records)
            final_status = "completed" if clips_failed == 0 else "partial"

            self._adapter.update_pipeline_status(
                self._run_id,
                final_status,
                clips_generated=clips_generated,
            )

            return PipelineResult(
                video_id=video_id,
                scene_list=scene_list,
                transcript=transcript,
                face_detection=face_result,
                audio_energy=audio_data,
                scored_scenes=scored_scenes,
                clip_list=clip_list,
                storage_records=tuple(storage_records),
                clips_generated=clips_generated,
                clips_failed=clips_failed,
            )
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


def _empty_tts_result(clip_id: str) -> "TTSResult":
    """Return a no-op TTSResult for clips where TTS failed."""
    from contracts.tts import TTSResult

    return TTSResult(
        clip_id=clip_id,
        audio_path="",
        duration_seconds=0.0,
        engine="none",
        voice="",
        word_timings=(),
    )


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
