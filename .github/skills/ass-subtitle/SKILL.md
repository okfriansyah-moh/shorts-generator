---
name: ass-subtitle
description: "ASS (Advanced SubStation Alpha) subtitle patterns for Shorts Factory. Use when implementing the subtitle module. Covers ASS format specification, style definitions, word-level karaoke animation, safe area positioning, and FFmpeg burn-in integration."
---

# ASS Subtitle Skill

## When to Use

- Implementing the subtitle module
- Generating timed subtitle tracks from transcript
- Creating karaoke-style word highlighting
- Positioning subtitles in the safe area (above face region)
- Burning subtitles into video via FFmpeg

## Format Choice: ASS over SRT

| Feature              | SRT | ASS                        |
| -------------------- | --- | -------------------------- |
| Styled text          | No  | Yes (bold, color, outline) |
| Word-level animation | No  | Yes (karaoke tags)         |
| Positioning          | No  | Yes (MarginV, Alignment)   |
| Font specification   | No  | Yes                        |

**Decision:** ASS format is required for styled, animated subtitles.

## ASS File Structure

```
[Script Info]
Title: Shorts Factory Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Transcript,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,20,20,150,1
Style: Narration,Arial,42,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,8,20,20,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
```

## Style Definitions

### Transcript Style (gameplay speech)

```
Style: Transcript,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,20,20,150,1
```

| Field         | Value        | Meaning                                             |
| ------------- | ------------ | --------------------------------------------------- |
| Name          | Transcript   | Style identifier                                    |
| Fontname      | Arial        | Font family (`config.subtitle.font_name`)           |
| Fontsize      | 48           | Size in pixels (`config.subtitle.font_size`)        |
| PrimaryColour | `&H00FFFFFF` | White (AABBGGRR)                                    |
| OutlineColour | `&H00000000` | Black outline                                       |
| Bold          | -1           | Bold enabled                                        |
| Outline       | 3            | 3px outline width (`config.subtitle.outline_width`) |
| Shadow        | 1            | 1px shadow                                          |
| Alignment     | 2            | Bottom-center                                       |
| MarginV       | 150          | 150px from bottom (`config.subtitle.margin_bottom`) |

### Narration Style (TTS voice-over)

```
Style: Narration,Arial,42,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,8,20,20,200,1
```

| Difference    | Value        | Meaning                          |
| ------------- | ------------ | -------------------------------- |
| Fontsize      | 42           | Slightly smaller than transcript |
| PrimaryColour | `&H0000FFFF` | Yellow (for distinction)         |
| Alignment     | 8            | Top-center (above gameplay)      |
| MarginV       | 200          | Higher positioning               |

## ASS Color Format

Colors use `&HAABBGGRR` format (hex, alpha-blue-green-red):

| Color       | ASS Code     |
| ----------- | ------------ |
| White       | `&H00FFFFFF` |
| Black       | `&H00000000` |
| Yellow      | `&H0000FFFF` |
| Red         | `&H000000FF` |
| Transparent | `&HFF000000` |

**Note:** Alpha is inverted — `00` = opaque, `FF` = transparent.

## Generating Dialogue Events

### Basic Timed Subtitles

```python
def generate_transcript_subtitles(words: list, clip_start_ms: int) -> list[str]:
    """Generate ASS dialogue lines from word-level timestamps."""
    events = []

    # Group words into lines (max 2 lines, ~6-8 words each)
    line_words = []
    for word in words:
        line_words.append(word)
        if len(line_words) >= 7 or (line_words and word.text.endswith((".", "!", "?"))):
            start = format_ass_time(line_words[0].start_time - clip_start_ms)
            end = format_ass_time(line_words[-1].end_time - clip_start_ms)
            text = " ".join(w.text for w in line_words)
            events.append(f"Dialogue: 0,{start},{end},Transcript,,0,0,0,,{text}")
            line_words = []

    # Flush remaining words
    if line_words:
        start = format_ass_time(line_words[0].start_time - clip_start_ms)
        end = format_ass_time(line_words[-1].end_time - clip_start_ms)
        text = " ".join(w.text for w in line_words)
        events.append(f"Dialogue: 0,{start},{end},Transcript,,0,0,0,,{text}")

    return events
```

