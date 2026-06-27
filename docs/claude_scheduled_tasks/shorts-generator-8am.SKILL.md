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
