"""Unit tests for core/logging.py — structured JSON logging."""

from __future__ import annotations

import json
import logging

from core.logging import JSONFormatter, configure_logging


class TestJSONFormatter:
    """Tests for JSONFormatter output."""

    def test_basic_format(self):
        """Log record produces valid JSON with required fields."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test_module",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["module"] == "test_module"
        assert data["message"] == "Test message"
        assert "timestamp" in data

    def test_extra_fields_included(self):
        """Extra fields (stage, video_id) are included in output."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="scoring",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Scoring complete",
            args=(),
            exc_info=None,
        )
        record.stage = "scoring"
        record.video_id = "abc123def4567890"

        output = formatter.format(record)
        data = json.loads(output)

        assert data["stage"] == "scoring"
        assert data["video_id"] == "abc123def4567890"

    def test_clip_id_included_for_per_clip(self):
        """clip_id extra field is included when present."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="renderer",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Render complete",
            args=(),
            exc_info=None,
        )
        record.stage = "renderer"
        record.video_id = "abc123"
        record.clip_id = "clip456"

        output = formatter.format(record)
        data = json.loads(output)

        assert data["clip_id"] == "clip456"


class TestConfigureLogging:
    """Tests for configure_logging setup."""

    def test_configure_sets_level(self):
        """configure_logging sets root logger level."""
        configure_logging(level="DEBUG")
        assert logging.getLogger().level == logging.DEBUG

        # Reset
        configure_logging(level="INFO")

    def test_configure_adds_handler(self):
        """configure_logging adds at least one handler."""
        configure_logging(level="INFO")
        assert len(logging.getLogger().handlers) >= 1

    def test_configure_clears_previous_handlers(self):
        """Reconfiguring logging doesn't duplicate handlers."""
        configure_logging(level="INFO")
        count1 = len(logging.getLogger().handlers)
        configure_logging(level="INFO")
        count2 = len(logging.getLogger().handlers)
        assert count1 == count2
