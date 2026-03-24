"""Unit tests for core/dependencies.py — FFmpeg, FFprobe, Python checks."""

from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock

import pytest

from core.dependencies import (
    check_python_version,
    check_ffmpeg,
    check_ffprobe,
    check_all_dependencies,
)


class TestCheckPythonVersion:
    """Tests for Python version check."""

    def test_current_version_passes(self):
        """Current Python version should pass (we're running >= 3.10)."""
        assert check_python_version() is True

    def test_old_version_exits(self):
        """Python < 3.10 should cause SystemExit."""
        with patch.object(sys, "version_info", (3, 9, 0)):
            with pytest.raises(SystemExit):
                check_python_version()


class TestCheckFFmpeg:
    """Tests for FFmpeg availability check."""

    def test_ffmpeg_available(self):
        """FFmpeg check succeeds when ffmpeg is in PATH."""
        mock_result = MagicMock()
        mock_result.stdout = "ffmpeg version 6.0\n"
        mock_result.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", return_value=mock_result):
            assert check_ffmpeg() is True

    def test_ffmpeg_missing(self):
        """Missing FFmpeg causes SystemExit."""
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                check_ffmpeg()


class TestCheckFFprobe:
    """Tests for FFprobe availability check."""

    def test_ffprobe_available(self):
        """FFprobe check succeeds when ffprobe is in PATH."""
        mock_result = MagicMock()
        mock_result.stdout = "ffprobe version 6.0\n"
        mock_result.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/ffprobe"), \
             patch("subprocess.run", return_value=mock_result):
            assert check_ffprobe() is True

    def test_ffprobe_missing(self):
        """Missing FFprobe causes SystemExit."""
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                check_ffprobe()


class TestCheckAllDependencies:
    """Tests for combined dependency check."""

    def test_all_pass(self):
        """All checks pass with mocked dependencies."""
        mock_result = MagicMock()
        mock_result.stdout = "version 6.0\n"
        mock_result.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", return_value=mock_result):
            assert check_all_dependencies() is True
