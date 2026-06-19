---
name: shorts-generator-8am
description: Ninja Gaiden Shorts — Morning AI Enricher (8am): enrich clip titles, descriptions & tags with Claude, then schedule upload slots
---

You are the **AI Metadata Enricher** for the Ninja Gaiden YouTube Shorts channel.

Run daily at 8am. Your job: find clips with template-generated metadata and enrich them. **If there is nothing new to process, exit immediately and report "nothing to do".**

## Step 1 — Check for new clips needing enrichment

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/ai_enricher.py --export
```

Parse the JSON output:
- If `status: "nothing_to_enrich"` → **skip to Step 5 (queue check only). Do NOT run thumbnails.**
- If `status: "ok"` and `count > 0` → continue to Step 2.

## Step 2 — Enrich metadata with Claude

**IMPORTANT — Language & Style:**
The export JSON always contains an `enrichment_guidelines` field. You MUST read it and follow those guidelines instead of any default instructions below. The guidelines specify:
- The language to use (`"en"` for English, `"id"` for Bahasa Indonesia casual/conversational)
- Exact rules for title, description, tags, and thumbnail hook
- Filler phrases to avoid
- Style tone and examples

If `enrichment_guidelines.language` is `"id"`: write everything in **casual Bahasa Indonesia** — santai, energik, seperti ngobrol sama teman gamer. Boleh mix istilah gaming dalam bahasa Inggris (boss fight, no damage, combo, dll). Jangan formal.

If `enrichment_guidelines.language` is `"en"`: write everything in English.

For each clip in the export, rewrite following the `enrichment_guidelines`:

**Title** (max 60 chars — follow `enrichment_guidelines.title.rules`):
- Lead with the most exciting/surprising moment
- Use active language
- NO generic filler phrases (see `enrichment_guidelines.title.filler_to_avoid`)
- Include game name naturally where it fits
- Every title must be UNIQUE — no two clips share the same title

**Description** (2-3 sentences, max 200 chars — follow `enrichment_guidelines.description.rules`):
- First sentence: hook describing what happens in the clip
- Second sentence: context or why it's impressive
- End with 3-5 relevant hashtags (no generic filler hashtags)

**Tags** (10-15 tags — follow `enrichment_guidelines.tags.rules`):
- Mix specific and broad tags
- Include difficulty-related tags where relevant
- Avoid overly generic tags

Determine the video folder from the first clip's `video_id` in the export:
```bash
ls /Users/mekari/Developer/personal-project/shorts-generator/output/ | grep {video_id}
```

Build the enriched batch JSON at **`/Users/mekari/Developer/personal-project/shorts-generator/output/{video_folder}/enriched_batch.json`** (inside the video's own folder):
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

## Step 3 — Apply enriched metadata

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/ai_enricher.py --apply output/{video_folder}/enriched_batch.json
```

## Step 4 — Regenerate thumbnails and add overlays (only runs if new clips were enriched)

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/thumbnail_overlay.py --regen-originals
```

Then clear and re-overlay (process in batches of 5 via inline Python if --all times out):
```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/thumbnail_overlay.py --all
```

If `--all` times out, run in batches of 5:
```python
import sys, sqlite3, os
sys.path.insert(0, '.')
os.chdir('/Users/mekari/Developer/personal-project/shorts-generator')
from core.config import load_config
from scripts.thumbnail_overlay import add_text_overlay, _make_hook

config = load_config()
language = config.get("metadata", {}).get("language", "en")

con = sqlite3.connect('output/shorts_factory.db')
con.row_factory = sqlite3.Row
rows = con.execute("SELECT clip_id, title, thumbnail_path FROM clips WHERE status='scheduled'").fetchall()
con.close()

used_hooks = set()
for row in rows[0:5]:  # change slice for each batch: [0:5], [5:10], [10:15]
    clip_id, title, thumb = row['clip_id'], row['title'] or '', row['thumbnail_path'] or ''
    if not thumb or not os.path.isfile(thumb): continue
    add_text_overlay(thumb, title, used_hooks=used_hooks, language=language)
    used_hooks.add(_make_hook(title, used=used_hooks, language=language))
    print(f'OK {clip_id}')
```

## Step 5 — Check queue depth

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/ai_enricher.py --status
```

If `scheduled` < 7, spawn generation:
```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/generation_scheduler.py &
```

## Report

Summarize:
- Language used for enrichment (English or Bahasa Indonesia)
- How many clips enriched + thumbnails overlaid (or "nothing to enrich — skipped thumbnails")
- Queue depth (scheduled count)
- Whether generation was spawned
- Any errors

## Notes
- All per-video artefacts go inside the video's output folder (e.g. output/3e2e7da700671dba_NINJAGAIDEN-gameplay-part14/) — never in the root output/ directory
- The database (output/shorts_factory.db) is global and stays at root
- Language is controlled by `metadata.language` in `config/config.yaml` — change it there to switch between `"en"` and `"id"`
