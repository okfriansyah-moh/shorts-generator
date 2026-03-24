---
name: determinism
description: "Determinism enforcement for Shorts Factory. Use when implementing or reviewing code to ensure same input + same config = identical output. Detects randomness, non-deterministic patterns, and sorting violations."
---

# Determinism Enforcement Skill

## When to Use

- Implementing any module logic
- Reviewing code for non-deterministic patterns
- Validating ID generation is content-addressable
- Checking sorting stability and tiebreaker rules

## Core Invariant

> Same input video + same `config.yaml` = identical output. Always. On every machine, on every run.

## Forbidden Patterns

### 1. Random Number Generation

```python
# ❌ FORBIDDEN
import random
random.choice(templates)
random.shuffle(scenes)
uuid.uuid4()  # Non-deterministic UUID

# ✅ CORRECT
templates[clip_index % len(templates)]  # Deterministic rotation
sorted(scenes, key=lambda s: s.start_time)  # Deterministic order
```

### 2. Time-Dependent Logic

```python
# ❌ FORBIDDEN — time as decision input
if datetime.now().hour < 12:
    template = morning_template

# ✅ CORRECT — time only for logging/timestamps
logging.info("Stage started", extra={"timestamp": datetime.now().isoformat()})
```

### 3. Non-Deterministic Iteration

```python
# ❌ FORBIDDEN — dict ordering (pre-3.7) or set iteration
for key in set(items):  # Set order is non-deterministic
    process(key)

# ✅ CORRECT — explicit sorting
for key in sorted(items):
    process(key)
```

### 4. Network-Dependent Decisions

```python
# ❌ FORBIDDEN — processing depends on network state
response = requests.get(api_url)
if response.ok:
    use_enhanced_mode()

# ✅ CORRECT — offline-first, network only for publishing
# All processing is local. Network only used in publisher module.
```

### 5. Float Comparison Without Tolerance

```python
# ❌ RISKY — floating point comparison
if score == 0.7:
    select_clip()

# ✅ CORRECT — use tolerance or integer comparison
if abs(score - 0.7) < 1e-9:
    select_clip()
```

## ID Generation Rules

All IDs must be **content-addressable** — derived from content, not from timestamps or random values.

| ID         | Formula                                               | Deterministic?                    |
| ---------- | ----------------------------------------------------- | --------------------------------- |
| `video_id` | `SHA256(first_10MB + str(file_size))[:16]`            | ✅ Same file = same ID            |
| `scene_id` | `{video_id}_{start_ms}_{end_ms}`                      | ✅ Same video + same boundaries   |
| `clip_id`  | `SHA256(video_id + str(start_ms) + str(end_ms))[:16]` | ✅ Same video + same time range   |
| `run_id`   | UUID (logging only)                                   | ⚠️ Allowed — not a decision input |

## Sorting Rules

All sorted collections must have **deterministic tiebreakers**:

```python
# ❌ INSUFFICIENT — ties are arbitrary
scenes.sort(key=lambda s: s.composite_score, reverse=True)

# ✅ CORRECT — tiebreaker ensures deterministic order
scenes.sort(key=lambda s: (-s.composite_score, s.start_time))
```

## Template Selection

Hook templates must use **deterministic rotation**, not random selection:

```python
# ❌ FORBIDDEN
template = random.choice(TEMPLATES)

# ✅ CORRECT — index-based rotation
template = TEMPLATES[clip_index % len(TEMPLATES)]
```

## TTS Caching

Edge TTS has minor synthesis variance across runs. To ensure determinism:

```python
# Cache by text hash
cache_key = hashlib.sha256(text.encode()).hexdigest()[:16]
cache_path = f"output/{video_id}/tts_cache/{cache_key}.mp3"
if os.path.exists(cache_path):
    return cache_path  # Deterministic on subsequent runs
```

## Checklist

Before committing any module code, verify:

- [ ] No `import random` or `random.` calls
- [ ] No `uuid.uuid4()` for IDs (only for `run_id` logging)
- [ ] No `datetime.now()` as logic input (only logging timestamps)
- [ ] No `set()` iteration without sorting
- [ ] All sorts have deterministic tiebreakers
- [ ] All IDs are SHA-256 content-addressable
- [ ] Template selection is index-based, not random
