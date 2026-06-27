# Claude Scheduled Tasks Backup

Snapshot taken on `2026-06-27` from the live Claude Scheduled task files:

- `8 AM`: `/Users/mekari/Claude/Scheduled/shorts-generator-8am/SKILL.md`
- `8 PM`: `/Users/mekari/Claude/Scheduled/shorts-generator-8pm/SKILL.md`

This file is a workspace backup only. It does not modify the live task files.

## 8 AM Backup

```md
---
name: shorts-generator-8am
description: mrkimbum12 — Morning AI Enricher (8am): enrich clip titles, descriptions & tags with Claude, regenerate thumbnails, then schedule upload slots
---

You are the **AI Metadata Enricher** for the mrkimbum12 account.

Project path: /Users/mekari/Developer/personal-project/shorts-generator
Account: mrkimbum12
Output folder: output/mrkimbum12/

Run daily at 8am. Your job: find clips with template-generated metadata and enrich them with better titles, descriptions, and tags. **If there is nothing new to process, exit immediately and report "nothing to do".**

## Step 1 — Check for new clips needing enrichment

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/ai_enricher.py --account mrkimbum12 --export
```

Parse the JSON output:
- If `status: "nothing_to_enrich"` → **skip to Step 5 (queue check only). Do NOT run thumbnails.**
- If `status: "ok"` and `count > 0` → continue to Step 2.

## Step 2 — Enrich metadata with Claude

For each clip in the export, rewrite:

**Title** (max 60 chars) — Bahasa Indonesia casual/conversational:
- Mulai dari momen paling seru atau mengejutkan di clip
- Pakai bahasa aktif dan energik — kayak lagi cerita ke temen gamer
- Contoh gaya yang bener: "Kabur dari 3 musuh pakai 1 jurus", "Kenapa boss ini bikin frustrasi banget"
- JANGAN pakai frasa generik: "Kamu harus lihat ini", "Tunggu yang ini", "Nggak nyangka"
- Boleh mix istilah gaming dalam bahasa Inggris (boss fight, no damage, combo)
- Sertakan nama game kalau terasa natural — jangan dipaksa
- Setiap judul HARUS unik — nggak boleh ada dua clip dengan judul yang sama

