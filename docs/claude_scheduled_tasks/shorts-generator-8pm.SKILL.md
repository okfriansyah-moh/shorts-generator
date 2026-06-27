---
name: shorts-generator-8pm
description: mrkimbum12 — Video Generation (8pm): run generation on the next raw video, verify all clips render, and hand off pending metadata for the 8am enricher
---

You are running the Shorts Factory **generation scheduler** for the `mrkimbum12` account.

Project path: `/Users/mekari/Developer/personal-project/shorts-generator`
Account: `mrkimbum12`
Raw video folder: `raw/mrkimbum12/`
Account output root: `output/mrkimbum12/`
Database: `output/shorts_factory.db`

Run daily at 8pm.

Your job:
1. Run the generation scheduler for the next unprocessed raw video.
2. Let it complete the full generation lifecycle for that video.
3. Verify that all clips for that video have rendered media files.
4. Confirm that `pending_ai_metadata.json` exists for that video so the 8am enricher can process it.
5. Report the result.

## Step 1 — Run the generation scheduler

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/generation_scheduler.py --account mrkimbum12
```

Interpret the exit code:

- `0` = success, or nothing to do
- `1` = pipeline/render failure, report and stop
- `2` = fatal config/account error, report and stop

Important:

- The current `generation_scheduler.py` already runs synchronously.
- It already loops through render batches internally until that video's remaining clips are rendered.
- Do not use the old outer manual loop unless the process was interrupted or clearly exited early.

## Step 2 — Determine whether a video was processed

If the run reports that there was no unprocessed video in `raw/mrkimbum12/`, report `nothing to process` and stop.

If a video was processed, identify its `video_id` and output folder from the scheduler output or by checking the newest account-scoped output directory under:

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
ls -td output/mrkimbum12/*/ | head
```

## Step 3 — Verify all clips are rendered for that video

Check the clip state for the processed `video_id`:

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 - <<'PY'
import sqlite3

video_id = "REPLACE_WITH_VIDEO_ID"
conn = sqlite3.connect("output/shorts_factory.db")
cur = conn.cursor()
cur.execute(
    "SELECT status, COUNT(*) FROM clips WHERE video_id=? GROUP BY status ORDER BY status",
    (video_id,),
)
print("status_counts", cur.fetchall())
cur.execute(
    "SELECT COUNT(*) FROM clips WHERE video_id=? AND (video_path IS NULL OR trim(video_path)='')",
    (video_id,),
)
print("missing_video_path", cur.fetchone()[0])
cur.execute(
    "SELECT COUNT(*) FROM clips WHERE video_id=? AND (thumbnail_path IS NULL OR trim(thumbnail_path)='')",
    (video_id,),
)
print("missing_thumbnail_path", cur.fetchone()[0])
conn.close()
PY
```

Success condition for this task:

- `missing_video_path = 0`
- `missing_thumbnail_path = 0`

If media is still missing because the process was interrupted, rerun:

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/generation_scheduler.py --account mrkimbum12
```

Then verify again.

## Step 4 — Verify pending metadata handoff exists

The generation task should leave:

`output/mrkimbum12/{video_id}_{video_name}/pending_ai_metadata.json`

Check it exists:

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
find output/mrkimbum12 -maxdepth 2 -name "pending_ai_metadata.json"
```

If the processed video has a matching `pending_ai_metadata.json`, that is the correct handoff for the 8am enrichment task.

Do not manually apply metadata here with ad-hoc SQL.
Do not use the legacy root-level `output/pending_ai_metadata.json` flow.
Do not write `ai_metadata_results_new.json` from this 8pm task.

## Report

Summarize:

- which video was processed, or `nothing to process`
- the `video_id`
- total clip count for that video
- final clip status counts
- whether all clips have `video_path`
- whether all clips have `thumbnail_path`
- whether `pending_ai_metadata.json` exists for that video
- any errors

## Notes

- New raw videos are discovered from `raw/mrkimbum12/`
- Processed filenames are tracked in `raw/mrkimbum12/.processed`
- Per-video artifacts live under `output/mrkimbum12/{video_id}_{video_name}/`
- This task overlaps with the cron-driven auto-generation path in `upload_scheduler.py`, so the lock in `generation_scheduler.py` may cause the task to no-op if another generation run already holds the lease
