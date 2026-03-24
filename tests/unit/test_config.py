"""Unit tests for core/config.py — YAML loader, validation, env overrides."""

from __future__ import annotations

import os

import pytest
import yaml

from core.config import load_config, _validate_config, _apply_env_overrides, _convert_env_value


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_valid_config(self):
        """Default config.yaml loads without error."""
        config = load_config("config/config.yaml")
        assert isinstance(config, dict)
        assert "paths" in config
        assert "pipeline" in config
        assert "scoring" in config

    def test_load_missing_file(self):
        """Missing config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="not found"):
            load_config("/nonexistent/config.yaml")

    def test_load_invalid_yaml(self, tmp_path):
        """Non-mapping YAML raises ValueError."""
        config_file = tmp_path / "bad.yaml"
        config_file.write_text("just a string\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_config(str(config_file))

    def test_load_missing_sections(self, tmp_path):
        """Config with missing required sections raises ValueError."""
        config_file = tmp_path / "partial.yaml"
        config_file.write_text(yaml.dump({"paths": {"output_dir": "out", "temp_dir": "tmp", "database": "db"}}))
        with pytest.raises(ValueError, match="Missing required configuration sections"):
            load_config(str(config_file))


class TestValidateConfig:
    """Tests for _validate_config function."""

    def test_valid_config_passes(self, sample_config):
        """Complete config passes validation."""
        _validate_config(sample_config)  # Should not raise

    def test_missing_required_section(self):
        """Missing top-level section is detected."""
        config = {"paths": {"output_dir": "out", "temp_dir": "tmp", "database": "db"}}
        with pytest.raises(ValueError, match="Missing required configuration sections"):
            _validate_config(config)

    def test_missing_required_key(self, sample_config):
        """Missing key within a required section is detected."""
        del sample_config["paths"]["output_dir"]
        with pytest.raises(ValueError, match="paths.output_dir"):
            _validate_config(sample_config)


class TestEnvOverrides:
    """Tests for environment variable overrides."""

    def test_override_string_value(self, sample_config):
        """SF_OUTPUT_DIR overrides paths.output_dir."""
        os.environ["SF_OUTPUT_DIR"] = "/custom/output"
        try:
            result = _apply_env_overrides(sample_config)
            assert result["paths"]["output_dir"] == "/custom/output"
        finally:
            del os.environ["SF_OUTPUT_DIR"]

    def test_override_numeric_value(self, sample_config):
        """SF_FFMPEG_TIMEOUT overrides with integer conversion."""
        os.environ["SF_FFMPEG_TIMEOUT"] = "600"
        try:
            result = _apply_env_overrides(sample_config)
            assert result["pipeline"]["ffmpeg_timeout"] == 600
            assert isinstance(result["pipeline"]["ffmpeg_timeout"], int)
        finally:
            del os.environ["SF_FFMPEG_TIMEOUT"]

    def test_no_override_when_unset(self, sample_config):
        """Config unchanged when no SF_ env vars are set."""
        original_output = sample_config["paths"]["output_dir"]
        # Ensure none of the SF_ vars are set
        for key in ("SF_OUTPUT_DIR", "SF_TEMP_DIR", "SF_DATABASE"):
            os.environ.pop(key, None)
        result = _apply_env_overrides(sample_config)
        assert result["paths"]["output_dir"] == original_output


class TestConvertEnvValue:
    """Tests for _convert_env_value type conversion."""

    def test_convert_integer(self):
        assert _convert_env_value("42") == 42

    def test_convert_float(self):
        assert _convert_env_value("3.14") == 3.14

    def test_convert_bool_true(self):
        assert _convert_env_value("true") is True

    def test_convert_bool_false(self):
        assert _convert_env_value("false") is False

    def test_convert_string(self):
        assert _convert_env_value("hello") == "hello"
