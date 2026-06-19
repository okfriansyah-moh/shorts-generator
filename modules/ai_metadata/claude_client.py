"""Anthropic Claude HTTP client for Shorts Factory.

Thin, zero-dependency wrapper around the Anthropic Messages API.
Uses only stdlib (urllib, json) — no anthropic SDK required.

Reads the API key from the environment variable specified in config
(default: ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


class ClaudeClient:
    """Minimal Anthropic Messages API client.

    Args:
        config: Full pipeline config dict. Reads from config['ai_metadata']:
            - api_key_env (str): env var name holding the Anthropic API key.
                                 Default: "ANTHROPIC_API_KEY".
            - model (str): Claude model ID.
                           Default: "claude-haiku-4-5-20251001".
            - max_tokens (int): Max tokens in the response. Default: 1024.
            - temperature (float): Sampling temperature 0.0–1.0. Default: 0.7.
    """

    def __init__(self, config: dict) -> None:
        ai_cfg = config.get("ai_metadata", {})
        api_key_env = ai_cfg.get("api_key_env", "ANTHROPIC_API_KEY")
        self._api_key = os.environ.get(api_key_env, "")
        self._model = ai_cfg.get("model", "claude-haiku-4-5-20251001")
        self._max_tokens = int(ai_cfg.get("max_tokens", 1024))
        self._temperature = float(ai_cfg.get("temperature", 0.7))

    def is_configured(self) -> bool:
        """Return True if an API key is available."""
        return bool(self._api_key)

    def complete(self, system: str, user: str) -> str:
        """Send a single-turn request and return the assistant text response.

        Args:
            system: System prompt.
            user: User message content.

        Returns:
            Assistant response text.

        Raises:
            RuntimeError: On HTTP or parsing errors.
        """
        if not self._api_key:
            raise RuntimeError(
                "Anthropic API key not set. "
                "Export ANTHROPIC_API_KEY in your environment."
            )

        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        body = json.dumps(payload).encode()

        req = urllib.request.Request(
            _ANTHROPIC_API_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                response_body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            error_text = exc.read().decode(errors="replace")
            raise RuntimeError(
                f"Claude API error ({exc.code}): {error_text}"
            ) from exc

        try:
            text = response_body["content"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(
                f"Unexpected Claude API response shape: {response_body}"
            ) from exc

        logger.debug(
            "Claude response received",
            extra={
                "stage": "ai_metadata",
                "model": self._model,
                "input_tokens": response_body.get("usage", {}).get("input_tokens"),
                "output_tokens": response_body.get("usage", {}).get("output_tokens"),
            },
        )
        return text
