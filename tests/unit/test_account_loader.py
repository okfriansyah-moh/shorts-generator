"""Unit tests for core/account_loader.py.

Covers: discover_accounts, resolve_account, load_account_config
— auto-discovery behavior (0/1/N accounts), deep-merge semantics,
output_dir/raw_dir scoping guarantee, and path scoping after deep-merge.
"""

from __future__ import annotations

import os

import pytest
import yaml

from core.account_loader import (
    discover_accounts,
    load_account_config,
    resolve_account,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_account(tmp_path, name: str, content: dict) -> None:
    """Create a config/accounts/<name>/account.yaml under tmp_path."""
    acct_dir = tmp_path / "config" / "accounts" / name
    acct_dir.mkdir(parents=True)
    with open(acct_dir / "account.yaml", "w") as fh:
        yaml.dump(content, fh)


def _global_config() -> dict:
    return {
        "paths": {"output_dir": "output", "raw_dir": "raw", "temp_dir": "output/temp"},
        "metadata": {"language": "en", "max_title_length": 60},
        "scheduler": {"posts_per_day": 1},
        "telegram": {"enabled": True, "bot_token": ""},
    }


# ---------------------------------------------------------------------------
# discover_accounts
# ---------------------------------------------------------------------------

class TestDiscoverAccounts:
    def test_no_accounts_dir_returns_empty(self, tmp_path):
        assert discover_accounts(str(tmp_path / "config")) == []

    def test_empty_accounts_dir_returns_empty(self, tmp_path):
        (tmp_path / "config" / "accounts").mkdir(parents=True)
        assert discover_accounts(str(tmp_path / "config")) == []

    def test_discovers_single_account(self, tmp_path):
        _make_account(tmp_path, "mrkimbum12", {"name": "mrkimbum12"})
        result = discover_accounts(str(tmp_path / "config"))
        assert result == ["mrkimbum12"]

    def test_discovers_multiple_accounts_sorted(self, tmp_path):
        for name in ["zebra", "alpha", "beta"]:
            _make_account(tmp_path, name, {"name": name})
        result = discover_accounts(str(tmp_path / "config"))
        assert result == ["alpha", "beta", "zebra"]

    def test_ignores_dirs_without_account_yaml(self, tmp_path):
        (tmp_path / "config" / "accounts" / "ghost").mkdir(parents=True)
        _make_account(tmp_path, "real", {"name": "real"})
        result = discover_accounts(str(tmp_path / "config"))
        assert result == ["real"]


# ---------------------------------------------------------------------------
# resolve_account
# ---------------------------------------------------------------------------

class TestResolveAccount:
    def test_explicit_name_returned_unchanged(self, tmp_path):
        assert resolve_account("myaccount", str(tmp_path / "config")) == "myaccount"

    def test_auto_discover_single_account(self, tmp_path):
        _make_account(tmp_path, "solo", {"name": "solo"})
        assert resolve_account(None, str(tmp_path / "config")) == "solo"

    def test_zero_accounts_raises(self, tmp_path):
        (tmp_path / "config" / "accounts").mkdir(parents=True)
        with pytest.raises(ValueError, match="No accounts found"):
            resolve_account(None, str(tmp_path / "config"))

    def test_multiple_accounts_raises(self, tmp_path):
        _make_account(tmp_path, "a", {"name": "a"})
        _make_account(tmp_path, "b", {"name": "b"})
        with pytest.raises(ValueError, match="Multiple accounts found"):
            resolve_account(None, str(tmp_path / "config"))


# ---------------------------------------------------------------------------
# load_account_config
# ---------------------------------------------------------------------------

class TestLoadAccountConfig:
    def test_missing_account_yaml_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_account_config("ghost", _global_config(), str(tmp_path / "config"), str(tmp_path))

    def test_output_dir_scoped_to_account(self, tmp_path):
        _make_account(tmp_path, "ch1", {"name": "ch1"})
        merged = load_account_config("ch1", _global_config(), str(tmp_path / "config"), str(tmp_path))
        assert merged["paths"]["output_dir"] == os.path.join("output", "ch1")

    def test_raw_dir_scoped_to_account(self, tmp_path):
        _make_account(tmp_path, "ch1", {"name": "ch1"})
        merged = load_account_config("ch1", _global_config(), str(tmp_path / "config"), str(tmp_path))
        assert merged["paths"]["raw_dir"] == os.path.join("raw", "ch1")

    def test_account_name_injected(self, tmp_path):
        _make_account(tmp_path, "ch1", {"name": "ch1"})
        merged = load_account_config("ch1", _global_config(), str(tmp_path / "config"), str(tmp_path))
        assert merged["_account_name"] == "ch1"

    def test_deep_merge_overrides_leaf(self, tmp_path):
        _make_account(tmp_path, "ch1", {"name": "ch1", "scheduler": {"posts_per_day": 3}})
        merged = load_account_config("ch1", _global_config(), str(tmp_path / "config"), str(tmp_path))
        assert merged["scheduler"]["posts_per_day"] == 3

    def test_deep_merge_preserves_unmentioned_keys(self, tmp_path):
        _make_account(tmp_path, "ch1", {"name": "ch1", "metadata": {"max_title_length": 80}})
        merged = load_account_config("ch1", _global_config(), str(tmp_path / "config"), str(tmp_path))
        assert merged["metadata"]["language"] == "en"
        assert merged["metadata"]["max_title_length"] == 80

    def test_path_scoping_survives_account_paths_override(self, tmp_path):
        """output_dir/raw_dir must stay account-scoped even if account.yaml has a paths: section."""
        _make_account(tmp_path, "ch1", {
            "name": "ch1",
            "paths": {"temp_dir": "output/ch1/tmp"},
        })
        merged = load_account_config("ch1", _global_config(), str(tmp_path / "config"), str(tmp_path))
        assert merged["paths"]["output_dir"] == os.path.join("output", "ch1")
        assert merged["paths"]["raw_dir"] == os.path.join("raw", "ch1")
        assert merged["paths"]["temp_dir"] == "output/ch1/tmp"

    def test_global_config_not_mutated(self, tmp_path):
        _make_account(tmp_path, "ch1", {"name": "ch1", "scheduler": {"posts_per_day": 5}})
        global_cfg = _global_config()
        load_account_config("ch1", global_cfg, str(tmp_path / "config"), str(tmp_path))
        assert global_cfg["scheduler"]["posts_per_day"] == 1

    def test_invalid_yaml_raises(self, tmp_path):
        acct_dir = tmp_path / "config" / "accounts" / "bad"
        acct_dir.mkdir(parents=True)
        (acct_dir / "account.yaml").write_text("- list\n- not\n- a\n- mapping\n")
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            load_account_config("bad", _global_config(), str(tmp_path / "config"), str(tmp_path))
