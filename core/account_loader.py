"""Account configuration loader for Shorts Factory.

Loads per-account config from config/accounts/<name>/account.yaml and
merges it with the global config, producing a single backward-compatible
merged config dict.  All existing pipeline modules (publisher, tiktok,
meta, multi_platform) continue to work without modification — the loader
translates the new account.yaml format into the old key shapes they expect.

Account directory layout:
    config/accounts/<account-name>/
        account.yaml              — account settings + platform config
        youtube_credentials.json  — YouTube OAuth2 credentials
        tiktok_credentials.json   — TikTok Content Posting API credentials
        meta_credentials.json     — Meta Graph API credentials (IG + FB)

Merged config guarantees:
  paths.output_dir → output/<account-name>/
  paths.raw_dir    → raw/<account-name>/
  publisher / tiktok / meta / platforms keys populated for backward compat
  _account_name, _account_dir injected for path resolution

Per-account overrideable sections (deep-merged on top of global defaults):
  metadata, scheduler, channel, telegram, tts, compositor, subtitle,
  thumbnail, scoring, clip_builder, hook_generator, pipeline, ingestion,
  video_type (top-level string).

Any key in account.yaml that is not an account-meta key (name, description,
enabled, min_score, platforms) is deep-merged into the global config, so
accounts only need to specify what differs from global defaults.
"""

from __future__ import annotations

