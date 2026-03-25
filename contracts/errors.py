"""Structured error classification for Shorts Factory.

Provides deterministic error types for all pipeline failures.
Used by the orchestrator for logging and state transitions.
"""

from __future__ import annotations

from enum import Enum


class ErrorType(Enum):
    """Structured failure classification for pipeline errors."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    PROCESS_ERROR = "PROCESS_ERROR"
    DATA_ERROR = "DATA_ERROR"


class PipelineError(Exception):
    """Base exception for all pipeline errors with structured classification."""

    def __init__(self, message: str, error_type: ErrorType) -> None:
        super().__init__(message)
        self.error_type = error_type


class ValidationError(PipelineError):
    """Input validation failure (bad config, invalid video, constraint violation)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorType.VALIDATION_ERROR)


class DependencyError(PipelineError):
    """External dependency failure (FFmpeg, PySceneDetect, disk space)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorType.DEPENDENCY_ERROR)


class ProcessError(PipelineError):
    """Processing failure (FFmpeg crash, rendering failure, timeout)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorType.PROCESS_ERROR)


class DataError(PipelineError):
    """Data integrity issue (corrupt file, missing DB record, DTO mismatch)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorType.DATA_ERROR)


def classify_error(exc: Exception) -> ErrorType:
    """Classify an arbitrary exception into a structured error type."""
    if isinstance(exc, PipelineError):
        return exc.error_type
    if isinstance(exc, (FileNotFoundError, PermissionError, OSError)):
        return ErrorType.DEPENDENCY_ERROR
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return ErrorType.VALIDATION_ERROR
    return ErrorType.PROCESS_ERROR
