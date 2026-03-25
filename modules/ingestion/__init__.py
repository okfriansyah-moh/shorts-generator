"""Ingestion module for Shorts Factory.

Public interface: ingest(file_path, config) -> IngestionResult
"""

from .ingest import ingest, IngestionError

__all__ = ["ingest", "IngestionError"]