import copy
import os
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Keys in account.yaml that are account-meta — NOT deep-merged into pipeline config.
# They are either handled specifically below or are documentation-only.
_ACCOUNT_META_KEYS = frozenset({
    "name", "description", "enabled", "min_score", "platforms",
})


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*. Override wins at every leaf.

    Neither input dict is mutated — a new dict is always returned.
    Non-dict values in override fully replace the corresponding base value.
    """
    result: dict[str, Any] = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_accounts(config_dir: str = "config") -> list[str]:
    """Return sorted list of account names under config/accounts/.

    An account is any subdirectory that contains an account.yaml file.

    Args:
        config_dir: Path to the config directory (relative or absolute).

    Returns:
        Sorted list of account name strings.  Empty list if none found.
    """
    accounts_dir = os.path.join(config_dir, "accounts")
    if not os.path.isdir(accounts_dir):
        return []
    return sorted(
        name for name in os.listdir(accounts_dir)
        if os.path.isfile(os.path.join(accounts_dir, name, "account.yaml"))
    )


def resolve_account(
    account_name: str | None,
    config_dir: str = "config",
) -> str:
    """Resolve an account name — explicit arg → auto-discover → error.

    If ``account_name`` is provided, returns it unchanged (no existence
    check here — load_account_config will raise FileNotFoundError).

    If ``account_name`` is None:
      - Exactly one account found → returns it automatically.
      - Zero accounts → raises ValueError.
      - Multiple accounts → raises ValueError (must be explicit).

    Args:
        account_name: Explicit account name, or None to auto-discover.
        config_dir: Path to the config directory.

    Returns:
        Resolved account name string.

    Raises:
        ValueError: If auto-discovery fails (zero or multiple accounts).
    """
    if account_name:
        return account_name

    accounts = discover_accounts(config_dir)
    if not accounts:
        raise ValueError(
            f"No accounts found under {config_dir}/accounts/. "
            "Create config/accounts/<name>/account.yaml first."
        )
    if len(accounts) == 1:
        return accounts[0]

    raise ValueError(
        f"Multiple accounts found ({', '.join(accounts)}). "
        "Specify --account <name> explicitly."
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_account_config(
    account_name: str,
    global_config: dict[str, Any],
    config_dir: str = "config",
    project_root: str = "",
) -> dict[str, Any]:
    """Load account.yaml and merge with global config.

    Produces a merged config dict that is fully backward-compatible:
      - ``publisher``, ``tiktok``, ``meta``, ``platforms`` keys are
        populated from account.yaml so existing publisher modules need
        no changes.
      - ``paths.output_dir`` is scoped to ``output/<account_name>/``.
      - ``paths.raw_dir``    is scoped to ``raw/<account_name>/``.
      - ``metadata.language`` is overridden from account.yaml if present.
      - ``_account_name`` and ``_account_dir`` are injected.

    Args:
        account_name:  Folder name under config/accounts/.
        global_config: Already-loaded global config (from load_config()).
        config_dir:    Path to the config directory (default "config").
        project_root:  Absolute project root for resolving paths.
                       Defaults to os.getcwd() if empty.

    Returns:
        Merged config dict with account-specific overrides applied.

    Raises:
        FileNotFoundError: If account.yaml does not exist.
        ValueError:        If account.yaml is not a valid YAML mapping.
    """
    if not project_root:
        project_root = os.getcwd()

    account_dir = os.path.abspath(
        os.path.join(project_root, config_dir, "accounts", account_name)
    )
    account_yaml_path = os.path.join(account_dir, "account.yaml")

    if not os.path.isfile(account_yaml_path):
        raise FileNotFoundError(
            f"Account config not found: {account_yaml_path}\n"
            f"  → Create config/accounts/{account_name}/account.yaml first."
        )

    with open(account_yaml_path) as f:
        account_cfg = yaml.safe_load(f)

    if not isinstance(account_cfg, dict):
        raise ValueError(
            f"{account_yaml_path} must be a YAML mapping, "
            f"got {type(account_cfg).__name__}"
        )

    # Deep-copy global config so the original is never mutated
    merged: dict[str, Any] = copy.deepcopy(global_config)

    # ── Account identity ───────────────────────────────────────────────────
    merged["_account_name"] = account_name
    merged["_account_dir"]  = account_dir

    # ── Path overrides ─────────────────────────────────────────────────────
    base_output = global_config.get("paths", {}).get("output_dir", "output")
    base_raw    = global_config.get("paths", {}).get("raw_dir", "raw")
    merged.setdefault("paths", {})
    merged["paths"]["output_dir"] = os.path.join(base_output, account_name)
    merged["paths"]["raw_dir"]    = os.path.join(base_raw,    account_name)

    # ── Generic deep-merge of per-account pipeline overrides ──────────────
    # Any key in account.yaml that is not an account-meta key is deep-merged
    # on top of the global config.  This covers: metadata, scheduler, channel,
    # telegram, tts, compositor, subtitle, thumbnail, scoring, clip_builder,
    # hook_generator, pipeline, ingestion, video_type, and any future keys.
    for key, value in account_cfg.items():
        if key in _ACCOUNT_META_KEYS:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)

    # ── Re-apply path scoping after deep-merge ─────────────────────────────
    # account.yaml may include a paths: section for other keys (e.g. temp_dir).
    # Re-enforce output_dir and raw_dir so the account scoping guarantee holds
    # regardless of what the deep-merge loop wrote to merged["paths"].
    merged.setdefault("paths", {})
    merged["paths"]["output_dir"] = os.path.join(base_output, account_name)
    merged["paths"]["raw_dir"]    = os.path.join(base_raw,    account_name)

    # ── Platform configs ───────────────────────────────────────────────────
    platforms_cfg: dict[str, Any] = account_cfg.get("platforms") or {}

    def _abs_cred(filename: str) -> str:
        """Resolve a credential filename to absolute path inside account_dir."""
        return os.path.join(account_dir, filename)

    def _is_enabled(platform_dict: dict) -> bool:
        val = platform_dict.get("enabled", False)
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "yes", "1", "enabled")

    yt_cfg = platforms_cfg.get("youtube")  or {}
    tt_cfg = platforms_cfg.get("tiktok")   or {}
    ig_cfg = platforms_cfg.get("instagram") or {}
    fb_cfg = platforms_cfg.get("facebook") or {}

    # ── platforms (old string-format) — consumed by _is_platform_enabled() ─
    merged["platforms"] = {
        "youtube":   "enabled" if _is_enabled(yt_cfg) else "disabled",
        "tiktok":    "enabled" if _is_enabled(tt_cfg) else "disabled",
        "instagram": "enabled" if _is_enabled(ig_cfg) else "disabled",
        "facebook":  "enabled" if _is_enabled(fb_cfg) else "disabled",
    }

    # ── publisher (YouTube) — consumed by YouTubeClient(config["publisher"]) ─
    yt_cred = yt_cfg.get("credentials", "youtube_credentials.json")
    merged["publisher"] = {
        "platform":             "youtube",
        "credentials_path":     _abs_cred(yt_cred),
        "max_retries":          global_config.get("publisher", {}).get("max_retries", 3),
        "retry_delays":         global_config.get("publisher", {}).get("retry_delays", [60, 300, 900]),
        "initial_visibility":   yt_cfg.get("initial_visibility", "unlisted"),
        "public_delay_minutes": yt_cfg.get("public_delay_minutes", 30),
    }

    # ── tiktok — consumed by TikTokClient(config) via config["tiktok"] ───
    tt_cred = tt_cfg.get("credentials", "tiktok_credentials.json")
    merged["tiktok"] = {
        "credentials_path": _abs_cred(tt_cred),
        "privacy_level":    tt_cfg.get("privacy_level",  "PUBLIC_TO_EVERYONE"),
        "disable_duet":     tt_cfg.get("disable_duet",   False),
        "disable_stitch":   tt_cfg.get("disable_stitch", False),
        "disable_comment":  tt_cfg.get("disable_comment", False),
    }

    # ── meta — consumed by MetaClient(config) via config["meta"] ─────────
    # IG and FB share one credentials file; prefer instagram's path
    meta_cred = (
        ig_cfg.get("credentials")
        or fb_cfg.get("credentials")
        or "meta_credentials.json"
    )
    merged["meta"] = {
        "credentials_path": _abs_cred(meta_cred),
        "serve_port":       ig_cfg.get("serve_port", 8080),
        "public_ip":        ig_cfg.get("public_ip",  ""),
    }

    return merged
