"""Unit tests for database/connection.py — SQLite setup and migrations."""

from __future__ import annotations

import os
import sqlite3

import pytest

from database.connection import create_connection, run_migrations, initialize_database


class TestCreateConnection:
    """Tests for create_connection function."""

    def test_creates_connection(self, tmp_path):
        """Connection is created successfully."""
        db_path = str(tmp_path / "test.db")
        conn = create_connection(db_path)
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_wal_mode_enabled(self, tmp_path):
        """WAL journal mode is enabled."""
        db_path = str(tmp_path / "test.db")
        conn = create_connection(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_foreign_keys_enabled(self, tmp_path):
        """Foreign keys are enabled."""
        db_path = str(tmp_path / "test.db")
        conn = create_connection(db_path)
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        conn.close()

    def test_creates_parent_directories(self, tmp_path):
        """Parent directories are created if they don't exist."""
        db_path = str(tmp_path / "subdir" / "nested" / "test.db")
        conn = create_connection(db_path)
        assert os.path.exists(db_path)
        conn.close()


class TestRunMigrations:
    """Tests for run_migrations function."""

    def test_applies_all_migrations(self, tmp_path):
        """All migration files are applied."""
        db_path = str(tmp_path / "test.db")
        conn = create_connection(db_path)
        migrations_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "database", "migrations",
        )
        count = run_migrations(conn, migrations_dir)
        assert count == 4
        conn.close()

    def test_creates_all_tables(self, test_db):
        """All four tables exist after migration."""
        tables = test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = sorted([t[0] for t in tables])
        assert "clips" in table_names
        assert "pipeline_runs" in table_names
        assert "scenes" in table_names
        assert "videos" in table_names

    def test_creates_indexes(self, test_db):
        """Expected indexes exist after migration."""
        indexes = test_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' ORDER BY name"
        ).fetchall()
        index_names = sorted([i[0] for i in indexes])
        assert "idx_clips_status" in index_names
        assert "idx_clips_video" in index_names
        assert "idx_scenes_video" in index_names
        assert "idx_runs_video" in index_names

    def test_idempotent_migrations(self, tmp_path):
        """Running migrations twice produces identical state."""
        db_path = str(tmp_path / "test.db")
        conn = create_connection(db_path)
        migrations_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "database", "migrations",
        )

        # Run twice
        run_migrations(conn, migrations_dir)
        run_migrations(conn, migrations_dir)

        # Should still have exactly 4 tables
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "videos" in table_names
        assert "scenes" in table_names
        assert "clips" in table_names
        assert "pipeline_runs" in table_names
        conn.close()

    def test_missing_migrations_dir(self, tmp_path):
        """FileNotFoundError if migrations dir doesn't exist."""
        db_path = str(tmp_path / "test.db")
        conn = create_connection(db_path)
        with pytest.raises(FileNotFoundError):
            run_migrations(conn, "/nonexistent/migrations")
        conn.close()


class TestInitializeDatabase:
    """Tests for initialize_database function."""

    def test_returns_connection(self, tmp_path):
        """Returns a working connection with schema."""
        db_path = str(tmp_path / "test.db")
        migrations_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "database", "migrations",
        )
        conn = initialize_database(db_path, migrations_dir)
        assert isinstance(conn, sqlite3.Connection)

        # Verify tables exist
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        assert tables >= 4
        conn.close()
