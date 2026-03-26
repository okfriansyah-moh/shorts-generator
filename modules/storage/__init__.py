"""Storage module for Shorts Factory.

Verifies, organizes, and persists all pipeline artifacts for a clip.
Returns a StorageRecord DTO for downstream consumption.
"""

from .store import process

__all__ = ["process"]
