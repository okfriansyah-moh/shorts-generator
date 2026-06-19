#!/usr/bin/env python3
"""AI Metadata Enricher for Shorts Factory.

Run by the Claude Cowork scheduled task (8am daily).
Claude reads the exported clips, rewrites titles/descriptions/tags,
then calls this script again with --apply to write enriched data to DB
and assign scheduled_at upload times.

Usage:
  # Step 1 — export queued clips needing enrichment (Claude reads this):
  python3 scripts/ai_enricher.py --export

  # Step 2 — apply Claude's enriched JSON back to DB:
  python3 scripts/ai_enricher.py --apply enriched.json

  # Check enrichment status:
  python3 scripts/ai_enricher.py --status

Exit codes:
  0 — success
  1 — error
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.config import load_config          # noqa: E402
from core.logging import configure_logging   # noqa: E402
from database.adapter import DatabaseAdapter # noqa: E402
from database.connection import initialize_database  # noqa: E402

logger = logging.getLogger(__name__)

ENRICHED_FLAG = "enriched"  # status value after AI enrichment, before upload


# ---------------------------------------------------------------------------
# Language-aware enrichment guidelines
# ---------------------------------------------------------------------------

def _enrichment_guidelines(language: str) -> dict:
    """Return per-field enrichment guidelines for the given language.

    These are embedded in the --export JSON so Claude follows them during
    the 8am enrichment step regardless of what's hardcoded in the SKILL.md.
    When this dict is present in the export, Claude MUST follow it instead
    of any default English instructions.
    """
    # ── Per-language rich guidelines (filler lists, tone examples, etc.) ──────
    _RICH: dict[str, dict] = {
        "id": {
            "language": "id",
            "language_name": "Bahasa Indonesia (casual/conversational — bukan bahasa formal)",
            "important": (
                "Semua teks WAJIB dalam Bahasa Indonesia yang kasual dan natural. "
                "Gaya bahasa seperti ngobrol sama teman gamer — santai, energik, nggak kaku. "
                "Boleh mix istilah gaming dalam bahasa Inggris (boss fight, no damage, combo, dll) "
                "karena itu udah jadi bahasa sehari-hari gamer Indonesia."
            ),
            "title": {
                "max_chars": 60,
                "rules": [
                    "Mulai dari momen paling seru atau mengejutkan di clip",
                    "Pakai bahasa aktif dan energik — kayak lagi cerita ke temen",
                    "Contoh gaya yang bener: 'Kabur dari 3 musuh pakai 1 jurus', 'Kenapa boss ini bikin frustrasi banget'",
                    "JANGAN pakai frasa generik: 'Kamu harus lihat ini', 'Tunggu yang ini', 'Nggak nyangka'",
                    "Sertakan nama game kalau terasa natural — jangan dipaksa",
                    "Setiap judul HARUS unik — nggak boleh ada dua clip dengan judul yang sama",
                ],
                "filler_to_avoid": [
                    "Kamu harus lihat ini", "Hentikan dan tonton", "Tunggu bagian terbaiknya",
                    "Ini nggak nyata", "Sebuah momen", "Tonton ini", "Luar biasa banget",
                    "Keren parah", "Nggak percaya",
                ],
            },
            "description": {
                "max_chars": 200,
                "rules": [
                    "Kalimat pertama: hook yang langsung ngejelasin apa yang terjadi di clip",
                    "Kalimat kedua: konteks atau kenapa momen ini keren/susah/lucu",
                    "Akhiri dengan 3-5 hashtag yang relevan — jangan pakai #Shorts #Gaming #Clips yang terlalu generik",
                    "Gaya penulisan santai dan natural, bukan seperti artikel berita",
                ],
            },
            "tags": {
                "count": "10-15 tag",
                "rules": [
                    "Mix antara tag spesifik dan yang lebih luas (gaming highlight, boss fight, action game)",
                    "Sertakan tag kesulitan kalau relevan: game susah, no damage, one life, tanpa mati",
                    "Campuran bahasa lokal dan Inggris OK untuk istilah gaming yang umum",
                    "Hindari tag yang terlalu generik: best, epic, wow, omg, keren",
                ],
            },
            "thumbnail_hook": {
                "rules": [
                    "Teks singkat dan nendang — maksimal 5-6 kata",
                    "Pakai kata yang bikin penasaran atau menggambarkan momen ekstrem",
                    "Contoh: 'KABUR DARI 3 MUSUH', 'BOSS NYA GILA', 'NGGAK NYANGKA'",
                    "Hindari: 'LUAR BIASA', 'KEREN', 'WOW' — terlalu generik",
                ],
            },
        },
        "en": {
            "language": "en",
            "language_name": "English",
            "important": "All text must be in English.",
            "title": {
                "max_chars": 60,
                "rules": [
                    "Lead with the most exciting/surprising moment",
                    "Use active language: 'Escapes 3 enemies with ONE move', 'Why this boss fight broke me'",
                    "NO generic phrases: 'You need to see this', 'Stop and watch', 'This is unreal'",
                    "Include the game name naturally where it fits",
                    "Every title must be UNIQUE — no two clips share the same title",
                ],
                "filler_to_avoid": [
                    "You need to see this", "Stop and watch", "Wait for the best part",
                    "This is unreal", "A moment", "Watch this",
                ],
            },
            "description": {
                "max_chars": 200,
                "rules": [
                    "First sentence: hook describing what happens in the clip",
                    "Second sentence: context or why it's impressive",
                    "End with 3-5 relevant hashtags only (no filler like #Shorts #Gaming #Clips)",
                ],
            },
            "tags": {
                "count": "10-15 tags",
                "rules": [
                    "Mix specific and broad tags (gaming highlight, boss fight, action games)",
                    "Include difficulty-related tags (hard game, one life, no damage) where relevant",
                    "Avoid generic tags: best, epic, wow, omg",
                ],
            },
            "thumbnail_hook": {
                "rules": [
                    "Short punchy text — max 5-6 words",
                    "Make it intriguing or describe the extreme moment",
                    "Avoid: 'AMAZING', 'EPIC', 'WOW' — too generic",
                ],
            },
        },
    }

    if language in _RICH:
        return _RICH[language]

    # ── Generic fallback for any other language ───────────────────────────────
    # Claude has native knowledge of most languages and styles. We just tell it
    # the target language + universal rules; Claude handles tone and idioms.
    return {
        "language": language,
        "language_name": language,
        "important": (
            f"ALL text (title, description, tags, thumbnail hook) MUST be written in {language}. "
            "Use natural, conversational tone — like talking to a fellow gamer, not writing an article. "
            "You may keep widely-understood gaming terms in English (boss fight, no damage, combo, etc.) "
            "if they are commonly used that way in the target language community."
        ),
        "title": {
            "max_chars": 60,
            "rules": [
                "Lead with the most exciting or surprising moment in the clip",
                "Use active, energetic language",
                "Avoid generic filler phrases that don't describe the actual clip",
                "Include the game name naturally if it fits",
                "Every title must be UNIQUE across all clips",
            ],
        },
        "description": {
            "max_chars": 200,
            "rules": [
                "First sentence: describe what happens in the clip",
                "Second sentence: context or why it is impressive",
                "End with 3-5 relevant hashtags — avoid generic ones",
            ],
        },
        "tags": {
            "count": "10-15 tags",
            "rules": [
                "Mix game-specific and broader gaming tags",
                "Include difficulty tags where relevant (hard game, no damage, etc.)",
                "Avoid overly generic tags",
                "Mix local-language tags with common English gaming terms as appropriate",
            ],
        },
        "thumbnail_hook": {
            "rules": [
                "Short punchy text — max 5-6 words",
                "Describe the extreme or intriguing moment",
                "Avoid generic words like AMAZING, EPIC, WOW",
            ],
        },
    }
# Legacy global path — kept as fallback only; new code writes per-video.
_GLOBAL_ENRICHED_STATE_FILE = os.path.join(_PROJECT_ROOT, "output", "ai_enriched_clips.json")


def _video_dir_for_rows(rows: list[dict]) -> str | None:
    """Derive the video output directory from clip thumbnail paths.

    thumbnail_path is stored as e.g.
    'output/3e2e7da700671dba_NINJAGAIDEN-gameplay-part14/clips/shorts-3/thumbnail.jpg'
    We extract the second path component and return the full abs path.
    """
    import glob as _glob
    for r in rows:
        thumb = (r.get("thumbnail_path") or "").replace("\\", "/")
        if not thumb:
            # Fall back to video_id glob
            vid = r.get("video_id", "")
            if vid:
                matches = _glob.glob(os.path.join(_PROJECT_ROOT, "output", f"{vid}_*"))
                if matches:
                    return matches[0]
            continue
        parts = thumb.split("/")
        if len(parts) >= 2:
            candidate = os.path.join(_PROJECT_ROOT, parts[0], parts[1])
            if os.path.isdir(candidate):
                return candidate
    return None


def _enriched_state_file(video_dir: str | None) -> str:
    """Return the per-video state file path, falling back to the global one."""
    if video_dir and os.path.isdir(video_dir):
        return os.path.join(video_dir, "ai_enriched_clips.json")
    return _GLOBAL_ENRICHED_STATE_FILE


def _load_enriched_ids(state_file: str | None = None) -> set[str]:
    """Load set of clip_ids already enriched by Claude."""
    path = state_file or _GLOBAL_ENRICHED_STATE_FILE
    # Also check legacy global file and merge (handles migration)
    ids: set[str] = set()
    for f in {path, _GLOBAL_ENRICHED_STATE_FILE}:
        if os.path.isfile(f):
            try:
                ids.update(json.load(open(f)).get("enriched_ids", []))
            except Exception:
                pass
    return ids


def _save_enriched_ids(ids: set[str], state_file: str | None = None) -> None:
    path = state_file or _GLOBAL_ENRICHED_STATE_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"enriched_ids": sorted(ids)}, f, indent=2)


def _db_path(config: dict) -> str:
    path = config.get("paths", {}).get("database", "output/shorts_factory.db")
    return path if os.path.isabs(path) else os.path.join(_PROJECT_ROOT, path)


def _next_scheduled_at(occupied: set[str], config: dict) -> str:
    """Find next free upload slot using config's preferred hours and posts_per_day."""
    preferred_hours: list[int] = config.get("scheduler", {}).get("preferred_hours", [10])
    posts_per_day: int = int(config.get("scheduler", {}).get("posts_per_day", 1))
    min_gap_hours: int = int(config.get("scheduler", {}).get("min_gap_hours", 4))

    now = datetime.now(timezone.utc)
    # Start from tomorrow to avoid same-day conflicts
    candidate = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    slots_assigned_today: list[datetime] = []

    for _ in range(365):  # safety limit
        for hour in sorted(preferred_hours):
            slot = candidate.replace(hour=hour, minute=0, second=0, microsecond=0)
            slot_iso = slot.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Skip if already occupied
            if slot_iso in occupied:
                continue

            # Respect min_gap_hours between slots on the same day
            too_close = any(
                abs((slot - s).total_seconds()) < min_gap_hours * 3600
                for s in slots_assigned_today
            )
            if too_close:
                continue

            # Respect posts_per_day
            slots_today = sum(1 for s in occupied if s.startswith(slot.strftime("%Y-%m-%d")))
            if slots_today >= posts_per_day:
                break  # Move to next day

            return slot_iso

        candidate += timedelta(days=1)
        slots_assigned_today = []

    # Fallback: 30 days from now at first preferred hour
    fallback = now + timedelta(days=30)
    return fallback.replace(hour=preferred_hours[0], minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def cmd_export(adapter: DatabaseAdapter, config: dict | None = None) -> int:
    """Export clips that haven't been AI-enriched yet.

    The output JSON includes enrichment_guidelines so Claude follows the
    correct language and style during the 8am enrichment step — regardless
    of what is hardcoded in the SKILL.md.  Claude MUST use these guidelines
    instead of any default English instructions when this field is present.
    """
    # Check both queued and scheduled clips
    rows = adapter.get_clips_by_status(["queued", "scheduled"])

    # Resolve per-video state file
    video_dir = _video_dir_for_rows(rows)
    state_file = _enriched_state_file(video_dir)
    already_enriched = _load_enriched_ids(state_file)

    # Filter out clips already processed by Claude
    to_enrich = [r for r in rows if r["clip_id"] not in already_enriched]

    if not to_enrich:
        print(json.dumps({"status": "nothing_to_enrich", "clips": []}))
        return 0

    # Read language from config (default: "en")
    language = "en"
    if config:
        language = config.get("metadata", {}).get("language", "en")

    clips = []
    for r in to_enrich:
        clips.append({
            "clip_id": r["clip_id"],
            "video_id": r["video_id"],
            "duration": r.get("duration"),
            "composite_score": r.get("composite_score"),
            "title": r.get("title", ""),
            "description": r.get("description", ""),
            "tags": json.loads(r["tags"]) if isinstance(r.get("tags"), str) and r["tags"] else r.get("tags") or [],
        })

    print(json.dumps({
        "status": "ok",
        "count": len(clips),
        "already_enriched": len(already_enriched),
        "enrichment_guidelines": _enrichment_guidelines(language),
        "clips": clips,
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_apply(adapter: DatabaseAdapter, config: dict, enriched_path: str) -> int:
    """Apply enriched metadata from a JSON file back to the DB and assign scheduled_at."""
    if not os.path.isfile(enriched_path):
        logger.error(f"Enriched file not found: {enriched_path}")
        return 1

    with open(enriched_path) as f:
        data = json.load(f)

    clips = data.get("clips", [])
    if not clips:
        logger.warning("No clips in enriched JSON — nothing to apply.")
        return 0

    # Collect already-occupied scheduled slots
    all_scheduled = adapter.get_clips_by_status(["scheduled", "published"])
    occupied: set[str] = {
        r["scheduled_at"] for r in all_scheduled if r.get("scheduled_at")
    }

    applied = 0
    for clip in clips:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue

        title = clip.get("title", "")
        description = clip.get("description", "")
        tags = clip.get("tags", [])
        tags_json = json.dumps(tags)

        # Assign next available upload slot
        scheduled_at = _next_scheduled_at(occupied, config)
        occupied.add(scheduled_at)

        adapter.connection.execute(
            """UPDATE clips
               SET title=?, description=?, tags=?, scheduled_at=?,
                   status='scheduled', updated_at=CURRENT_TIMESTAMP
               WHERE clip_id=?""",
            (title, description, tags_json, scheduled_at, clip_id),
        )

        applied += 1
        logger.info(
            "Enriched clip applied",
            extra={
                "clip_id": clip_id,
                "scheduled_at": scheduled_at,
                "stage": "ai_enricher",
            },
        )

    adapter.connection.commit()

    # Persist the enriched IDs into the per-video folder
    rows_for_dir = adapter.get_clips_by_status(["scheduled", "published"])
    video_dir = _video_dir_for_rows(rows_for_dir)
    state_file = _enriched_state_file(video_dir)
    existing = _load_enriched_ids(state_file)
    existing.update(c["clip_id"] for c in clips if c.get("clip_id"))
    _save_enriched_ids(existing, state_file)

    print(
        json.dumps(
            {
                "status": "ok",
                "applied": applied,
                "message": f"{applied} clip(s) enriched and scheduled.",
            }
        )
    )
    return 0


def cmd_status(adapter: DatabaseAdapter) -> int:
    """Print a summary of clip statuses."""
    for status in ["queued", "scheduled", "published", "failed"]:
        count = len(adapter.get_clips_by_status([status]))
        print(f"{status:12}: {count}")
    return 0


def main() -> int:
    os.chdir(_PROJECT_ROOT)
    args = sys.argv[1:]

    try:
        config = load_config()
    except Exception as exc:
        print(f"[ai_enricher] FATAL: config load failed: {exc}", file=sys.stderr)
        return 1

    configure_logging(
        level=config.get("logging", {}).get("level", "INFO"),
        log_file=config.get("logging", {}).get("log_file"),
    )

    try:
        conn = initialize_database(_db_path(config))
        adapter = DatabaseAdapter(conn)
    except Exception as exc:
        logger.error(f"DB error: {exc}")
        return 1

    if not args or args[0] == "--status":
        return cmd_status(adapter)
    elif args[0] == "--export":
        return cmd_export(adapter, config)
    elif args[0] == "--apply" and len(args) >= 2:
        return cmd_apply(adapter, config, args[1])
    else:
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
