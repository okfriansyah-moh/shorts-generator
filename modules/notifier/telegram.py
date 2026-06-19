"""Telegram notification client for Shorts Factory.

Mirrors the crypto-sniping-bot internal/telegram/bot.go design:
  - Client struct  →  TelegramNotifier class
  - SendMessage()  →  send_message()
  - HTML parse mode
  - 10-second HTTP timeout
  - 1 MiB response cap
  - Bot token masked in error messages

Credentials are read from environment variables (preferred) or config:
  SF_TELEGRAM_BOT_TOKEN   — Telegram bot token (from @BotFather)
  SF_TELEGRAM_CHAT_ID     — Target chat/channel ID (integer or "@handle")

Or from config.yaml:
  telegram:
    bot_token_env: "SF_TELEGRAM_BOT_TOKEN"   # env var name
    chat_id_env:   "SF_TELEGRAM_CHAT_ID"     # env var name
    # Optionally hardcode (not recommended):
    # bot_token: "1234:abcd..."
    # chat_id:   "-100123456789"
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_TIMEOUT_SECONDS = 10
_MAX_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MiB


@dataclass
class TelegramNotifier:
    """Thin client around the Telegram Bot API sendMessage endpoint."""

    bot_token: str
    chat_id: str

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: dict) -> "TelegramNotifier":
        """Build a notifier from config.yaml + environment variables.

        Resolution order (highest to lowest):
          1. Env var named by ``telegram.bot_token_env`` / ``telegram.chat_id_env``
          2. Direct ``telegram.bot_token`` / ``telegram.chat_id`` in config
          3. Default env var names ``SF_TELEGRAM_BOT_TOKEN`` / ``SF_TELEGRAM_CHAT_ID``

        Raises ``ValueError`` if credentials cannot be resolved.
        """
        tg = config.get("telegram", {})

        # Env var names (config-overridable, sane defaults)
        token_env = tg.get("bot_token_env", "SF_TELEGRAM_BOT_TOKEN")
        chat_env = tg.get("chat_id_env", "SF_TELEGRAM_CHAT_ID")

        bot_token = (
            os.environ.get(token_env)
            or tg.get("bot_token", "")
        )
        chat_id = (
            os.environ.get(chat_env)
            or tg.get("chat_id", "")
        )

        if not bot_token:
            raise ValueError(
                f"Telegram bot token not found. "
                f"Set the {token_env!r} environment variable or "
                f"'telegram.bot_token' in config.yaml."
            )
        if not chat_id:
            raise ValueError(
                f"Telegram chat ID not found. "
                f"Set the {chat_env!r} environment variable or "
                f"'telegram.chat_id' in config.yaml."
            )

        return cls(bot_token=bot_token, chat_id=str(chat_id))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_message(self, text: str) -> None:
        """Send an HTML-formatted message to the configured chat.

        Mirrors Go's SendMessage(ctx, text) — fires-and-forgets after one
        attempt. Logs a warning on error instead of raising, so a Telegram
        failure never aborts the upload pipeline.
        """
        url = f"{_API_BASE}/bot{self.bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
                raw = resp.read(_MAX_RESPONSE_BYTES)
                data = json.loads(raw)
                if not data.get("ok"):
                    logger.warning(
                        "Telegram API returned ok=false: %s",
                        self._mask_token(str(data)),
                    )
        except urllib.error.HTTPError as exc:
            body = exc.read(_MAX_RESPONSE_BYTES).decode(errors="replace")
            logger.warning(
                "Telegram sendMessage HTTP error %s: %s",
                exc.code,
                self._mask_token(body),
            )
        except Exception as exc:
            logger.warning(
                "Telegram sendMessage failed: %s",
                self._mask_token(str(exc)),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _mask_token(self, text: str) -> str:
        """Replace the bot token in any string with [REDACTED]."""
        return text.replace(self.bot_token, "[REDACTED]")


# ------------------------------------------------------------------
# Message builder
# ------------------------------------------------------------------

def build_publish_message(
    *,
    title: str,
    clip_id: str,
    composite_score: float,
    scheduled_at: Optional[str],
    published_at: Optional[str],
    youtube_id: Optional[str] = None,
    tiktok_id: Optional[str] = None,
    instagram_id: Optional[str] = None,
    facebook_id: Optional[str] = None,
    error_summary: Optional[str] = None,
) -> str:
    """Return an HTML-formatted Telegram message for a published clip.

    Example output:
        🎬 <b>New Short Published!</b>

        📹 <b>Ninja Gaiden — Boss Rush no damage</b>
        🆔 <code>a1b2c3d4</code>
        ⭐ Score: 0.84

        📺 YouTube: https://youtu.be/abc123
        🎵 TikTok: abc123
        📸 Instagram: 12345678
        📘 Facebook: 12345678

        🕐 Scheduled: 2026-06-19T10:00:00Z
        ✅ Published: 2026-06-19T10:02:34Z
    """
    lines: list[str] = []

    lines.append("🎬 <b>New Short Published!</b>")
    lines.append("")

    # Clip metadata
    lines.append(f"📹 <b>{_esc(title)}</b>")
    lines.append(f"🆔 <code>{_esc(clip_id[:8])}</code>")
    lines.append(f"⭐ Score: {composite_score:.2f}")
    lines.append("")

    # Platform links / IDs
    platform_lines: list[str] = []
    if youtube_id:
        platform_lines.append(f"📺 YouTube: https://youtu.be/{youtube_id}")
    if tiktok_id:
        platform_lines.append(f"🎵 TikTok: <code>{_esc(tiktok_id)}</code>")
    if instagram_id:
        platform_lines.append(f"📸 Instagram: <code>{_esc(instagram_id)}</code>")
    if facebook_id:
        platform_lines.append(f"📘 Facebook: <code>{_esc(facebook_id)}</code>")

    if platform_lines:
        lines.extend(platform_lines)
        lines.append("")

    # Timestamps
    if scheduled_at:
        lines.append(f"🕐 Scheduled: {_esc(scheduled_at)}")
    ts = published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.append(f"✅ Published: {_esc(ts)}")

    # Partial failure note
    if error_summary:
        lines.append("")
        lines.append(f"⚠️ Partial failures: {_esc(error_summary)}")

    return "\n".join(lines)


def _esc(text: str) -> str:
    """Minimal HTML escaping for Telegram HTML parse mode."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
