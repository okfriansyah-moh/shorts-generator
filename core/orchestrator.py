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

import hashlib
import json
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

# 19 stages in strict sequential order — never reorder, skip, or parallelize
PIPELINE_STAGES: tuple[str, ...] = (
    "ingestion",
    "scene_splitter",
    "transcription",
    "face_detection",
    "audio_analysis",
    "scene_activity",
    "image_quality",
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

# Stages 0-8 run once per video, 9-16 per clip, 17-18 per batch
VIDEO_LEVEL_STAGES: tuple[str, ...] = PIPELINE_STAGES[:9]
CLIP_LEVEL_STAGES: tuple[str, ...] = PIPELINE_STAGES[9:17]
BATCH_LEVEL_STAGES: tuple[str, ...] = PIPELINE_STAGES[17:]

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

STAGE_CACHE_VERSIONS: dict[str, str] = {
    "scene_splitter": "v1",
    "transcription": "v1",
    "face_detection": "v1",
    "audio_analysis": "v1",
    "scene_activity": "v1",
    "image_quality": "v1",
    "scoring": "v1",
}


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

    def _stage_cache_version(self, stage_name: str) -> str:
        return STAGE_CACHE_VERSIONS.get(stage_name, "v1")

    def _stage_config_hash(self, stage_name: str) -> str:
        relevant: dict[str, Any] = {}
        if stage_name == "scene_splitter":
            relevant = {"scene_splitter": self._config.get("scene_splitter", {})}
        elif stage_name == "transcription":
            relevant = {
                "transcription": self._config.get("transcription", {}),
                "gpu": self._config.get("gpu", {}),
                "pipeline_ffmpeg_timeout": self._config.get("pipeline", {}).get("ffmpeg_timeout"),
            }
        elif stage_name == "face_detection":
            relevant = {"face_detection": self._config.get("face_detection", {})}
        elif stage_name == "audio_analysis":
            relevant = {"pipeline_ffmpeg_timeout": self._config.get("pipeline", {}).get("ffmpeg_timeout")}
        elif stage_name in ("scene_activity", "image_quality", "scoring"):
            relevant = {"scoring": self._config.get("scoring", {})}
        payload = json.dumps(relevant, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _stage_state_matches(self, video_id: str, stage_name: str) -> bool:
        state = self._adapter.get_stage_state(video_id, stage_name)
        return bool(
            state
            and state.get("status") == "completed"
            and state.get("cache_version") == self._stage_cache_version(stage_name)
            and state.get("config_hash") == self._stage_config_hash(stage_name)
        )

    def _invalidate_stage_cache_from(self, video_id: str, stage_name: str) -> None:
        start_idx = get_stage_index(stage_name)
        stages = [
            s for s in PIPELINE_STAGES[start_idx:]
            if s in STAGE_CACHE_VERSIONS
        ]
        if stages:
            self._adapter.invalidate_stage_states(video_id, stages)

    def _mark_stage_started(
        self,
        video_id: str,
        stage_name: str,
        *,
        units_done: int = 0,
        units_total: int = 0,
        checkpoint_token: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._adapter.upsert_stage_state(
            video_id=video_id,
            stage_name=stage_name,
            status="running",
            cache_version=self._stage_cache_version(stage_name),
            config_hash=self._stage_config_hash(stage_name),
            units_done=units_done,
            units_total=units_total,
            checkpoint_token=checkpoint_token,
            payload_json=None if payload is None else json.dumps(payload, sort_keys=True),
        )

    def _mark_stage_completed(
        self,
        video_id: str,
        stage_name: str,
        *,
        units_done: int = 0,
        units_total: int = 0,
        checkpoint_token: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._adapter.upsert_stage_state(
            video_id=video_id,
            stage_name=stage_name,
            status="completed",
            cache_version=self._stage_cache_version(stage_name),
            config_hash=self._stage_config_hash(stage_name),
            units_done=units_done,
            units_total=units_total,
            checkpoint_token=checkpoint_token,
            payload_json=None if payload is None else json.dumps(payload, sort_keys=True),
        )

    def _hydrate_legacy_stage_states(
        self,
        video_id: str,
        scene_list: "SceneList | None" = None,
    ) -> None:
        """Backfill stage cache rows from legacy persisted artifacts."""
        states = self._adapter.list_stage_states(video_id)
        scene_rows = self._adapter.get_scene_rows(video_id)
        scene_count = len(scene_rows)
        if scene_list is None and scene_rows:
            from contracts.scene import SceneList

            scene_list = SceneList(
                video_id=video_id,
                scenes=tuple(self._adapter.get_scenes_for_video(video_id)),
                total_duration=round(sum(float(row["duration"]) for row in scene_rows), 6),
            )

        if scene_count and "scene_splitter" not in states:
            self._mark_stage_completed(video_id, "scene_splitter", units_done=scene_count, units_total=scene_count)
        if self._adapter.get_transcript(video_id) is not None and "transcription" not in states:
            transcript = self._adapter.get_transcript(video_id)
            total_segments = len(transcript.segments) if transcript is not None else 0
            self._mark_stage_completed(video_id, "transcription", units_done=total_segments, units_total=total_segments)
        cached_face = self._adapter.get_face_detection_result(video_id)
        if cached_face is not None and "face_detection" not in states and scene_count:
            self._mark_stage_completed(video_id, "face_detection", units_done=len(cached_face.scene_data), units_total=scene_count)
        if scene_count and "audio_analysis" not in states:
            raw_audio = self._adapter.get_scene_metric_map(video_id, "audio_rms_raw")
            if len([v for v in raw_audio.values() if v is not None]) == scene_count:
                self._mark_stage_completed(video_id, "audio_analysis", units_done=scene_count, units_total=scene_count)
        if scene_count and "scene_activity" not in states:
            raw_activity = self._adapter.get_scene_metric_map(video_id, "scene_activity_raw")
            if len([v for v in raw_activity.values() if v is not None]) == scene_count:
                self._mark_stage_completed(video_id, "scene_activity", units_done=scene_count, units_total=scene_count)
        if scene_count and "image_quality" not in states:
            raw_quality = self._adapter.get_scene_metric_map(video_id, "image_quality_raw")
            if len([v for v in raw_quality.values() if v is not None]) == scene_count:
                self._mark_stage_completed(video_id, "image_quality", units_done=scene_count, units_total=scene_count)
        if self._adapter.get_scored_scene_list(video_id) is not None and "scoring" not in states and scene_count:
            self._mark_stage_completed(video_id, "scoring", units_done=scene_count, units_total=scene_count)

    def _restore_cached_transcript(self, video_id: str) -> "Transcript | None":
        transcript = self._adapter.get_transcript(video_id)
        state = self._adapter.get_stage_state(video_id, "transcription")
        payload: dict[str, Any] = {}
        if state and state.get("payload_json"):
            try:
                payload = json.loads(state["payload_json"])
            except Exception:
                payload = {}
        if transcript is None:
            if state and state.get("status") == "completed":
                from contracts.transcript import Transcript

                return Transcript(
                    video_id=video_id,
                    segments=(),
                    total_words=0,
                    language=str(payload.get("language", self._config.get("transcription", {}).get("language", "en"))),
                )
            return None
        if payload.get("language") and transcript.language != payload["language"]:
            from contracts.transcript import Transcript
            return Transcript(
                video_id=transcript.video_id,
                segments=transcript.segments,
                total_words=transcript.total_words,
                language=str(payload["language"]),
            )
        return transcript

    def _restore_cached_face_result(self, video_id: str) -> "FaceDetectionResult | None":
        result = self._adapter.get_face_detection_result(video_id)
        if result is None:
            return None
        from contracts.face import FaceDetectionResult
        from modules.face_detection.detect import _compute_video_level_bbox, _vote_pip_region

        estimated = _vote_pip_region(result.scene_data)
        if estimated is None:
            estimated = _compute_video_level_bbox(result.scene_data)
        return FaceDetectionResult(
            video_id=result.video_id,
            scene_data=result.scene_data,
            average_visibility=result.average_visibility,
            faceless_scene_count=result.faceless_scene_count,
            estimated_pip_bbox=estimated,
        )

    def _restore_cached_audio(self, video_id: str) -> "AudioEnergyData | None":
        from contracts.audio import AudioEnergyData, SceneAudioEnergy

        rows = self._adapter.get_scene_rows(video_id)
        if not rows:
            return None
        energies = [
            row for row in rows
            if row.get("audio_rms_raw") is not None and row.get("audio_energy_score") is not None
        ]
        if len(energies) != len(rows):
            return None
        scene_energies = tuple(
            SceneAudioEnergy(
                scene_id=row["scene_id"],
                rms_energy=float(row["audio_rms_raw"]),
                normalized_energy=float(row["audio_energy_score"]),
            )
            for row in energies
        )
        rms_values = [energy.rms_energy for energy in scene_energies]
        return AudioEnergyData(
            video_id=video_id,
            scene_energies=scene_energies,
            video_min_rms=min(rms_values),
            video_max_rms=max(rms_values),
            video_mean_rms=sum(rms_values) / len(rms_values),
        )

    def _restore_cached_metric_scores(self, video_id: str, raw_column: str, normalized_column: str) -> dict[str, float] | None:
        rows = self._adapter.get_scene_rows(video_id)
        if not rows:
            return None
        if any(row.get(raw_column) is None or row.get(normalized_column) is None for row in rows):
            return None
        return {
            row["scene_id"]: float(row[normalized_column] or 0.0)
            for row in rows
        }

    def _build_transcript_text_by_scene(
        self,
        scene_list: "SceneList",
        transcript: "Transcript",
    ) -> dict[str, str]:
        text_by_scene: dict[str, str] = {}
        for scene in scene_list.scenes:
            parts: list[str] = []
            for segment in transcript.segments:
                if segment.end_time <= scene.start_time or segment.start_time >= scene.end_time:
                    continue
                segment_text = segment.text.strip()
                if segment_text:
                    parts.append(segment_text)
            text_by_scene[scene.scene_id] = " ".join(parts).strip()
        return text_by_scene

    def _normalise_scene_metric(
        self,
        video_id: str,
        raw_column: str,
        normalized_column: str,
    ) -> dict[str, float]:
        rows = self._adapter.get_scene_rows(video_id)
        raw_pairs = [
            (row["scene_id"], float(row[raw_column]))
            for row in rows
            if row.get(raw_column) is not None
        ]
        if not raw_pairs:
            return {}
        values = [value for _, value in raw_pairs]
        vmin = min(values)
        vmax = max(values)
        span = vmax - vmin
        normalized = {
            scene_id: ((value - vmin) / span if span > 0.0 else 0.0)
            for scene_id, value in raw_pairs
        }
        self._adapter.bulk_update_scene_metrics(
            [(scene_id, {normalized_column: score}) for scene_id, score in normalized.items()]
        )
        return normalized

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
        from modules.transcription.transcribe import transcribe_chunk

        video_id = ingestion_result.video_id
        if self._stage_state_matches(video_id, "transcription"):
            cached = self._restore_cached_transcript(video_id)
            if cached is not None:
                return cached

        self._invalidate_stage_cache_from(video_id, "transcription")
        chunk_duration = float(
            self._config.get("transcription", {}).get("chunk_duration_seconds", 300)
        )
        overlap = float(
            self._config.get("transcription", {}).get("chunk_overlap_seconds", 2)
        )
        detected_language = str(
            self._config.get("transcription", {}).get("language", "en")
        )
        total_duration = ingestion_result.duration_seconds
        total_chunks = max(1, int((total_duration + chunk_duration - 1e-9) // chunk_duration))
        cached_chunks = self._adapter.get_transcript_chunk_indexes(video_id)
        self._mark_stage_started(
            video_id,
            "transcription",
            units_done=len(cached_chunks),
            units_total=total_chunks,
        )

        for chunk_index in range(total_chunks):
            if chunk_index in cached_chunks:
                continue
            logical_start = chunk_index * chunk_duration
            logical_end = min(total_duration, logical_start + chunk_duration)
            extract_start = max(0.0, logical_start - overlap)
            extract_end = min(total_duration, logical_end + overlap)
            chunk = transcribe_chunk(
                ingestion_result,
                self._config,
                start_seconds=extract_start,
                duration_seconds=max(0.001, extract_end - extract_start),
                offset_ms=round(extract_start * 1000),
            )
            detected_language = chunk.language or detected_language
            interior_start_ms = round(logical_start * 1000)
            interior_end_ms = round(logical_end * 1000)
            filtered_segments = _filter_transcript_to_interior(
                chunk,
                interior_start_ms,
                interior_end_ms,
            )
            self._adapter.upsert_transcript_chunk(video_id, chunk_index, filtered_segments)
            cached_chunks.add(chunk_index)
            self._mark_stage_started(
                video_id,
                "transcription",
                units_done=len(cached_chunks),
                units_total=total_chunks,
                checkpoint_token=str(chunk_index),
                payload={"language": detected_language},
            )

        self._mark_stage_completed(
            video_id,
            "transcription",
            units_done=total_chunks,
            units_total=total_chunks,
            checkpoint_token=str(total_chunks - 1),
            payload={"language": detected_language},
        )
        transcript = self._restore_cached_transcript(video_id)
        if transcript is None:
            from contracts.transcript import Transcript

            transcript = Transcript(
                video_id=video_id,
                segments=(),
                total_words=0,
                language=detected_language,
            )
        return transcript

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
        from contracts.scene import SceneList
        from modules.face_detection.detect import detect_faces

        video_id = ingestion_result.video_id
        if self._stage_state_matches(video_id, "face_detection"):
            cached = self._restore_cached_face_result(video_id)
            if cached is not None and len(cached.scene_data) == len(scene_list.scenes):
                return cached

        self._invalidate_stage_cache_from(video_id, "face_detection")
        cached_scene_ids = self._adapter.get_cached_face_scene_ids(video_id)
        self._mark_stage_started(
            video_id,
            "face_detection",
            units_done=len(cached_scene_ids),
            units_total=len(scene_list.scenes),
        )
        for scene in scene_list.scenes:
            if scene.scene_id in cached_scene_ids:
                continue
            subset = SceneList(
                video_id=scene_list.video_id,
                scenes=(scene,),
                total_duration=scene.duration,
            )
            partial = detect_faces(ingestion_result, subset, self._config)
            self._adapter.upsert_face_scene(scene.scene_id, video_id, partial.scene_data[0])
            cached_scene_ids.add(scene.scene_id)
            self._mark_stage_started(
                video_id,
                "face_detection",
                units_done=len(cached_scene_ids),
                units_total=len(scene_list.scenes),
                checkpoint_token=scene.scene_id,
            )

        result = self._restore_cached_face_result(video_id)
        if result is None:
            raise RuntimeError(f"Failed to reconstruct face detection cache for {video_id}")
        self._mark_stage_completed(
            video_id,
            "face_detection",
            units_done=len(scene_list.scenes),
            units_total=len(scene_list.scenes),
        )
        return result

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
        from modules.audio_analysis.analyze import _extract_scene_rms

        video_id = ingestion_result.video_id
        cached = self._restore_cached_audio(video_id)
        if self._stage_state_matches(video_id, "audio_analysis") and cached is not None:
            return cached

        self._invalidate_stage_cache_from(video_id, "audio_analysis")
        missing_scene_ids = set(self._adapter.get_scene_ids_missing_metric(video_id, "audio_rms_raw"))
        done = len(scene_list.scenes) - len(missing_scene_ids)
        self._mark_stage_started(
            video_id,
            "audio_analysis",
            units_done=done,
            units_total=len(scene_list.scenes),
        )
        scene_by_id = {scene.scene_id: scene for scene in scene_list.scenes}
        for scene_id in [scene.scene_id for scene in scene_list.scenes if scene.scene_id in missing_scene_ids]:
            scene = scene_by_id[scene_id]
            rms = _extract_scene_rms(scene, ingestion_result.path, video_id, self._config)
            self._adapter.update_scene_metric(scene.scene_id, "audio_rms_raw", rms)
            done += 1
            self._mark_stage_started(
                video_id,
                "audio_analysis",
                units_done=done,
                units_total=len(scene_list.scenes),
                checkpoint_token=scene.scene_id,
            )
        self._normalise_scene_metric(video_id, "audio_rms_raw", "audio_energy_score")
        cached = self._restore_cached_audio(video_id)
        if cached is None:
            raise RuntimeError(f"Failed to reconstruct audio cache for {video_id}")
        self._mark_stage_completed(
            video_id,
            "audio_analysis",
            units_done=len(scene_list.scenes),
            units_total=len(scene_list.scenes),
        )
        return cached

    # ------------------------------------------------------------------
    # Stage: scene_activity
    # ------------------------------------------------------------------

    def run_scene_activity(
        self,
        ingestion_result: "IngestionResult",
        scene_list: "SceneList",
    ) -> dict[str, float]:
        """Execute the scene activity stage."""
        from contracts.scene import SceneList
        from modules.scoring.activity import compute_scene_activities

        video_id = ingestion_result.video_id
        cached_scores = self._restore_cached_metric_scores(
            video_id, "scene_activity_raw", "scene_activity_score"
        )
        if self._stage_state_matches(video_id, "scene_activity") and cached_scores is not None:
            return cached_scores

        self._invalidate_stage_cache_from(video_id, "scene_activity")
        missing = set(self._adapter.get_scene_ids_missing_metric(video_id, "scene_activity_raw"))
        done = len(scene_list.scenes) - len(missing)
        self._mark_stage_started(
            video_id,
            "scene_activity",
            units_done=done,
            units_total=len(scene_list.scenes),
        )
        for scene in scene_list.scenes:
            if scene.scene_id not in missing:
                continue
            subset = SceneList(video_id=video_id, scenes=(scene,), total_duration=scene.duration)
            raw_map = compute_scene_activities(subset, ingestion_result.path, self._config)
            self._adapter.update_scene_metric(scene.scene_id, "scene_activity_raw", raw_map.get(scene.scene_id, 0.0))
            done += 1
            self._mark_stage_started(
                video_id,
                "scene_activity",
                units_done=done,
                units_total=len(scene_list.scenes),
                checkpoint_token=scene.scene_id,
            )
        scores = self._normalise_scene_metric(video_id, "scene_activity_raw", "scene_activity_score")
        self._mark_stage_completed(
            video_id,
            "scene_activity",
            units_done=len(scene_list.scenes),
            units_total=len(scene_list.scenes),
        )
        return scores

    # ------------------------------------------------------------------
    # Stage: image_quality
    # ------------------------------------------------------------------

    def run_image_quality(
        self,
        ingestion_result: "IngestionResult",
        scene_list: "SceneList",
    ) -> dict[str, float]:
        """Execute the image quality stage."""
        from contracts.scene import SceneList
        from modules.scoring.quality import compute_scene_qualities

        video_id = ingestion_result.video_id
        cached_scores = self._restore_cached_metric_scores(
            video_id, "image_quality_raw", "image_quality_score"
        )
        if self._stage_state_matches(video_id, "image_quality") and cached_scores is not None:
            return cached_scores

        self._invalidate_stage_cache_from(video_id, "image_quality")
        missing = set(self._adapter.get_scene_ids_missing_metric(video_id, "image_quality_raw"))
        done = len(scene_list.scenes) - len(missing)
        self._mark_stage_started(
            video_id,
            "image_quality",
            units_done=done,
            units_total=len(scene_list.scenes),
        )
        for scene in scene_list.scenes:
            if scene.scene_id not in missing:
                continue
            subset = SceneList(video_id=video_id, scenes=(scene,), total_duration=scene.duration)
            raw_map = compute_scene_qualities(subset, ingestion_result.path, self._config)
            self._adapter.update_scene_metric(scene.scene_id, "image_quality_raw", raw_map.get(scene.scene_id, 0.0))
            done += 1
            self._mark_stage_started(
                video_id,
                "image_quality",
                units_done=done,
                units_total=len(scene_list.scenes),
                checkpoint_token=scene.scene_id,
            )
        scores = self._normalise_scene_metric(video_id, "image_quality_raw", "image_quality_score")
        self._mark_stage_completed(
            video_id,
            "image_quality",
            units_done=len(scene_list.scenes),
            units_total=len(scene_list.scenes),
        )
        return scores

    # ------------------------------------------------------------------
    # Stage: scoring
    # ------------------------------------------------------------------

    def run_scoring(
        self,
        scene_list: "SceneList",
        transcript: "Transcript",
        face_result: "FaceDetectionResult",
        audio_data: "AudioEnergyData | None",
        activity_scores: dict[str, float] | None = None,
        quality_scores: dict[str, float] | None = None,
    ) -> "ScoredSceneList":
        """Execute the scoring stage."""
        from modules.scoring.score import process as score_process

        video_id = scene_list.video_id
        if self._stage_state_matches(video_id, "scoring"):
            cached = self._adapter.get_scored_scene_list(video_id)
            if cached is not None:
                return cached

        self._invalidate_stage_cache_from(video_id, "scoring")
        scored = score_process(
            scene_list,
            transcript,
            face_result,
            audio_data,
            self._config,
            file_path=None,
            activity_scores=activity_scores,
            quality_scores=quality_scores,
        )
        self._adapter.persist_scored_scenes(
            scored,
            transcript_text_by_scene=self._build_transcript_text_by_scene(scene_list, transcript),
        )
        self._mark_stage_completed(
            video_id,
            "scoring",
            units_done=len(scored.scenes),
            units_total=len(scene_list.scenes),
        )
        return scored

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
        transcript: "Transcript | None" = None,
    ) -> "CompositeStream":
        """Execute the compositor stage for a single clip.

        For podcast videos, the strategy module is called here to generate a
        PodcastFramePlan DTO before invoking the compositor. This satisfies the
        Architecture Invariant: only the orchestrator calls modules; the
        compositor receives only frozen DTO contracts.
        """
        from modules.compositor.compose import process as comp_process

        plan = None
        video_type = self._config.get("video_type", "gameplay")

        if video_type == "podcast":
            from modules.strategies.podcast_strategy import generate_plan
            plan = generate_plan(clip, transcript, face_result, ingestion_result, self._config)

        elif video_type.startswith("sports_"):
            compositor_config = self._config.get("compositor", {})
            layout = (
                compositor_config.get("override_layout")
                or compositor_config.get("default_layout", "sports_center_crop")
            )
            if layout == "sports_action_crop":
                from modules.strategies.sports_strategy import generate_plan as generate_sports_plan
                plan = generate_sports_plan(clip, face_result, ingestion_result, self._config)

        return comp_process(clip, face_result, ingestion_result, self._config, plan)

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
            clip, face_result, ingestion_result, transcript,
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
        config_snapshot = json.dumps(
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

            # Compute a human-friendly video directory name:
            # {video_id}_{sanitized_filename_stem}
            input_stem = os.path.splitext(os.path.basename(ingestion_result.path))[0]
            # Keep only alphanumeric, hyphens, underscores; replace others
            safe_stem = "".join(
                c if (c.isalnum() or c in "-_") else "_"
                for c in input_stem
            ).strip("_")[:80]
            video_dir_name = f"{video_id}_{safe_stem}" if safe_stem else video_id
            self._config.setdefault("_runtime", {})["video_dir_name"] = video_dir_name

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

            # Only set "analyzing" for fresh runs or runs still in early stages.
            # A resumed run past clip_builder is already "building" — don't regress.
            if last_completed is None:
                self._adapter.update_pipeline_status(self._run_id, "analyzing")
                self._adapter.update_checkpoint(self._run_id, "ingestion")
            elif get_stage_index(last_completed) < get_stage_index("clip_builder"):
                self._adapter.update_pipeline_status(self._run_id, "analyzing")

            _reconfigure_logging_for_run(self._config, video_dir_name)

            resume_idx = get_resume_stage_index(last_completed)

            # ── Stage 1: scene_splitter ─────────────────────────────────
            if resume_idx <= get_stage_index("scene_splitter"):
                scene_list = self._run_stage_with_retry(
                    "scene_splitter", self.run_scene_splitter, ingestion_result,
                )
                self._adapter.update_checkpoint(self._run_id, "scene_splitter")
            else:
                scene_list = self.run_scene_splitter(ingestion_result)
            self._mark_stage_completed(
                video_id,
                "scene_splitter",
                units_done=len(scene_list.scenes),
                units_total=len(scene_list.scenes),
            )
            self._hydrate_legacy_stage_states(video_id, scene_list)

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

            # ── Stage 4: audio_analysis ────────────────────────────────
            if resume_idx <= get_stage_index("audio_analysis"):
                audio_data = self._run_stage_with_retry(
                    "audio_analysis", self.run_audio_analysis,
                    ingestion_result, scene_list,
                )
                self._adapter.update_checkpoint(self._run_id, "audio_analysis")
            else:
                audio_data = self.run_audio_analysis(ingestion_result, scene_list)

            # ── Stage 5: scene_activity ────────────────────────────────
            if resume_idx <= get_stage_index("scene_activity"):
                activity_scores = self._run_stage_with_retry(
                    "scene_activity", self.run_scene_activity,
                    ingestion_result, scene_list,
                )
                self._adapter.update_checkpoint(self._run_id, "scene_activity")
            else:
                activity_scores = self.run_scene_activity(ingestion_result, scene_list)

            # ── Stage 6: image_quality ─────────────────────────────────
            if resume_idx <= get_stage_index("image_quality"):
                quality_scores = self._run_stage_with_retry(
                    "image_quality", self.run_image_quality,
                    ingestion_result, scene_list,
                )
                self._adapter.update_checkpoint(self._run_id, "image_quality")
            else:
                quality_scores = self.run_image_quality(ingestion_result, scene_list)

            # ── Stage 7: scoring ───────────────────────────────────────
            if resume_idx <= get_stage_index("scoring"):
                scored_scenes = self._run_stage_with_retry(
                    "scoring", self.run_scoring,
                    scene_list, transcript, face_result, audio_data, activity_scores, quality_scores,
                )
                self._adapter.update_checkpoint(self._run_id, "scoring")
            else:
                scored_scenes = self.run_scoring(
                    scene_list, transcript, face_result, audio_data, activity_scores, quality_scores,
                )

            # ── Stage 8: clip_builder ──────────────────────────────────
            if resume_idx <= get_stage_index("clip_builder"):
                clip_list = self._run_stage_with_retry(
                    "clip_builder", self.run_clip_builder, scored_scenes,
                )
                self._adapter.update_checkpoint(self._run_id, "clip_builder")
            else:
                clip_list = self.run_clip_builder(scored_scenes)

            # Insert clip records into the database
            account_name = self._config.get("_account_name", "")
            for clip in clip_list.clips:
                self._adapter.insert_clip(
                    clip_id=clip.clip_id,
                    video_id=video_id,
                    start_time=clip.start_time,
                    end_time=clip.end_time,
                    duration=clip.duration,
                    composite_score=clip.average_score,
                    account_name=account_name,
                )

            # Transition to building phase
            self._adapter.update_pipeline_status(self._run_id, "building")

            # ── Stages 6-13: per-clip processing ────────────────────────
            video_output_dir = os.path.join(output_dir, video_dir_name)
            os.makedirs(video_output_dir, exist_ok=True)

            storage_records: list[StorageRecord] = []
            used_template_ids: frozenset[int] = frozenset()
            clips_failed = 0

            for clip_idx, clip in enumerate(clip_list.clips, 1):
                logger.info(
                    f"Processing clip {clip_idx}/{len(clip_list.clips)}",
                    extra={
                        "clip_id": clip.clip_id,
                        "video_id": video_id,
                        "clip_index": clip_idx,
                        "total_clips": len(clip_list.clips),
                    },
                )
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
                            video_path=record.file_paths.get("video"),
                            thumbnail_path=record.file_paths.get("thumbnail"),
                            account_name=account_name,
                        )
                        self._adapter.update_clip_status(
                            clip_id=record.clip_id,
                            new_status="queued",
                            valid_from=("generated",),
                        )
                        logger.info(
                            f"Clip {clip_idx}/{len(clip_list.clips)} completed",
                            extra={
                                "clip_id": clip.clip_id,
                                "video_id": video_id,
                                "status": "queued",
                            },
                        )
                    else:
                        clips_failed += 1
                        self._adapter.update_clip_status(
                            clip_id=clip.clip_id,
                            new_status="failed",
                            valid_from=("generated",),
                            error_message="Clip processing returned None",
                        )
                except Exception as exc:
                    clips_failed += 1
                    self._adapter.update_clip_status(
                        clip_id=clip.clip_id,
                        new_status="failed",
                        valid_from=("generated",),
                        error_message=str(exc)[:500],
                    )
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
            local_only = self._config.get("pipeline", {}).get("local_only", False)
            if local_only:
                logger.info(
                    "Local-only mode: skipping scheduler and publisher stages",
                    extra={"video_id": video_id, "run_id": self._run_id},
                )
                scheduled_records = storage_records
            else:
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
        sample_rate=44100,
        word_timings=(),
        engine_used="none",
    )


def _filter_transcript_to_interior(
    transcript: "Transcript",
    start_ms: int,
    end_ms: int,
) -> tuple["TranscriptSegment", ...]:
    """Keep only transcript content whose midpoint lies in the target window."""
    from contracts.transcript import TranscriptSegment, Word

    kept_segments: list[TranscriptSegment] = []
    for segment in transcript.segments:
        midpoint = (segment.start_time + segment.end_time) // 2
        if midpoint < start_ms or midpoint >= end_ms:
            continue
        words = tuple(
            Word(
                text=word.text,
                start_time=word.start_time,
                end_time=word.end_time,
                confidence=word.confidence,
            )
            for word in segment.words
            if start_ms <= ((word.start_time + word.end_time) // 2) < end_ms
        )
        if not words and not segment.text.strip():
            continue
        kept_segments.append(
            TranscriptSegment(
                text=" ".join(word.text for word in words).strip() or segment.text.strip(),
                start_time=max(segment.start_time, start_ms),
                end_time=min(segment.end_time, end_ms),
                words=words,
                confidence=segment.confidence,
            )
        )
    return tuple(kept_segments)


def _reconfigure_logging_for_run(config: dict[str, Any], video_dir_name: str) -> None:
    """Add a per-run file handler after video_id is known."""
    import os

    from core.logging import JSONFormatter

    output_dir = config.get("paths", {}).get("output_dir", "output")
    log_dir = os.path.join(output_dir, video_dir_name)
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
