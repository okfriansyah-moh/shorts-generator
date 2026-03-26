"""Scheduler module for Shorts Factory.

Assigns publish dates to queued clips based on score ordering
and configurable scheduling constraints.
"""

from .schedule import process

__all__ = ["process"]
