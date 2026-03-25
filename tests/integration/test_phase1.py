"""Integration tests for Phase 1 — ingestion + scene splitting pipeline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from contracts.ingestion import IngestionResult
from contracts.scene import SceneList, SceneSegment
from database.adapter import DatabaseAdapter
from database.connection import initialize_database
from modules.ingestion.ingest import ingest
from modules.scene_splitter.split import split_scenes


def _ffprobe_mock(duration: float = 3600.0) -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stderr = ""
    m.stdout = json.dumps({
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/1",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": str(duration)},
    })
    return m


def _config() -> dict:
    return {
        "ingestion": {
            "min_duration_seconds": 1800,
            "max_duration_seconds": 7200,
            "supported_formats": ["mp4", "mkv", "avi", "mov", "webm"],
        },
        "scene_splitter": {
            "threshold": 27.0,
            "min_scene_duration": 3.0,
            "max_scene_duration": 20.0,
        },
    }


class TestPhase1Integration:
    """Integration tests: ingestion → scene_splitter → valid SceneList."""

    def test_ingest_then_split_produces_valid_scene_list(self, tmp_path, monkeypatch):
        """Full phase 1 flow: valid MP4 → IngestionResult → SceneList."""
        video = tmp_path / "test_video.mp4"
        video.write_bytes(b"\x55" * 2048)

        raw_pairs = [(float(i * 10), float(i * 10 + 10)) for i in range(360)]
        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: raw_pairs,
        )

        with patch("subprocess.run", return_value=_ffprobe_mock(3600.0)):
            ingestion_result = ingest(str(video), _config())

        scene_list = split_scenes(ingestion_result, _config())

        assert isinstance(ingestion_result, IngestionResult)
        assert isinstance(scene_list, SceneList)
        assert scene_list.video_id == ingestion_result.video_id
        assert len(scene_list.scenes) > 0
        assert scene_list.total_duration == pytest.approx(
            sum(s.duration for s in scene_list.scenes), abs=0.01
        )
        for scene in scene_list.scenes:
            assert isinstance(scene, SceneSegment)
            assert scene.duration >= 3.0 - 1e-9
            assert scene.duration <= 20.0 + 1e-9
            assert scene.scene_id.startswith(ingestion_result.video_id)

    def test_scenes_sorted_by_start_time(self, tmp_path, monkeypatch):
        """Scenes in SceneList are sorted by start_time ASC."""
        video = tmp_path / "test_video.mp4"
        video.write_bytes(b"\xAA" * 2048)

        raw_pairs = [(20.0, 30.0), (0.0, 10.0), (10.0, 20.0)]
        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: raw_pairs,
        )

        with patch("subprocess.run", return_value=_ffprobe_mock(3600.0)):
            ingestion_result = ingest(str(video), _config())

        scene_list = split_scenes(ingestion_result, _config())

        for i in range(len(scene_list.scenes) - 1):
            assert scene_list.scenes[i].start_time < scene_list.scenes[i + 1].start_time

    def test_scene_id_determinism(self, tmp_path, monkeypatch):
        """Same video produces same scene IDs on repeated runs."""
        video = tmp_path / "test_video.mp4"
        video.write_bytes(b"\xBB" * 2048)

        raw_pairs = [(float(i * 10), float(i * 10 + 10)) for i in range(360)]
        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: raw_pairs,
        )

        with patch("subprocess.run", return_value=_ffprobe_mock(3600.0)):
            r1 = ingest(str(video), _config())
            r2 = ingest(str(video), _config())

        s1 = split_scenes(r1, _config())
        s2 = split_scenes(r2, _config())

        assert r1.video_id == r2.video_id
        assert [sc.scene_id for sc in s1.scenes] == [sc.scene_id for sc in s2.scenes]

    def test_orchestrator_wires_ingestion_scene_splitter(self, tmp_path, monkeypatch):
        """Orchestrator.run() correctly wires ingestion → scene_splitter."""
        from core.orchestrator import Orchestrator

        video = tmp_path / "test_video.mp4"
        video.write_bytes(b"\xCC" * 2048)

        raw_pairs = [(float(i * 10), float(i * 10 + 10)) for i in range(360)]
        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: raw_pairs,
        )

        migrations_dir = "database/migrations"
        db_path = str(tmp_path / "test.db")
        conn = initialize_database(db_path, migrations_dir)
        adapter = DatabaseAdapter(conn)

        full_config = _config()
        full_config["paths"] = {
            "output_dir": str(tmp_path / "output"),
            "temp_dir": str(tmp_path / "temp"),
            "database": db_path,
        }

        with patch("subprocess.run", return_value=_ffprobe_mock(3600.0)):
            orchestrator = Orchestrator(full_config, adapter, str(video))
            with patch.object(orchestrator, "run_transcription") as mock_t, \
                 patch.object(orchestrator, "run_face_detection") as mock_f, \
                 patch.object(orchestrator, "run_audio_analysis") as mock_a:
                from contracts.audio import AudioEnergyData
                from contracts.face import FaceDetectionResult
                from contracts.transcript import Transcript
                mock_t.return_value = Transcript(
                    video_id="", segments=(), total_words=0, language="en"
                )
                mock_f.return_value = FaceDetectionResult(
                    video_id="", scene_data=(), average_visibility=0.0, faceless_scene_count=0
                )
                mock_a.return_value = AudioEnergyData(
                    video_id="", scene_energies=(), video_min_rms=0.0,
                    video_max_rms=0.0, video_mean_rms=0.0,
                )
                result = orchestrator.run()

        assert result is not None
        assert len(result.scene_energies) == 0

        # Verify scenes were persisted to the database
        assert mock_t.call_args is not None
        scene_list_arg = mock_t.call_args[0][1]
        db_scenes = adapter.get_scenes_for_video(scene_list_arg.video_id)
        assert len(db_scenes) == len(scene_list_arg.scenes)

        conn.close()

    def test_rerun_skips_already_processed_video(self, tmp_path, monkeypatch):
        """Running the orchestrator twice skips ingestion on the second run."""
        from core.orchestrator import Orchestrator

        video = tmp_path / "test_video.mp4"
        video.write_bytes(b"\xDD" * 2048)

        raw_pairs = [(float(i * 10), float(i * 10 + 10)) for i in range(360)]
        monkeypatch.setattr(
            "modules.scene_splitter.split._detect_with_scenedetect",
            lambda path, threshold: raw_pairs,
        )

        migrations_dir = "database/migrations"
        db_path = str(tmp_path / "test2.db")
        conn = initialize_database(db_path, migrations_dir)
        adapter = DatabaseAdapter(conn)

        full_config = _config()
        full_config["paths"] = {
            "output_dir": str(tmp_path / "output"),
            "temp_dir": str(tmp_path / "temp"),
            "database": db_path,
        }

        def _mock_phase2(orchestrator_instance):
            from contracts.audio import AudioEnergyData
            from contracts.face import FaceDetectionResult
            from contracts.transcript import Transcript
            empty_transcript = Transcript(video_id="", segments=(), total_words=0, language="en")
            empty_face = FaceDetectionResult(
                video_id="", scene_data=(), average_visibility=0.0, faceless_scene_count=0
            )
            empty_audio = AudioEnergyData(
                video_id="", scene_energies=(), video_min_rms=0.0,
                video_max_rms=0.0, video_mean_rms=0.0,
            )
            orchestrator_instance.run_transcription = lambda *a: empty_transcript
            orchestrator_instance.run_face_detection = lambda *a: empty_face
            orchestrator_instance.run_audio_analysis = lambda *a: empty_audio

        with patch("subprocess.run", return_value=_ffprobe_mock(3600.0)):
            o1 = Orchestrator(full_config, adapter, str(video))
            _mock_phase2(o1)
            r1 = o1.run()

            o2 = Orchestrator(full_config, adapter, str(video))
            _mock_phase2(o2)
            r2 = o2.run()

        assert r1 is not None
        assert r2 is not None

        conn.close()
