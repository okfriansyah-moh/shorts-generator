"""Unit tests for core/orchestrator.py — pipeline stage definitions."""

from __future__ import annotations

import pytest

from core.orchestrator import (
    PIPELINE_STAGES,
    VIDEO_LEVEL_STAGES,
    CLIP_LEVEL_STAGES,
    BATCH_LEVEL_STAGES,
    PIPELINE_STATES,
    CLIP_STATES,
    Orchestrator,
    get_stage_index,
    get_resume_stage_index,
)


class TestPipelineStages:
    """Tests for pipeline stage constants."""

    def test_exactly_16_stages(self):
        """Pipeline has exactly 16 stages."""
        assert len(PIPELINE_STAGES) == 16

    def test_stage_order(self):
        """Stages are in the canonical order."""
        assert PIPELINE_STAGES[0] == "ingestion"
        assert PIPELINE_STAGES[1] == "scene_splitter"
        assert PIPELINE_STAGES[4] == "scoring"
        assert PIPELINE_STAGES[10] == "renderer"
        assert PIPELINE_STAGES[15] == "publisher"

    def test_video_clip_batch_partition(self):
        """Video + clip + batch stages cover all 16 stages exactly."""
        all_stages = VIDEO_LEVEL_STAGES + CLIP_LEVEL_STAGES + BATCH_LEVEL_STAGES
        assert all_stages == PIPELINE_STAGES

    def test_video_level_stages(self):
        """First 6 stages are video-level."""
        assert len(VIDEO_LEVEL_STAGES) == 6
        assert VIDEO_LEVEL_STAGES[0] == "ingestion"
        assert VIDEO_LEVEL_STAGES[-1] == "clip_builder"

    def test_clip_level_stages(self):
        """Stages 6-13 are clip-level."""
        assert len(CLIP_LEVEL_STAGES) == 8
        assert CLIP_LEVEL_STAGES[0] == "hook_generator"
        assert CLIP_LEVEL_STAGES[-1] == "storage"

    def test_batch_level_stages(self):
        """Last 2 stages are batch-level."""
        assert len(BATCH_LEVEL_STAGES) == 2
        assert BATCH_LEVEL_STAGES == ("scheduler", "publisher")


class TestGetStageIndex:
    """Tests for get_stage_index function."""

    def test_valid_stage(self):
        assert get_stage_index("ingestion") == 0
        assert get_stage_index("publisher") == 15

    def test_invalid_stage(self):
        with pytest.raises(ValueError, match="Unknown pipeline stage"):
            get_stage_index("nonexistent")


class TestGetResumeStageIndex:
    """Tests for get_resume_stage_index function."""

    def test_fresh_run(self):
        """None means start from beginning."""
        assert get_resume_stage_index(None) == 0

    def test_resume_after_ingestion(self):
        """After ingestion, resume at scene_splitter (index 1)."""
        assert get_resume_stage_index("ingestion") == 1

    def test_resume_after_scoring(self):
        """After scoring, resume at clip_builder (index 5)."""
        assert get_resume_stage_index("scoring") == 5


class TestStateConstants:
    """Tests for state machine constants."""

    def test_pipeline_states(self):
        assert "started" in PIPELINE_STATES
        assert "completed" in PIPELINE_STATES
        assert "failed" in PIPELINE_STATES

    def test_clip_states(self):
        assert "generated" in CLIP_STATES
        assert "published" in CLIP_STATES
        assert "failed" in CLIP_STATES


class TestRunTranscriptionSignature:
    """Verify run_transcription calls transcribe() with correct arity."""

    def test_transcribe_accepts_two_args(self):
        """transcribe() must accept exactly 2 positional params."""
        import inspect
        from modules.transcription.transcribe import transcribe

        sig = inspect.signature(transcribe)
        params = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
        ]
        assert len(params) == 2, (
            f"transcribe() should take exactly 2 required params, got {len(params)}"
        )

    def test_run_transcription_forwards_two_args(self):
        """run_transcription must NOT pass scene_list to transcribe()."""
        import inspect
        import ast
        import textwrap

        src = inspect.getsource(Orchestrator.run_transcription)
        src = textwrap.dedent(src)
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "transcribe":
                    assert len(node.args) == 2, (
                        f"transcribe() called with {len(node.args)} args, expected 2"
                    )


class TestResumeLogic:
    """Verify the orchestrator checks for active runs before creating new ones."""

    def test_run_method_calls_get_active_run(self):
        """The run() method must call get_active_run before create_pipeline_run."""
        import inspect

        src = inspect.getsource(Orchestrator.run)
        active_run_pos = src.find("get_active_run")
        create_run_pos = src.find("create_pipeline_run")
        assert active_run_pos != -1, "run() must call get_active_run"
        assert create_run_pos != -1, "run() must call create_pipeline_run"
        assert active_run_pos < create_run_pos, (
            "get_active_run must be called BEFORE create_pipeline_run"
        )

    def test_run_method_uses_get_active_run_for_resume(self):
        """run() must use get_active_run (not get_last_completed_stage) for resume."""
        import inspect

        src = inspect.getsource(Orchestrator.run)
        assert "get_active_run" in src, (
            "run() must use get_active_run for resume detection"
        )
        assert "get_last_completed_stage(self._run_id)" not in src, (
            "run() must not pass run_id to get_last_completed_stage"
        )
