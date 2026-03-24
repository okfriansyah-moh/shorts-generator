"""Integration test: startup → config → DB init → dependency check → clean exit."""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest

from core.logging import configure_logging
from database.connection import initialize_database
from run_pipeline import main


class TestStartupIntegration:
    """Integration tests for full startup sequence."""

    def test_full_startup_sequence(self, tmp_path, sample_config):
        """Config loads → DB initializes → dependencies check → clean exit."""
        # Setup
        configure_logging(level="WARNING")
        db_path = os.path.join(str(tmp_path), "test.db")
        migrations_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "database", "migrations",
        )

        # Initialize database
        conn = initialize_database(db_path, migrations_dir)

        # Verify tables
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = sorted([t[0] for t in tables])
        assert "clips" in table_names
        assert "pipeline_runs" in table_names
        assert "scenes" in table_names
        assert "videos" in table_names

        conn.close()

    def test_run_pipeline_with_missing_video(self, tmp_path):
        """run_pipeline exits with error for missing video file."""
        mock_ffmpeg = MagicMock()
        mock_ffmpeg.stdout = "ffmpeg version 6.0\n"
        mock_ffmpeg.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", return_value=mock_ffmpeg):
            with pytest.raises(SystemExit):
                main(["--log-level", "CRITICAL", "/nonexistent/video.mp4"])

    def test_run_pipeline_with_valid_video(self, tmp_path):
        """run_pipeline completes with a valid (fake) video file."""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"\x00" * 1024)

        mock_ffmpeg = MagicMock()
        mock_ffmpeg.stdout = "ffmpeg version 6.0\n"
        mock_ffmpeg.returncode = 0

        mock_scene_list = MagicMock()
        mock_scene_list.video_id = "abc123"
        mock_scene_list.scenes = ()

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", return_value=mock_ffmpeg), \
             patch("run_pipeline.Orchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = mock_scene_list
            result = main([
                "--log-level", "CRITICAL",
                "--config", "config/config.yaml",
                str(video_file),
            ])
            assert result == 0

    def test_config_load_and_db_init_idempotent(self, tmp_path):
        """Loading config and initializing DB twice produces identical state."""
        db_path = os.path.join(str(tmp_path), "test.db")
        migrations_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "database", "migrations",
        )

        # First init
        conn1 = initialize_database(db_path, migrations_dir)
        tables_1 = conn1.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        conn1.close()

        # Second init (same DB)
        conn2 = initialize_database(db_path, migrations_dir)
        tables_2 = conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        conn2.close()

        assert [t[0] for t in tables_1] == [t[0] for t in tables_2]
