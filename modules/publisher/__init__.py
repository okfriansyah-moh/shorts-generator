"""Publisher module for Shorts Factory.

Uploads scheduled clips to YouTube with full metadata and thumbnail,
then updates lifecycle state. Supports retry with exponential backoff
and delayed visibility transition (unlisted → public).
"""

from .publish import process

__all__ = ["process"]
