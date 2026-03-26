"""Analytics module for Shorts Factory.

Generates structured observability reports after each pipeline run.
Reports include per-run summaries, quality distributions, and publishing status.

This module does NOT access the database. All data is passed in by the orchestrator
as frozen dataclass DTOs.
"""

from .pipeline_report import process

__all__ = ["process"]
