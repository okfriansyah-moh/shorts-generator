"""Structured JSON logging for Shorts Factory.

Provides a JSON formatter with the 7+1 required base fields and
dual-output support (stdout + per-run log file).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Format log records as JSON with required structured fields."""

    EXTRA_KEYS = (
        "stage",
        "video_id",
        "run_id",
        "clip_id",
        "scene_count",
        "word_count",
        "clips_selected",
        "top_score",
        "duration",
        "file_path",
        "resolution",
        "has_audio",
        "clip_index",
        "total_clips",
        "error",
        "version",
        "returncode",
        "config_path",
        "database",
        "video_path",
        "stages",
        "required",
        "current",
        "dir",
        "migration",
        "total_duration",
    )

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }

        # Merge standard extra fields
        for key in self.EXTRA_KEYS:
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json.dumps(log_entry, default=str)


def configure_logging(
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """Configure root logger with structured JSON output.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Optional path for file-based logging output.
    """
    root_logger = logging.getLogger()

    # Remove and close existing handlers to avoid duplicates and file descriptor leaks
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(stdout_handler)

    # Optional file handler
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(file_handler)
