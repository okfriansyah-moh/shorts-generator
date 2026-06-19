#!/usr/bin/env python3
"""Apply Claude-generated AI metadata back into the Shorts Factory database.

This script is called by the Cowork scheduled task AFTER Claude has:
  1. Read  output/pending_ai_metadata.json  (clip data exported by scheduled_run.py)
  2. Generated viral metadata for each clip
  3. Written output/ai_metadata_results.json (Claude's output)

This script then reads ai_metadata_results.json and updates the clips table.

Expected input format (output/ai_metadata_results.json):
{
  "results": [
    {
      "clip_id": "abc123",
      "title": "...",
      "description": "...",
      "tags": ["tag1", "tag2", ...],
      "viral_confidence": 0.85,
      "viral_reasoning": "...",
      "used": true
    },
    ...
  ]
}

Clips with "used": false are left unchanged (template metadata kept).

Exit codes:
    0 — success
    1 — input file missing or malformed
    2 — database error
"""

from __future__ import annotations

import json
import logging
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.config import load_config  # noqa: E402
from core.logging import configure_logging  # noqa: E402
from database.connection import initialize_database  # noqa: E402

logger = logging.getLogger(__name__)

RESULTS_PATH = os.path.join(_PROJECT_ROOT, "output", "ai_metadata_results.json")
PENDING_PATH = os.path.join(_PROJECT_ROOT, "output", "pending_ai_metadata.json")


def main() -> int:
    os.chdir(_PROJECT_ROOT)

    try:
        config = load_config()
    except Exception as exc:
        print(f"[apply_ai_metadata] FATAL: config load failed: {exc}", file=sys.stderr)
        return 2

    configure_logging(level=config.get("logging", {}).get("level", "INFO"))

    # ── Load results file ─────────────────────────────────────────────────────
    if not os.path.isfile(RESULTS_PATH):
        logger.warning(
            "[apply_ai_metadata] No results file found at %s — nothing to apply.",
            RESULTS_PATH,
        )
        return 0

    try:
        with open(RESULTS_PATH) as f:
            data = json.load(f)
        results = data.get("results", [])
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[apply_ai_metadata] Failed to read results file: %s", exc)
        return 1

    if not results:
        logger.info("[apply_ai_metadata] Results file is empty — nothing to apply.")
        return 0

    # ── Open database ─────────────────────────────────────────────────────────
    db_path = config.get("paths", {}).get("database", "output/shorts_factory.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(_PROJECT_ROOT, db_path)

    try:
        conn = initialize_database(db_path)
    except Exception as exc:
        logger.error("[apply_ai_metadata] DB init failed: %s", exc)
        return 2

    applied = 0
    skipped = 0

    for item in results:
        clip_id = item.get("clip_id", "")
        used = item.get("used", False)

        if not clip_id:
            logger.warning("[apply_ai_metadata] Skipping result with no clip_id.")
            skipped += 1
            continue

        if not used:
            logger.info(
                "[apply_ai_metadata] clip %s: AI metadata rejected (confidence=%.2f) — keeping template.",
                clip_id,
                item.get("viral_confidence", 0.0),
            )
            skipped += 1
            continue

        title = str(item.get("title", "")).strip()
        description = str(item.get("description", "")).strip()
        raw_tags = item.get("tags", [])
        tags_json = json.dumps([str(t).strip().lower() for t in raw_tags if str(t).strip()])
        viral_confidence = float(item.get("viral_confidence", 0.0))
        viral_reasoning = str(item.get("viral_reasoning", "")).strip()

        if not title or not description:
            logger.warning(
                "[apply_ai_metadata] clip %s: missing title or description — skipping.",
                clip_id,
            )
            skipped += 1
            continue

        try:
            conn.execute(
                """UPDATE clips
                   SET title = ?,
                       description = ?,
                       tags = ?,
                       viral_confidence = ?,
                       viral_reasoning = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE clip_id = ?""",
                (title, description, tags_json, viral_confidence, viral_reasoning, clip_id),
            )
            conn.commit()
            applied += 1
            logger.info(
                "[apply_ai_metadata] clip %s: AI metadata applied (confidence=%.2f).",
                clip_id,
                viral_confidence,
            )
        except Exception as exc:
            logger.error(
                "[apply_ai_metadata] clip %s: DB update failed: %s",
                clip_id,
                exc,
            )
            skipped += 1

    conn.close()

    logger.info(
        "[apply_ai_metadata] Done — %d applied, %d skipped.",
        applied,
        skipped,
    )

    # Clean up processed files
    for path in (RESULTS_PATH, PENDING_PATH):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
