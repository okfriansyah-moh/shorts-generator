"""SQLite connection manager for Shorts Factory.

Handles connection setup (WAL mode, foreign keys), migration execution,
and connection lifecycle.
"""

from __future__ import annotations

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)


def create_connection(db_path: str) -> sqlite3.Connection:
    """Create a configured SQLite connection.

    Enables WAL mode, foreign keys, and NORMAL synchronous mode.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Configured sqlite3.Connection.
    """
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")

    return conn


def run_migrations(
    conn: sqlite3.Connection,
    migrations_dir: str = "database/migrations",
) -> int:
    """Execute all SQL migrations in lexicographic order.

    All migrations must be idempotent (CREATE TABLE IF NOT EXISTS, etc.).
    Migration failure is fatal — raises RuntimeError.

    Args:
        conn: Active SQLite connection.
        migrations_dir: Directory containing .sql migration files.

    Returns:
        Number of migration files executed.

    Raises:
        RuntimeError: If any migration fails.
        FileNotFoundError: If migrations directory does not exist.
    """
    if not os.path.isdir(migrations_dir):
        raise FileNotFoundError(f"Migrations directory not found: {migrations_dir}")

    migration_files = sorted(
        f for f in os.listdir(migrations_dir) if f.endswith(".sql")
    )

    if not migration_files:
        logger.warning(
            "No migration files found",
            extra={"stage": "startup", "video_id": "", "dir": migrations_dir},
        )
        return 0

    for filename in migration_files:
        filepath = os.path.join(migrations_dir, filename)
        with open(filepath, "r") as f:
            sql = f.read()

        try:
            conn.executescript(sql)
            logger.debug(
                "Migration applied",
                extra={
                    "stage": "startup",
                    "video_id": "",
                    "migration": filename,
                },
            )
        except Exception as exc:
            raise RuntimeError(
                f"Migration failed: {filename}: {exc}"
            ) from exc

    conn.commit()

    logger.info(
        "All migrations applied",
        extra={
            "stage": "startup",
            "video_id": "",
            "migration_count": len(migration_files),
        },
    )
    return len(migration_files)


def initialize_database(
    db_path: str,
    migrations_dir: str = "database/migrations",
) -> sqlite3.Connection:
    """Create connection and run all migrations.

    Args:
        db_path: Path to the SQLite database file.
        migrations_dir: Directory containing .sql migration files.

    Returns:
        Initialized sqlite3.Connection with schema applied.
    """
    conn = create_connection(db_path)
    run_migrations(conn, migrations_dir)
    return conn
