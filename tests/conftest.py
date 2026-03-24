"""Shared test fixtures for Shorts Factory."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest
import yaml

from database.connection import create_connection, run_migrations


@pytest.fixture
def sample_config() -> dict:
    """Load default configuration for testing."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "config.yaml"
    )
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Override paths for testing
    tmp_dir = tempfile.mkdtemp()
    config["paths"]["output_dir"] = os.path.join(tmp_dir, "output")
    config["paths"]["temp_dir"] = os.path.join(tmp_dir, "temp")
    config["paths"]["database"] = os.path.join(tmp_dir, "test.db")

    return config


@pytest.fixture
def test_db() -> sqlite3.Connection:
    """Create a temp SQLite database with all migrations applied."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test.db")
    conn = create_connection(db_path)

    migrations_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "database", "migrations"
    )
    run_migrations(conn, migrations_dir)

    yield conn
    conn.close()


@pytest.fixture
def sample_video_path(tmp_path) -> str:
    """Create a fake video file for testing."""
    video_file = tmp_path / "test_video.mp4"
    video_file.write_bytes(b"\x00" * 1024)
    return str(video_file)
