#!/usr/bin/env python3
"""Shorts Factory — Pipeline Entry Point.

Parses arguments, loads configuration, verifies dependencies,
initializes the database, and launches the pipeline.

Usage:
    python run_pipeline.py <video_file_path>
    python run_pipeline.py --config config/config.yaml <video_file_path>
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from core.config import load_config
from core.dependencies import check_all_dependencies
from core.logging import configure_logging
from core.orchestrator import PIPELINE_STAGES, Orchestrator, PipelineResult
from database.adapter import DatabaseAdapter
from database.connection import initialize_database

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Shorts Factory — Transform long-form videos into YouTube Shorts.",
    )
    parser.add_argument(
        "video_path",
        help="Path to the input video file.",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to configuration file (default: config/config.yaml).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: INFO).",
    )
    return parser.parse_args(argv)


def validate_video_path(video_path: str) -> str:
    """Validate that the video file exists and return its absolute path.

    Raises:
        SystemExit: If the file does not exist.
    """
    abs_path = os.path.abspath(video_path)
    if not os.path.isfile(abs_path):
        logger.critical(
            "Video file not found",
            extra={"stage": "startup", "video_id": "", "file_path": abs_path},
        )
        sys.exit(1)
    return abs_path


def setup_output_dirs(config: dict) -> None:
    """Create output directories as specified in config."""
    output_dir = config["paths"]["output_dir"]
    temp_dir = config["paths"]["temp_dir"]
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the pipeline."""
    args = parse_args(argv)

    # Configure logging first
    configure_logging(level=args.log_level)

    logger.info(
        "Shorts Factory starting",
        extra={"stage": "startup", "video_id": ""},
    )

    # Load configuration
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        logger.critical(
            "Configuration error",
            extra={"stage": "startup", "video_id": "", "error": str(exc)},
        )
        return 1

    # Check dependencies
    check_all_dependencies()

    # Validate video file
    video_path = validate_video_path(args.video_path)

    # Setup output directories
    setup_output_dirs(config)

    # Initialize database
    db_path = config["paths"]["database"]
    try:
        conn = initialize_database(db_path)
    except RuntimeError as exc:
        logger.critical(
            "Database initialization failed",
            extra={"stage": "startup", "video_id": "", "error": str(exc)},
        )
        return 1

    logger.info(
        "Infrastructure ready",
        extra={
            "stage": "startup",
            "video_id": "",
            "database": db_path,
            "video_path": video_path,
            "stages": len(PIPELINE_STAGES),
        },
    )

    # Run pipeline through implemented stages
    adapter = DatabaseAdapter(conn)
    orchestrator = Orchestrator(config=config, adapter=adapter, video_path=video_path)
    result = orchestrator.run()

    conn.close()

    if result is None:
        logger.critical(
            "Pipeline execution failed",
            extra={"stage": "startup", "video_id": ""},
        )
        return 1

    logger.info(
        "Pipeline finished",
        extra={
            "stage": "startup",
            "video_id": result.video_id,
            "scene_count": len(result.scene_list.scenes),
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
