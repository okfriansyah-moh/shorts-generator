# Claude Scheduled Tasks For Current Codebase

This file is the replacement runbook for the live Claude Scheduled tasks:

- `8 AM`: `/Users/mekari/Claude/Scheduled/shorts-generator-8am/SKILL.md`
- `8 PM`: `/Users/mekari/Claude/Scheduled/shorts-generator-8pm/SKILL.md`

It is written to match the current codebase precisely as of `2026-06-27`.

Canonical standalone source files now also live here:

- [docs/claude_scheduled_tasks/shorts-generator-8am.SKILL.md](/Users/mekari/Developer/personal-project/shorts-generator/docs/claude_scheduled_tasks/shorts-generator-8am.SKILL.md)
- [docs/claude_scheduled_tasks/shorts-generator-8pm.SKILL.md](/Users/mekari/Developer/personal-project/shorts-generator/docs/claude_scheduled_tasks/shorts-generator-8pm.SKILL.md)

## Important Operational Note

`upload_scheduler.py` auto-starts generation when the scheduled upload queue is empty.

This behavior lives in:

- [scripts/upload_scheduler.py](/Users/mekari/Developer/personal-project/shorts-generator/scripts/upload_scheduler.py:222)
- [scripts/upload_scheduler.py](/Users/mekari/Developer/personal-project/shorts-generator/scripts/upload_scheduler.py:309)
- [scripts/upload_scheduler.py](/Users/mekari/Developer/personal-project/shorts-generator/scripts/upload_scheduler.py:392)

That means the auto-start is **not** part of the Claude `8am` task and **not** part of the Claude `8pm` task. It happens inside the local upload cron flow whenever `scripts/upload_scheduler.py --account mrkimbum12` runs and finds the queue exhausted.

If you keep:

- local cron for `upload_scheduler.py`
- Claude `8am` enrichment
- Claude `8pm` generation

then the system still works, but the `8pm` task overlaps with the cron-triggered generation path. The lock in `generation_scheduler.py` prevents same-account double-processing, but the overlap is still operationally redundant.

## Replacement: 8 AM Task

Copy this into `/Users/mekari/Claude/Scheduled/shorts-generator-8am/SKILL.md` if you want the live task updated.

```md
---
name: shorts-generator-8am
description: mrkimbum12 — Morning AI Enricher (8am): enrich generated clips, apply metadata, regenerate thumbnail overlays, and report queue status
---

You are the **AI Metadata Enricher** for the `mrkimbum12` account.

Project path: `/Users/mekari/Developer/personal-project/shorts-generator`
Account: `mrkimbum12`
Account output root: `output/mrkimbum12/`
Database: `output/shorts_factory.db`

Run daily at 8am.

Your job:
1. Export clips with status `generated` that still need enrichment.
2. Rewrite their metadata in Bahasa Indonesia casual/conversational style.
3. Save `enriched_batch.json` into the correct per-video output folder.
4. Apply that batch with `scripts/ai_enricher.py --apply`.
5. Regenerate thumbnail overlays for this account.
6. Report the resulting queue status.

If there is nothing new to process, exit quickly and report `nothing to do`.

## Step 1 — Export clips needing enrichment

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/ai_enricher.py --account mrkimbum12 --export
```

Interpret the JSON output:

- If `status` is `nothing_to_enrich`:
  - do not create any batch file
  - do not run thumbnail regeneration
  - skip directly to the final queue-status check and report `nothing to do`
- If `status` is `ok` and `count > 0`:
  - continue

## Step 2 — Build enriched metadata

For every exported clip, write:

- `title`: max 60 chars
- `description`: max 200 chars
- `tags`: 10–15 items

Language and tone:

- Bahasa Indonesia casual/conversational
- sound like talking to a gamer friend
- English gaming terms are allowed when natural: `boss fight`, `combo`, `no damage`, `one life`
- avoid generic filler like:
  - `Kamu harus lihat ini`
  - `Tunggu yang ini`
  - `Nggak nyangka`
  - `WOW`
  - `epic`

Title rules:

- lead with the most exciting or surprising moment
- keep it active and direct
- make every title unique
- include the game name only if it feels natural

Description rules:

- sentence 1: say what happens
- sentence 2: add context, stakes, or why it matters
- finish with only relevant hashtags
- do not append generic filler hashtags like `#Shorts #Gaming #Clips`

Tags rules:

- mix specific and broad terms
- include game name, characters, boss names, mechanics, or difficulty terms when relevant
- avoid meaningless filler tags

## Step 3 — Find the target video folder

Use the first clip's `video_id` from the export and locate the matching account-scoped output folder:

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
find output/mrkimbum12 -maxdepth 1 -type d -name "{video_id}_*"
```

There should be one matching folder:

`output/mrkimbum12/{video_id}_{video_name}/`

Write the enrichment batch file there as:

`output/mrkimbum12/{video_id}_{video_name}/enriched_batch.json`

File format:

```json
{
  "clips": [
    {
      "clip_id": "...",
      "title": "...",
      "description": "...",
      "tags": ["...", "..."]
    }
  ]
}
```

## Step 4 — Apply the batch

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/ai_enricher.py --account mrkimbum12 --apply output/mrkimbum12/{video_id}_{video_name}/enriched_batch.json
```

This is the supported path. Do not update the DB manually with ad-hoc SQL.

## Step 5 — Regenerate thumbnail overlays

Run this only if Step 4 actually applied new enrichment:

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/thumbnail_overlay.py --account mrkimbum12 --regen-originals
python3 scripts/thumbnail_overlay.py --account mrkimbum12 --all
```

If thumbnail regeneration fails or times out, report the failure clearly instead of silently skipping it.

## Step 6 — Check queue status

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/ai_enricher.py --account mrkimbum12 --status
```

Report the counts you see for:

- `generated`
- `scheduled`
- `published`
- `failed`

Do not spawn generation from this 8am task just because the queue is low.
Generation auto-start belongs to `upload_scheduler.py` in the cron-driven upload path.

## Report

Summarize:

- how many clips were enriched
- which `video_id` / output folder was processed
- whether thumbnail regeneration ran successfully
- the final queue status counts
- any errors

## Notes

- All per-video artifacts belong under `output/mrkimbum12/{video_id}_{video_name}/`
- The DB remains global at `output/shorts_factory.db`
- Use account-scoped commands with `--account mrkimbum12`
- If there is nothing to enrich, report `nothing to do`
```

## Replacement: 8 PM Task

Copy this into `/Users/mekari/Claude/Scheduled/shorts-generator-8pm/SKILL.md` if you want the live task updated.

```md
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
```