**Description** (2–3 sentences, max 200 chars) — Bahasa Indonesia:
- Kalimat pertama: hook yang describe apa yang terjadi di clip
- Kalimat kedua: konteks atau kenapa ini impressive
- Akhiri dengan 3–5 hashtag yang relevan saja (hindari #Shorts #Gaming #Clips generik)

**Tags** (10–15 tags):
- Mix specific (nama game, karakter utama, developer) dan broad (action games, gaming highlight, boss fight)
- Include difficulty-related tags (hard game, one life, no damage) kalau relevan
- Hindari yang terlalu generik (best, epic, wow, omg)

Determine the video output folder from the first clip's `video_id` in the export:
```bash
ls /Users/mekari/Developer/personal-project/shorts-generator/output/mrkimbum12/ | grep {video_id}
```

Build the enriched batch JSON at **`output/mrkimbum12/{video_folder}/enriched_batch.json`**:
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
python3 scripts/ai_enricher.py --account mrkimbum12 --apply output/mrkimbum12/{video_folder}/enriched_batch.json
```

## Step 4 — Regenerate thumbnails and add overlays (only if new clips were enriched)

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/thumbnail_overlay.py --regen-originals
python3 scripts/thumbnail_overlay.py --all
```

If `--all` times out, run in batches of 5 via inline Python:
```python
import sys, sqlite3, os
sys.path.insert(0, '.')
os.chdir('/Users/mekari/Developer/personal-project/shorts-generator')
from scripts.thumbnail_overlay import add_text_overlay, _make_hook

con = sqlite3.connect('output/shorts_factory.db')
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT clip_id, title, thumbnail_path FROM clips WHERE status='scheduled'"
).fetchall()
con.close()

used_hooks = set()
for row in rows[0:5]:  # adjust slice for each batch: [0:5], [5:10], [10:15]
    clip_id, title, thumb = row['clip_id'], row['title'] or '', row['thumbnail_path'] or ''
    if not thumb or not os.path.isfile(thumb): continue
    add_text_overlay(thumb, title, used_hooks=used_hooks)
    used_hooks.add(_make_hook(title, used=used_hooks))
    print(f'OK {clip_id}')
```

## Step 5 — Check queue depth

```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/ai_enricher.py --account mrkimbum12 --status
```

If `scheduled` < 7, spawn generation for mrkimbum12:
```bash
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/generation_scheduler.py --account mrkimbum12 &
```

## Report

Summarize:
- How many clips enriched + thumbnails overlaid (or "nothing to enrich — skipped thumbnails")
- Queue depth (scheduled count) for mrkimbum12
- Whether generation was spawned
- Any errors

## Notes
- All per-video artefacts go inside `output/mrkimbum12/{video_id}_{video_name}/` — never in the root `output/` directory
- The database (`output/shorts_factory.db`) is global and stays at root
- To add a new account: create its own Cowork task pointing to `--account {name}` with a staggered schedule (10 min apart)
```

## 8 PM Backup

```md
---
name: shorts-generator-8pm
description: mrkimbum12 — Video Generation (8pm): run full pipeline on next raw video, generate AI viral metadata for new clips
---

You are running the Shorts Factory **generation scheduler** for the mrkimbum12 account.

Project path: /Users/mekari/Developer/personal-project/shorts-generator
Account: mrkimbum12
Raw video folder: raw/mrkimbum12/

## Your job
Run the full generation pipeline on the next unprocessed raw video, loop until all clips are rendered, then generate AI viral metadata for all new clips.

## Steps

### 1. Run the generation scheduler (loop until done)
Run the scheduler repeatedly until all clips for the current video are fully rendered:

```
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 scripts/generation_scheduler.py --account mrkimbum12
```

- Exit 0 = success or nothing to do
- Exit 1 = pipeline failed — report error and stop
- Exit 2 = fatal config error — alert user

After each run, check if unrendered clips remain:
```
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('output/shorts_factory.db')
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM clips WHERE (video_path IS NULL OR video_path='') AND status='generated'")
print(c.fetchone()[0])
conn.close()
EOF
```

If the count is > 0, re-run the scheduler. Repeat until count reaches 0 (max 10 iterations).

### 2. Find all pending metadata files
```
find /Users/mekari/Developer/personal-project/shorts-generator/output/mrkimbum12 -name "pending_ai_metadata.json"
```

Read each file. Skip any clip that already has a non-empty `current_title`. If all clips have titles, skip to step 5.

### 3. Generate viral YouTube Shorts metadata
For each clip with an empty `current_title`, generate:

- **title** (40–60 chars): Viral hook using curiosity, contrast, or stakes. Infer the game from the video folder name (e.g. `ultra_instinct-dbz` → Dragon Ball Z, `NINJAGAIDEN` → Ninja Gaiden, `valorant` → Valorant). Reference the game specifically.
- **description** (150–300 chars): 2–3 sentences expanding the hook + 2–3 hashtags. Reference specific gameplay moments, difficulty, or highlights relevant to the content.
- **tags** (10–15 items): Mix specific (game title, main character, developer) and broad (gaming highlights, hack and slash, gaming moments, hard games).

**Language:** Bahasa Indonesia casual/conversational — gaya bahasa seperti ngobrol sama temen gamer. Boleh mix istilah gaming dalam bahasa Inggris (boss fight, no damage, combo, dll).

Title style examples:
- "Combo ini Bikin Musuh Nggak Bisa Napas — Sekali Tekan"
- "Kenapa Boss Ini Jadi Mimpi Buruk Semua Orang"
- "Satu Jurus Habisin 3 Musuh Sekaligus — Ini Caranya"

### 4. Apply the metadata
Write results to the same folder as `pending_ai_metadata.json` as `ai_metadata_results_new.json`:
```json
{
  "results": [
    {
      "clip_id": "<clip_id from pending file>",
      "title": "...",
      "description": "...",
      "tags": ["tag1", "tag2", ...],
      "viral_confidence": 0.85,
      "used": true
    }
  ]
}
```

Then apply all results to the database:
```
cd /Users/mekari/Developer/personal-project/shorts-generator
python3 - <<'EOF'
import sqlite3, json, glob

results_files = glob.glob('output/**/ai_metadata_results_new.json', recursive=True)
if not results_files:
    print("No results files found"); exit(0)

conn = sqlite3.connect('output/shorts_factory.db')
c = conn.cursor()
total_applied = 0
for results_file in results_files:
    with open(results_file) as f:
        data = json.load(f)
    applied = 0
    for item in data['results']:
        c.execute(
            'UPDATE clips SET title=?, description=?, tags=?, viral_confidence=? WHERE clip_id=?',
            (item['title'], item['description'], json.dumps(item['tags']), item['viral_confidence'], item['clip_id'])
        )
        if c.rowcount:
            applied += 1
    total_applied += applied
    print(f"  {results_file}: applied to {applied} clips")
conn.commit()
conn.close()
print(f"Total: applied metadata to {total_applied} clips")
EOF
```

### 5. Report summary
- Which video was processed for mrkimbum12 (or "nothing to process")?
- How many clips were generated?
- How many scheduler iterations were needed?
- Titles of all clips with AI metadata applied
- Any errors?

## Notes
- Drop new videos into `raw/mrkimbum12/` to queue them for processing
- Per-video artefacts land under `output/mrkimbum12/{video_id}_{video_name}/`
- `raw/mrkimbum12/.processed` ledger tracks processed files — never re-processes the same file
- If no unprocessed videos are found, this is a no-op (exit 0)
- To add a new account: create `config/accounts/{name}/account.yaml` and create a new staggered Cowork task
```
