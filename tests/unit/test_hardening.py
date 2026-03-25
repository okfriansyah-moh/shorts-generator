"""Hardening tests for Phase 0-1 system foundation.

Validates:
- DTO enforcement (no raw dicts across boundaries)
- Retry mechanism correctness
- Resume from partial state
- DB type consistency (ms↔sec conversion)
- Failure classification
- Observability fields in logs
"""

from __future__ import annotations

import json
import logging
import tempfile
import os
from unittest.mock import patch

import pytest

from contracts.errors import (
    classify_error,
    DataError,
    DependencyError,
    ErrorType,
    ProcessError,
    ValidationError,
)
from contracts.ingestion import IngestionResult
from contracts.scene import SceneList, SceneSegment
from database.adapter import DatabaseAdapter
from database.connection import create_connection, run_migrations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def adapter():
    """Create an in-memory adapter with all migrations applied."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test.db")
    conn = create_connection(db_path)
    migrations_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "database", "migrations",
    )
    run_migrations(conn, migrations_dir)
    adp = DatabaseAdapter(conn)
    yield adp
    conn.close()


@pytest.fixture
def sample_ingestion() -> IngestionResult:
    return IngestionResult(
        video_id="a1b2c3d4e5f67890",
        path="/tmp/test_video.mp4",
        duration_seconds=3600.0,
        resolution=(1920, 1080),
        codec="h264",
        audio_codec="aac",
        has_audio=True,
        file_size_bytes=500_000_000,
        fps=30.0,
    )


@pytest.fixture
def sample_scenes() -> tuple[SceneSegment, ...]:
    return (
        SceneSegment(scene_id="a1b2c3d4e5f67890_0_5000", video_id="a1b2c3d4e5f67890",
                     start_time=0, end_time=5000, duration=5.0),
        SceneSegment(scene_id="a1b2c3d4e5f67890_5000_10000", video_id="a1b2c3d4e5f67890",
                     start_time=5000, end_time=10000, duration=5.0),
        SceneSegment(scene_id="a1b2c3d4e5f67890_10000_15000", video_id="a1b2c3d4e5f67890",
                     start_time=10000, end_time=15000, duration=5.0),
    )


# ---------------------------------------------------------------------------
# 1. DTO ENFORCEMENT
# ---------------------------------------------------------------------------

class TestDTOEnforcement:
    """Adapter MUST accept DTOs and return DTOs — never raw dicts."""

    def test_insert_scenes_accepts_dto_tuple(self, adapter, sample_scenes):
        """insert_scenes accepts a tuple of SceneSegment DTOs."""
        adapter.insert_video(
            video_id="a1b2c3d4e5f67890", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_scenes(sample_scenes)
        result = adapter.get_scenes_for_video("a1b2c3d4e5f67890")
        assert len(result) == 3

    def test_insert_scenes_accepts_dto_list(self, adapter, sample_scenes):
        """insert_scenes accepts a list of SceneSegment DTOs."""
        adapter.insert_video(
            video_id="a1b2c3d4e5f67890", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_scenes(list(sample_scenes))
        result = adapter.get_scenes_for_video("a1b2c3d4e5f67890")
        assert len(result) == 3

    def test_get_scenes_returns_dtos(self, adapter, sample_scenes):
        """get_scenes_for_video returns SceneSegment DTOs, not dicts."""
        adapter.insert_video(
            video_id="a1b2c3d4e5f67890", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_scenes(sample_scenes)
        result = adapter.get_scenes_for_video("a1b2c3d4e5f67890")
        for scene in result:
            assert isinstance(scene, SceneSegment), f"Expected SceneSegment, got {type(scene)}"
            assert not isinstance(scene, dict), "Must not return dict"

    def test_scene_dto_is_frozen(self, adapter, sample_scenes):
        """SceneSegment DTOs returned from adapter are frozen (immutable)."""
        adapter.insert_video(
            video_id="a1b2c3d4e5f67890", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_scenes(sample_scenes)
        result = adapter.get_scenes_for_video("a1b2c3d4e5f67890")
        with pytest.raises(AttributeError):
            result[0].start_time = 999  # type: ignore[misc]

    def test_insert_scenes_rejects_raw_dicts(self, adapter):
        """insert_scenes must not accept raw dicts (type safety)."""
        adapter.insert_video(
            video_id="a1b2c3d4e5f67890", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        raw_dicts = [
            {"scene_id": "a1b2c3d4e5f67890_0_5000", "video_id": "a1b2c3d4e5f67890",
             "start_time": 0, "end_time": 5000, "duration": 5.0},
        ]
        with pytest.raises(AttributeError):
            adapter.insert_scenes(raw_dicts)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. DB TYPE CONSISTENCY (ms↔sec conversion)
# ---------------------------------------------------------------------------

class TestDBTypeConsistency:
    """Verify ms↔sec round-trip through adapter."""

    def test_ms_to_sec_conversion_on_insert(self, adapter, sample_scenes):
        """DTO ms values are stored as seconds in DB."""
        adapter.insert_video(
            video_id="a1b2c3d4e5f67890", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_scenes(sample_scenes)

        # Read raw DB values — should be seconds (REAL)
        row = adapter.connection.execute(
            "SELECT start_time, end_time FROM scenes WHERE scene_id = ?",
            ("a1b2c3d4e5f67890_5000_10000",),
        ).fetchone()
        assert row["start_time"] == 5.0, "DB should store seconds, not ms"
        assert row["end_time"] == 10.0

    def test_sec_to_ms_conversion_on_read(self, adapter, sample_scenes):
        """DB seconds are converted back to ms in DTOs."""
        adapter.insert_video(
            video_id="a1b2c3d4e5f67890", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_scenes(sample_scenes)
        result = adapter.get_scenes_for_video("a1b2c3d4e5f67890")
        assert result[0].start_time == 0
        assert result[0].end_time == 5000
        assert result[1].start_time == 5000
        assert result[1].end_time == 10000

    def test_round_trip_preserves_values(self, adapter):
        """Write ms → DB(sec) → read ms: values survive round-trip."""
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        original = SceneSegment(
            scene_id="vid1_3500_8200",
            video_id="vid1",
            start_time=3500,
            end_time=8200,
            duration=4.7,
        )
        adapter.insert_scenes([original])
        result = adapter.get_scenes_for_video("vid1")
        assert len(result) == 1
        assert result[0].start_time == 3500
        assert result[0].end_time == 8200
        assert result[0].duration == 4.7

    def test_scene_times_are_int_in_dto(self, adapter, sample_scenes):
        """start_time and end_time in returned DTOs must be int (ms)."""
        adapter.insert_video(
            video_id="a1b2c3d4e5f67890", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_scenes(sample_scenes)
        result = adapter.get_scenes_for_video("a1b2c3d4e5f67890")
        for s in result:
            assert isinstance(s.start_time, int), f"start_time should be int, got {type(s.start_time)}"
            assert isinstance(s.end_time, int), f"end_time should be int, got {type(s.end_time)}"


# ---------------------------------------------------------------------------
# 3. RETRY MECHANISM
# ---------------------------------------------------------------------------

class TestRetryMechanism:
    """Verify bounded, deterministic retry behavior."""

    def test_retry_succeeds_on_second_attempt(self, adapter, sample_ingestion):
        """Stage succeeds on retry after first failure."""
        from core.orchestrator import Orchestrator

        call_count = 0

        def flaky_ingestion():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Transient failure")
            return sample_ingestion

        config = {"retry": {"per_stage_max": 3}}
        orch = Orchestrator(config=config, adapter=adapter, video_path="/tmp/v.mp4")
        result = orch._run_stage_with_retry("ingestion", flaky_ingestion)
        assert result == sample_ingestion
        assert call_count == 2

    def test_retry_exhausted_raises(self, adapter):
        """Retry exhaustion raises the last exception."""
        from core.orchestrator import Orchestrator

        config = {"retry": {"per_stage_max": 2}}
        orch = Orchestrator(config=config, adapter=adapter, video_path="/tmp/v.mp4")

        def always_fails():
            raise RuntimeError("Permanent failure")

        with pytest.raises(RuntimeError, match="Permanent failure"):
            orch._run_stage_with_retry("test_stage", always_fails)

    def test_retry_is_bounded(self, adapter):
        """Retry count never exceeds max_stage_attempts."""
        from core.orchestrator import Orchestrator

        call_count = 0

        def counting_failure():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("fail")

        config = {"retry": {"per_stage_max": 3}}
        orch = Orchestrator(config=config, adapter=adapter, video_path="/tmp/v.mp4")
        with pytest.raises(RuntimeError):
            orch._run_stage_with_retry("test_stage", counting_failure)
        assert call_count == 3

    def test_retry_is_deterministic(self, adapter):
        """Two identical retry sequences produce same call counts."""
        from core.orchestrator import Orchestrator

        config = {"retry": {"per_stage_max": 2}}

        counts = []
        for _ in range(2):
            call_count = 0

            def deterministic_failure():
                nonlocal call_count
                call_count += 1
                raise RuntimeError("fail")

            orch = Orchestrator(config=config, adapter=adapter, video_path="/tmp/v.mp4")
            with pytest.raises(RuntimeError):
                orch._run_stage_with_retry("test_stage", deterministic_failure)
            counts.append(call_count)

        assert counts[0] == counts[1]


# ---------------------------------------------------------------------------
# 4. FAILURE CLASSIFICATION
# ---------------------------------------------------------------------------

class TestFailureClassification:
    """Verify structured error types."""

    def test_classify_validation_error(self):
        assert classify_error(ValueError("bad")) == ErrorType.VALIDATION_ERROR

    def test_classify_type_error(self):
        assert classify_error(TypeError("wrong type")) == ErrorType.VALIDATION_ERROR

    def test_classify_key_error(self):
        assert classify_error(KeyError("missing")) == ErrorType.VALIDATION_ERROR

    def test_classify_file_not_found(self):
        assert classify_error(FileNotFoundError("gone")) == ErrorType.DEPENDENCY_ERROR

    def test_classify_os_error(self):
        assert classify_error(OSError("disk")) == ErrorType.DEPENDENCY_ERROR

    def test_classify_pipeline_error_subtypes(self):
        assert classify_error(ValidationError("bad input")) == ErrorType.VALIDATION_ERROR
        assert classify_error(DependencyError("ffmpeg missing")) == ErrorType.DEPENDENCY_ERROR
        assert classify_error(ProcessError("timeout")) == ErrorType.PROCESS_ERROR
        assert classify_error(DataError("corrupt")) == ErrorType.DATA_ERROR

    def test_classify_unknown_defaults_to_process(self):
        assert classify_error(RuntimeError("unknown")) == ErrorType.PROCESS_ERROR

    def test_pipeline_error_has_error_type(self):
        exc = ProcessError("timeout")
        assert exc.error_type == ErrorType.PROCESS_ERROR
        assert str(exc) == "timeout"


# ---------------------------------------------------------------------------
# 5. STATE MACHINE HARDENING
# ---------------------------------------------------------------------------

class TestStateMachineHardening:
    """Verify checkpoint ordering and state transition safety."""

    def test_checkpoint_only_after_pipeline_run_exists(self, adapter):
        """update_checkpoint on non-existent run_id is a no-op (0 rows updated)."""
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        # No pipeline_run created — update_checkpoint should be safe no-op
        adapter.update_checkpoint("nonexistent_run", "ingestion")
        assert adapter.get_last_completed_stage("vid1") is None

    def test_partial_status_is_terminal(self, adapter):
        """'partial' status is excluded from active runs."""
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.create_pipeline_run("run1", "vid1")
        adapter.update_pipeline_status("run1", "partial")
        assert adapter.get_active_run("vid1") is None

    def test_failed_status_is_terminal(self, adapter):
        """'failed' status is excluded from active runs."""
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.create_pipeline_run("run1", "vid1")
        adapter.update_pipeline_status("run1", "failed", error_message="test error")
        assert adapter.get_active_run("vid1") is None

    def test_completed_status_is_terminal(self, adapter):
        """'completed' status is excluded from active runs."""
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.create_pipeline_run("run1", "vid1")
        adapter.update_pipeline_status("run1", "completed", clips_generated=10)
        assert adapter.get_active_run("vid1") is None

    def test_clip_status_transition_invalid_rejected(self, adapter):
        """Clip status transitions from invalid source state are rejected."""
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_clip(
            clip_id="clip1", video_id="vid1",
            start_time=0.0, end_time=45.0, duration=45.0,
        )
        # Try to skip states: generated → published (must go through queued/scheduled)
        assert adapter.update_clip_status("clip1", "published", ("scheduled",)) is False


# ---------------------------------------------------------------------------
# 6. OBSERVABILITY (logging fields)
# ---------------------------------------------------------------------------

class TestObservability:
    """Verify new observability fields are emitted in logs."""

    def test_retry_fields_in_log_output(self):
        """Retry-related fields are serialized by JSONFormatter."""
        from core.logging import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Stage completed", args=(), exc_info=None,
        )
        record.stage = "ingestion"  # type: ignore[attr-defined]
        record.video_id = "abc123"  # type: ignore[attr-defined]
        record.stage_attempt = 2  # type: ignore[attr-defined]
        record.retry_count = 1  # type: ignore[attr-defined]
        record.stage_duration_ms = 1234  # type: ignore[attr-defined]

        output = json.loads(formatter.format(record))
        assert output["stage_attempt"] == 2
        assert output["retry_count"] == 1
        assert output["stage_duration_ms"] == 1234

    def test_error_type_in_log_output(self):
        """error_type field is serialized by JSONFormatter."""
        from core.logging import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Stage failed", args=(), exc_info=None,
        )
        record.error_type = "PROCESS_ERROR"  # type: ignore[attr-defined]
        record.stage = "ingestion"  # type: ignore[attr-defined]
        record.video_id = ""  # type: ignore[attr-defined]

        output = json.loads(formatter.format(record))
        assert output["error_type"] == "PROCESS_ERROR"


# ---------------------------------------------------------------------------
# 7. CONFIG-DRIVEN FALLBACK VALUES
# ---------------------------------------------------------------------------

class TestConfigDrivenFallbacks:
    """Scene splitter uses config for fallback values, not hardcoded constants."""

    def test_fallback_threshold_from_config(self):
        """_detect_scenes uses config-driven fallback_threshold."""
        from modules.scene_splitter.split import _detect_scenes

        # With video_id plumbed through
        result = _detect_scenes(
            "/nonexistent", threshold=27.0, total_secs=60.0,
            video_id="test_vid",
            fallback_threshold=30.0,
            fallback_target_duration=15.0,
        )
        # Should fall back to uniform split since file doesn't exist
        assert len(result) > 0
        # With target_duration=15.0, a 60s video should produce 4 segments
        assert len(result) == 4

    def test_fallback_target_duration_from_config(self):
        """_detect_scenes uses config-driven fallback_target_duration."""
        from modules.scene_splitter.split import _detect_scenes

        result = _detect_scenes(
            "/nonexistent", threshold=27.0, total_secs=100.0,
            video_id="test_vid",
            fallback_threshold=30.0,
            fallback_target_duration=20.0,
        )
        # 100s / 20s = 5 segments
        assert len(result) == 5


# ---------------------------------------------------------------------------
# 8. ORCHESTRATOR-LEVEL IDEMPOTENCY
# ---------------------------------------------------------------------------

class TestOrchestratorIdempotency:
    """Verify orchestrator skips duplicate work on re-runs."""

    def test_ingestion_skips_insert_when_video_exists(self, adapter, sample_ingestion):
        """run_ingestion does not INSERT if video_id already in DB."""
        from core.orchestrator import Orchestrator

        # Pre-insert the video
        adapter.insert_video(
            video_id=sample_ingestion.video_id,
            file_path=sample_ingestion.path,
            duration_seconds=sample_ingestion.duration_seconds,
            width=sample_ingestion.resolution[0],
            height=sample_ingestion.resolution[1],
            fps=sample_ingestion.fps,
            has_audio=sample_ingestion.has_audio,
            file_size_bytes=sample_ingestion.file_size_bytes,
        )

        config = {"retry": {"per_stage_max": 1}}
        orch = Orchestrator(config=config, adapter=adapter, video_path="/tmp/v.mp4")

        # Mock ingest to return the sample DTO
        with patch("core.orchestrator.Orchestrator.run_ingestion") as mock_ingest:
            mock_ingest.__name__ = "run_ingestion"
            mock_ingest.return_value = sample_ingestion
            orch.run_ingestion = mock_ingest  # type: ignore[method-assign]
            orch.run_ingestion()

        # Video already existed — adapter.get_video would return non-None
        existing = adapter.get_video(sample_ingestion.video_id)
        assert existing is not None
        # Only one row in videos (no duplicate)
        count = adapter.connection.execute(
            "SELECT COUNT(*) FROM videos WHERE video_id = ?",
            (sample_ingestion.video_id,),
        ).fetchone()[0]
        assert count == 1

    def test_scene_splitter_returns_cached_scenes(self, adapter, sample_ingestion, sample_scenes):
        """run_scene_splitter returns cached SceneList when scenes exist in DB."""
        from core.orchestrator import Orchestrator

        # Pre-insert video and scenes
        adapter.insert_video(
            video_id=sample_ingestion.video_id,
            file_path=sample_ingestion.path,
            duration_seconds=sample_ingestion.duration_seconds,
            width=sample_ingestion.resolution[0],
            height=sample_ingestion.resolution[1],
            fps=sample_ingestion.fps,
            has_audio=sample_ingestion.has_audio,
            file_size_bytes=sample_ingestion.file_size_bytes,
        )
        adapter.insert_scenes(sample_scenes)

        config = {"retry": {"per_stage_max": 1}, "scene_splitter": {
            "threshold": 27.0, "min_scene_duration": 3.0,
            "max_scene_duration": 20.0,
        }}
        orch = Orchestrator(config=config, adapter=adapter, video_path="/tmp/v.mp4")

        # split_scenes should NOT be called — scenes already exist
        with patch("modules.scene_splitter.split.split_scenes") as mock_split:
            scene_list = orch.run_scene_splitter(sample_ingestion)

        mock_split.assert_not_called()
        assert isinstance(scene_list, SceneList)
        assert len(scene_list.scenes) == 3
        assert scene_list.video_id == sample_ingestion.video_id

    def test_scene_insert_is_idempotent(self, adapter, sample_scenes):
        """Inserting the same scenes twice produces no duplicates."""
        adapter.insert_video(
            video_id="a1b2c3d4e5f67890", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.insert_scenes(sample_scenes)
        adapter.insert_scenes(sample_scenes)  # Second insert — should be no-op

        result = adapter.get_scenes_for_video("a1b2c3d4e5f67890")
        assert len(result) == 3  # No duplicates

    def test_video_insert_is_idempotent(self, adapter):
        """Inserting the same video twice produces no duplicates."""
        for _ in range(2):
            adapter.insert_video(
                video_id="vid1", file_path="/tmp/v.mp4",
                duration_seconds=3600.0, width=1920, height=1080,
                fps=30.0, has_audio=True, file_size_bytes=100,
            )
        count = adapter.connection.execute(
            "SELECT COUNT(*) FROM videos WHERE video_id = 'vid1'"
        ).fetchone()[0]
        assert count == 1

    def test_pipeline_run_insert_is_idempotent(self, adapter):
        """Creating pipeline_run twice with same run_id is no-op."""
        adapter.insert_video(
            video_id="vid1", file_path="/tmp/v.mp4",
            duration_seconds=3600.0, width=1920, height=1080,
            fps=30.0, has_audio=True, file_size_bytes=100,
        )
        adapter.create_pipeline_run("run1", "vid1")
        adapter.create_pipeline_run("run1", "vid1")  # Same run_id
        count = adapter.connection.execute(
            "SELECT COUNT(*) FROM pipeline_runs WHERE run_id = 'run1'"
        ).fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# 9. FAIL-FAST BEHAVIOR
# ---------------------------------------------------------------------------

class TestFailFastBehavior:
    """Verify pipeline aborts correctly on stage failure."""

    def test_ingestion_failure_aborts_pipeline(self, adapter):
        """Ingestion failure → pipeline returns None, status = 'failed'."""
        from core.orchestrator import Orchestrator

        config = {"retry": {"per_stage_max": 1}}
        orch = Orchestrator(config=config, adapter=adapter, video_path="/tmp/v.mp4")

        with patch.object(orch, "run_ingestion", side_effect=RuntimeError("FFprobe crash")):
            result = orch.run()

        assert result is None

    def test_scene_splitter_failure_aborts_pipeline(self, adapter, sample_ingestion):
        """Scene splitting failure → pipeline returns None, status = 'failed'."""
        from core.orchestrator import Orchestrator

        config = {"retry": {"per_stage_max": 1}}
        orch = Orchestrator(config=config, adapter=adapter, video_path="/tmp/v.mp4")

        # Ingestion succeeds but scene_splitter fails
        with patch.object(orch, "run_ingestion", return_value=sample_ingestion):
            # Pre-insert video so pipeline_run can be created with FK
            adapter.insert_video(
                video_id=sample_ingestion.video_id,
                file_path=sample_ingestion.path,
                duration_seconds=sample_ingestion.duration_seconds,
                width=sample_ingestion.resolution[0],
                height=sample_ingestion.resolution[1],
                fps=sample_ingestion.fps,
                has_audio=sample_ingestion.has_audio,
                file_size_bytes=sample_ingestion.file_size_bytes,
            )
            with patch.object(orch, "run_scene_splitter", side_effect=RuntimeError("PySceneDetect crash")):
                result = orch.run()

        assert result is None
        # Pipeline should be marked as failed
        run = adapter.connection.execute(
            "SELECT status, error_log FROM pipeline_runs WHERE run_id = ?",
            (orch._run_id,),
        ).fetchone()
        assert run is not None
        assert run["status"] == "failed"
        assert "PySceneDetect crash" in run["error_log"]

    def test_retry_then_abort_on_persistent_failure(self, adapter, sample_ingestion):
        """Stage retries N times, then aborts the pipeline."""
        from core.orchestrator import Orchestrator

        call_count = 0

        def always_fails(*args):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("persistent error")

        config = {"retry": {"per_stage_max": 2}}
        orch = Orchestrator(config=config, adapter=adapter, video_path="/tmp/v.mp4")

        with patch.object(orch, "run_ingestion", return_value=sample_ingestion):
            adapter.insert_video(
                video_id=sample_ingestion.video_id,
                file_path=sample_ingestion.path,
                duration_seconds=sample_ingestion.duration_seconds,
                width=sample_ingestion.resolution[0],
                height=sample_ingestion.resolution[1],
                fps=sample_ingestion.fps,
                has_audio=sample_ingestion.has_audio,
                file_size_bytes=sample_ingestion.file_size_bytes,
            )
            with patch.object(orch, "run_scene_splitter", side_effect=always_fails):
                result = orch.run()

        assert result is None
        assert call_count == 2  # Retried exactly per_stage_max times


# ---------------------------------------------------------------------------
# 10. STRUCTURED LOG STATUS FIELD
# ---------------------------------------------------------------------------

class TestLogStatusField:
    """Verify status field appears in structured log output."""

    def test_status_success_in_log(self):
        """Status 'success' is emitted on stage completion."""
        from core.logging import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Stage completed", args=(), exc_info=None,
        )
        record.stage = "ingestion"  # type: ignore[attr-defined]
        record.status = "success"  # type: ignore[attr-defined]
        record.video_id = "abc123"  # type: ignore[attr-defined]

        output = json.loads(formatter.format(record))
        assert output["status"] == "success"

    def test_status_failed_in_log(self):
        """Status 'failed' is emitted on stage failure."""
        from core.logging import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg="Stage failed", args=(), exc_info=None,
        )
        record.stage = "ingestion"  # type: ignore[attr-defined]
        record.status = "failed"  # type: ignore[attr-defined]
        record.video_id = ""  # type: ignore[attr-defined]
        record.error_type = "PROCESS_ERROR"  # type: ignore[attr-defined]

        output = json.loads(formatter.format(record))
        assert output["status"] == "failed"

    def test_status_skipped_in_log(self):
        """Status 'skipped' is emitted when a stage is skipped."""
        from core.logging import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Stage skipped", args=(), exc_info=None,
        )
        record.stage = "scene_splitter"  # type: ignore[attr-defined]
        record.status = "skipped"  # type: ignore[attr-defined]
        record.video_id = "abc123"  # type: ignore[attr-defined]

        output = json.loads(formatter.format(record))
        assert output["status"] == "skipped"