### Karaoke-Style Word Highlighting

```python
def generate_karaoke_subtitles(words: list, clip_start_ms: int) -> list[str]:
    """Generate karaoke-style subtitles with word-by-word highlighting."""
    events = []

    # Group into display lines
    for group in chunk_words(words, max_per_line=7):
        group_start = format_ass_time(group[0].start_time - clip_start_ms)
        group_end = format_ass_time(group[-1].end_time - clip_start_ms)

        # Build karaoke tags
        parts = []
        for w in group:
            duration_cs = (w.end_time - w.start_time) // 10  # centiseconds
            parts.append(f"{{\\kf{duration_cs}}}{w.text}")

        text = " ".join(parts)
        events.append(f"Dialogue: 0,{group_start},{group_end},Transcript,,0,0,0,,{text}")

    return events
```

**Karaoke tags:**

- `\kf{duration}` — fill effect (smooth highlight, duration in centiseconds)
- `\k{duration}` — instant highlight
- `\ko{duration}` — outline highlight

## ASS Time Format

```python
def format_ass_time(ms: int) -> str:
    """Convert milliseconds to ASS time format H:MM:SS.cc"""
    if ms < 0:
        ms = 0
    total_seconds = ms / 1000.0
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    centiseconds = int((total_seconds * 100) % 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"
```

Examples: `0:00:05.50` = 5.5 seconds, `0:01:30.00` = 90 seconds.

## Safe Area Positioning

Subtitles must NOT overlap the face region (bottom 35%):

```
┌──────────────────┐
│                  │ ← Narration subtitles (Alignment 8, MarginV 200)
│    GAMEPLAY      │
│    (top 65%)     │
│                  │
│──────────────────│ ← Transcript subtitles safe zone
│  ↑ MarginV=150   │   (Alignment 2, MarginV 150 from bottom)
│    FACE CAM      │   Subtitles appear just above face region
│    (bottom 35%)  │
└──────────────────┘
```

- **Transcript:** `Alignment=2` (bottom-center), `MarginV=150` (above face region)
- **Narration:** `Alignment=8` (top-center), `MarginV=200` (below top edge)
- Max 2 lines visible simultaneously

## Complete ASS File Generation

```python
def generate_ass_file(
    words: list,
    tts_words: list | None,
    clip_start_ms: int,
    config: dict,
) -> str:
    """Generate complete ASS subtitle file content."""
    header = f"""[Script Info]
Title: Shorts Factory Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Transcript,{config['subtitle']['font_name']},{config['subtitle']['font_size']},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{config['subtitle']['outline_width']},1,2,20,20,{config['subtitle']['margin_bottom']},1
Style: Narration,{config['subtitle']['font_name']},42,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{config['subtitle']['outline_width']},1,8,20,20,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    # Transcript subtitles (from game speech)
    if words:
        events.extend(generate_karaoke_subtitles(words, clip_start_ms))

    # Narration subtitles (from TTS)
    if tts_words:
        for line in generate_transcript_subtitles(tts_words, 0):
            events.append(line.replace(",Transcript,", ",Narration,"))

    return header + "\n".join(events) + "\n"
```

## FFmpeg Subtitle Burn-In

```bash
ffmpeg -i composite.mp4 -vf "ass=subtitles.ass" -c:v libx264 -crf 20 output.mp4
```

```python
# In renderer module
subtitle_filter = f"ass={subtitle_path}"
args = ["-i", composite_path, "-vf", subtitle_filter, "-c:v", "libx264", output_path]
```

**Note:** Use `ass=` filter (not `subtitles=`) for ASS files — it preserves all styling.

## Anti-Patterns

```python
# ❌ Using SRT format (no styling support)
with open("subs.srt", "w") as f: ...

# ❌ Subtitles overlapping face region
Style: Default,...,Alignment=2,...,MarginV=0  # Overlaps face cam

# ❌ Too many words per line (unreadable on mobile)
text = "This is a very long line of subtitle text that nobody can read"

# ❌ Wrong color format (RGB instead of AABBGGRR)
PrimaryColour = "#FFFFFF"  # Wrong — must be &H00FFFFFF

# ✅ ASS format, safe area positioning, max 7 words/line, correct color format
```
