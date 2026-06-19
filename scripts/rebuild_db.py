#!/usr/bin/env python3
"""Rebuild shorts_factory.db from on-disk clip metadata.

Run this on your Mac when the DB gets corrupted:
  cd /Users/mekari/Developer/personal-project/shorts-generator
  python3 scripts/rebuild_db.py

It will:
  1. Delete the corrupted DB and WAL files
  2. Recreate the DB using database migrations
  3. Repopulate from metadata.json files in each clips/shorts-N directory
  4. Assign upload slots based on config (preferred_hours, posts_per_day)
"""

from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.config import load_config
from database.connection import initialize_database


def next_slot(used_slots: set[str], config: dict, start_date: datetime) -> str:
    preferred_hours: list[int] = config.get("scheduler", {}).get("preferred_hours", [10])
    posts_per_day: int = int(config.get("scheduler", {}).get("posts_per_day", 1))
    d = start_date
    for _ in range(365):
        for h in sorted(preferred_hours):
            s = d.replace(hour=h, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
            if s not in used_slots:
                day = d.strftime("%Y-%m-%d")
                if sum(1 for u in used_slots if u.startswith(day)) < posts_per_day:
                    return s
        d += timedelta(days=1)
    raise RuntimeError("Could not find a free upload slot in 365 days")


def main() -> None:
    os.chdir(_PROJECT_ROOT)
    config = load_config()

    db_path = config.get("paths", {}).get("database", "output/shorts_factory.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(_PROJECT_ROOT, db_path)

    # Remove corrupted files
    for suffix in ["", "-journal", "-shm", "-wal", ".bak", ".bak-wal"]:
        p = db_path + suffix
        if os.path.isfile(p):
            os.remove(p)
            print(f"Removed: {p}")

    # Recreate via migrations
    conn = initialize_database(db_path)
    print(f"DB created: {db_path}")

    output_dir = config.get("paths", {}).get("output_dir", "output")
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(_PROJECT_ROOT, output_dir)

    # Find all video directories
    video_dirs = sorted(glob.glob(os.path.join(output_dir, "*_*")))
    used_slots: set[str] = set()
    start_date = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)

    total = 0
    for video_dir in video_dirs:
        video_dir_name = os.path.basename(video_dir)
        video_id = video_dir_name.split("_")[0]

        conn.execute(
            """INSERT OR IGNORE INTO videos
               (video_id, file_path, duration_seconds, resolution_width,
                resolution_height, file_size_bytes, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (video_id, video_dir, 0.0, 1080, 1920, 0, "processed"),
        )
        conn.commit()  # commit video before inserting clips (foreign key constraint)

        clips_dir = os.path.join(video_dir, "clips")
        if not os.path.isdir(clips_dir):
            continue

        # Sort shorts-N directories numerically
        short_dirs = sorted(
            glob.glob(os.path.join(clips_dir, "shorts-*")),
            key=lambda p: int(os.path.basename(p).split("-")[1]),
        )

        for idx, short_dir in enumerate(short_dirs):
            meta_path = os.path.join(short_dir, "metadata.json")
            video_path = os.path.join(short_dir, "final.mp4")
            thumb_path = os.path.join(short_dir, "thumbnail.jpg")

            if not os.path.isfile(meta_path):
                print(f"  SKIP {short_dir} — no metadata.json")
                continue
            if not os.path.isfile(video_path):
                print(f"  SKIP {short_dir} — no final.mp4")
                continue

            with open(meta_path) as f:
                m = json.load(f)

            clip_id = m["clip_id"]
            tags_json = json.dumps(m.get("tags", []))
            slot = next_slot(used_slots, config, start_date)
            used_slots.add(slot)

            conn.execute(
                """INSERT OR IGNORE INTO clips
                   (clip_id, video_id, start_time, end_time, duration, composite_score,
                    title, description, tags, category, video_path, thumbnail_path,
                    status, scheduled_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, CURRENT_TIMESTAMP)""",
                (
                    clip_id, video_id,
                    idx * 50.0, (idx + 1) * 50.0, 50.0, 0.9,
                    m.get("title", ""), m.get("description", ""),
                    tags_json, m.get("category", "Gaming"),
                    video_path, thumb_path, slot,
                ),
            )
            print(f"  {os.path.basename(short_dir)}: {clip_id} -> {slot}")
            total += 1

    conn.commit()
    conn.close()
    print(f"\nRebuilt {total} clip(s) into {db_path}")


if __name__ == "__main__":
    main()
