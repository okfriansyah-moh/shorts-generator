#!/usr/bin/env python3
"""Thumbnail Text Overlay for Shorts Factory.

Adds big bold text to existing thumbnail.jpg files before upload.
- Converts 16:9 → 9:16 (1080x1920) for YouTube Shorts
- Blurred background fill for the extended areas
- Text color auto-selected for max contrast against background
- Semi-transparent gradient bar behind text

Usage:
  python3 scripts/thumbnail_overlay.py path/to/thumbnail.jpg "YOUR TEXT"
  python3 scripts/thumbnail_overlay.py --all
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import textwrap
import urllib.request

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

# ── Constants ──────────────────────────────────────────────────────────────

SHORTS_W, SHORTS_H = 1080, 1920  # 9:16

FONT_DIR = os.path.join(_PROJECT_ROOT, "assets", "fonts")
_MACOS_FONT_CANDIDATES = [
    # Linux system fonts (available in sandbox)
    "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf",
    # macOS fonts
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/Library/Fonts/Impact.ttf",
    "/Library/Fonts/Arial Black.ttf",
]
_ANTON_URL = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"
_ANTON_PATH = os.path.join(FONT_DIR, "Anton-Regular.ttf")

# Vibrant candidate colors for text (RGBA)
_CANDIDATE_COLORS = [
    (255, 255,   0, 255),  # yellow
    (255, 255, 255, 255),  # white
    (255,  60,  60, 255),  # red
    (  0, 255, 180, 255),  # cyan-green
    (255, 140,   0, 255),  # orange
    (180, 255,   0, 255),  # lime
    (  0, 200, 255, 255),  # sky blue
    (255,   0, 200, 255),  # magenta
]


# ── Font ───────────────────────────────────────────────────────────────────

_FONT_PATH_CACHE: str | None | bool = False  # False = not yet resolved


def _get_font_path() -> str | None:
    global _FONT_PATH_CACHE
    if _FONT_PATH_CACHE is not False:
        return _FONT_PATH_CACHE  # type: ignore[return-value]
    for path in _MACOS_FONT_CANDIDATES:
        if os.path.isfile(path):
            _FONT_PATH_CACHE = path
            return path
    if os.path.isfile(_ANTON_PATH):
        _FONT_PATH_CACHE = _ANTON_PATH
        return _ANTON_PATH
    try:
        os.makedirs(FONT_DIR, exist_ok=True)
        print("Downloading Anton font...")
        req = urllib.request.urlopen(_ANTON_URL, timeout=5)
        with open(_ANTON_PATH, "wb") as f:
            f.write(req.read())
        if os.path.isfile(_ANTON_PATH):
            _FONT_PATH_CACHE = _ANTON_PATH
            return _ANTON_PATH
    except Exception as e:
        print(f"Warning: could not download font: {e}")
    _FONT_PATH_CACHE = None
    return None


_FONT_CACHE: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    path = _get_font_path()
    if path:
        try:
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[size] = font
            return font
        except Exception:
            pass
    font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


# ── 9:16 conversion ────────────────────────────────────────────────────────

def _to_9x16(img: Image.Image) -> Image.Image:
    """Convert any aspect ratio image to 1080x1920 (9:16).

    Strategy:
    1. Scale original to fill width (1080px), centered vertically
    2. Fill top/bottom bars with a heavily blurred + darkened version of the image
    """
    img = img.convert("RGBA")
    orig_w, orig_h = img.size

    canvas = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 255))

    # --- Blurred background: scale to fill entire 9:16 canvas ---
    bg_scale = max(SHORTS_W / orig_w, SHORTS_H / orig_h)
    bg_w = int(orig_w * bg_scale)
    bg_h = int(orig_h * bg_scale)
    bg = img.resize((bg_w, bg_h), Image.LANCZOS)
    # Crop center to 1080x1920
    bg_x = (bg_w - SHORTS_W) // 2
    bg_y = (bg_h - SHORTS_H) // 2
    bg = bg.crop((bg_x, bg_y, bg_x + SHORTS_W, bg_y + SHORTS_H))
    # Heavy blur + darken
    bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
    darkener = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 120))
    bg = Image.alpha_composite(bg, darkener)
    canvas.paste(bg, (0, 0))

    # --- Main image: scale to fill width, center vertically ---
    scale = SHORTS_W / orig_w
    new_w = SHORTS_W
    new_h = int(orig_h * scale)
    main = img.resize((new_w, new_h), Image.LANCZOS)
    paste_y = (SHORTS_H - new_h) // 2
    canvas.paste(main, (0, paste_y), main)

    return canvas


# ── Contrast color picker ──────────────────────────────────────────────────

def _relative_luminance(r: int, g: int, b: int) -> float:
    """WCAG relative luminance (0=black, 1=white)."""
    def ch(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast_ratio(c1: tuple, c2: tuple) -> float:
    l1 = _relative_luminance(c1[0], c1[1], c1[2])
    l2 = _relative_luminance(c2[0], c2[1], c2[2])
    bright, dark = max(l1, l2), min(l1, l2)
    return (bright + 0.05) / (dark + 0.05)


def _pick_text_color(img: Image.Image, text_y: int) -> tuple[int, int, int, int]:
    """Sample the text placement area and pick the highest-contrast candidate color."""
    W, H = img.size
    # Sample bottom region where text will appear
    region = img.crop((0, text_y, W, H)).convert("RGB")
    # Downsample for speed
    small = region.resize((40, 20), Image.LANCZOS)
    stat = ImageStat.Stat(small)
    avg_r, avg_g, avg_b = int(stat.mean[0]), int(stat.mean[1]), int(stat.mean[2])
    bg_color = (avg_r, avg_g, avg_b)

    best_color = _CANDIDATE_COLORS[0]
    best_ratio = 0.0
    for color in _CANDIDATE_COLORS:
        ratio = _contrast_ratio(color[:3], bg_color)
        if ratio > best_ratio:
            best_ratio = ratio
            best_color = color

    return best_color


# ── Text shortening ────────────────────────────────────────────────────────

_FILLER_EN = [
    "A moment", "Watch this", "You need to see this",
    "Stop and watch", "Wait for the best part", "This is unreal",
    "You won't believe", "Must see",
]

_FILLER_ID = [
    "Kamu harus lihat ini", "Hentikan dan tonton", "Tunggu bagian terbaiknya",
    "Ini nggak nyata", "Sebuah momen", "Tonton ini", "Luar biasa banget",
    "Keren parah", "Nggak percaya", "Harus ditonton", "Ini keren",
    "Kalian harus lihat", "Tonton sampai habis",
]


def _make_hook(
    title: str,
    max_words: int = 6,
    used: set[str] | None = None,
    language: str = "en",
) -> str:
    """Extract a short punchy hook from a clip title for thumbnail overlay.

    Strips language-specific filler phrases, then picks the most impactful
    word window.  Guarantees uniqueness when ``used`` is provided.

    Args:
        title: Full clip title.
        max_words: Maximum words in the hook (default 6).
        used: Set of already-used hooks — ensures no duplicates.
        language: "en" or "id" — controls which filler list to strip.
    """
    filler = _FILLER_ID if language == "id" else _FILLER_EN
    text = title
    for f in filler:
        # Case-insensitive strip
        import re as _re
        text = _re.sub(_re.escape(f), "", text, flags=_re.IGNORECASE).strip(" —-")

    words = text.split()

    # Try sliding windows across the title to find a unique hook
    candidates: list[str] = []
    for start in range(len(words)):
        chunk = words[start:start + max_words]
        if not chunk:
            break
        candidates.append(" ".join(chunk).upper())

    # Also try shorter versions from the end
    for end_words in range(min(max_words, len(words)), 2, -1):
        candidates.append(" ".join(words[-end_words:]).upper())

    if used is None:
        return candidates[0] if candidates else title.upper()

    for candidate in candidates:
        if candidate not in used:
            return candidate

    # All windows are taken — append clip number suffix
    base = candidates[0] if candidates else title.upper()
    n = sum(1 for h in used if h.startswith(base))
    return f"{base} {n + 1}"


# ── Core overlay ────────────────────────────────────────────────────────────

def add_text_overlay(
    image_path: str,
    text: str,
    output_path: str | None = None,
    used_hooks: set[str] | None = None,
    language: str = "en",
) -> str:
    output_path = output_path or image_path

    # Always work from the original — back it up on first run
    orig_path = image_path.replace(".jpg", "_orig.jpg").replace(".jpeg", "_orig.jpeg")
    if not os.path.isfile(orig_path):
        import shutil
        shutil.copy2(image_path, orig_path)

    # Load and convert to 9:16
    img = Image.open(orig_path).convert("RGBA")
    img = _to_9x16(img)
    W, H = img.size  # 1080 x 1920

    hook = _make_hook(text, used=used_hooks, language=language)

    # Font size: start large and shrink until text fits within margins
    margin = int(W * 0.06)           # 6% margin each side
    max_width_px = W - margin * 2
    font_size = W // 4               # start at ~270px on 1080w canvas

    draw_temp = ImageDraw.Draw(img)

    def _wrap_by_pixels(words_list: list[str], fnt, max_px: int) -> list[str]:
        lines, current = [], ""
        for word in words_list:
            test = (current + " " + word).strip()
            bb = draw_temp.textbbox((0, 0), test, font=fnt)
            if (bb[2] - bb[0]) <= max_px:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    # Auto-shrink font until the whole block fits within 35% of image height
    # (keeps 2-3 lines of big text without dominating the whole frame)
    words = hook.split()
    font_size = W // 6               # start at ~180px on 1080w canvas
    while font_size >= 60:
        font = _load_font(font_size)
        line_height = int(font_size * 1.15)
        lines = _wrap_by_pixels(words, font, max_width_px)
        block_h = len(lines) * line_height
        if block_h <= H * 0.35:
            break
        font_size -= 8

    font = _load_font(font_size)
    line_height = int(font_size * 1.15)
    lines = _wrap_by_pixels(words, font, max_width_px)
    block_h = len(lines) * line_height

    # Center text block vertically in the middle third of the image (33%–67%)
    zone_top = int(H * 0.33)
    zone_bot = int(H * 0.67)
    text_y = zone_top + (zone_bot - zone_top - block_h) // 2

    # Pick contrast color from the area behind the text
    text_color = _pick_text_color(img, text_y)

    # Dark semi-transparent box behind text block for readability
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bar_draw = ImageDraw.Draw(overlay)
    pad = int(font_size * 0.25)
    bar_draw.rectangle(
        [(0, text_y - pad), (W, text_y + block_h + pad)],
        fill=(0, 0, 0, 160),
    )
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)
    stroke_size = max(4, font_size // 12)
    current_y = text_y

    for line in lines:
        lb = draw.textbbox((0, 0), line, font=font)
        lw = lb[2] - lb[0]
        lx = (W - lw) // 2 - lb[0]  # true horizontal center

        # Black stroke for punch
        for dx in range(-stroke_size, stroke_size + 1):
            for dy in range(-stroke_size, stroke_size + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((lx + dx, current_y + dy), line, font=font, fill=(0, 0, 0, 255))

        # Main text
        draw.text((lx, current_y), line, font=font, fill=text_color)
        current_y += line_height

    img.convert("RGB").save(output_path, "JPEG", quality=95)
    return output_path


# ── --regen-originals mode ────────────────────────────────────────────────

def regen_originals() -> int:
    """Re-extract clean thumbnail frames from the raw video using FFmpeg (no text)."""
    import glob
    import subprocess

    from core.config import load_config

    os.chdir(_PROJECT_ROOT)
    config = load_config()

    output_dir = config.get("paths", {}).get("output_dir", "output")
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(_PROJECT_ROOT, output_dir)

    sat = config.get("thumbnail", {}).get("saturation_boost", 1.15)
    con = config.get("thumbnail", {}).get("contrast_boost", 1.10)

    video_dirs = sorted(glob.glob(os.path.join(output_dir, "*_*")))
    total = 0

    for video_dir in video_dirs:
        clips_dir = os.path.join(video_dir, "clips")
        if not os.path.isdir(clips_dir):
            continue

        # Find raw source video
        video_dir_name = os.path.basename(video_dir)
        raw_name = "_".join(video_dir_name.split("_")[1:])  # strip video_id prefix
        raw_video = os.path.join(_PROJECT_ROOT, "raw", f"{raw_name}.mp4")
        if not os.path.isfile(raw_video):
            print(f"  SKIP {video_dir_name} — raw video not found: {raw_video}")
            continue

        short_dirs = sorted(
            glob.glob(os.path.join(clips_dir, "shorts-*")),
            key=lambda p: int(os.path.basename(p).split("-")[1]),
        )

        for idx, short_dir in enumerate(short_dirs):
            # 15% into each 50s clip window
            clip_start_s = idx * 50.0
            timestamp = clip_start_s + 50.0 * 0.15

            thumb_path = os.path.join(short_dir, "thumbnail.jpg")
            orig_path  = os.path.join(short_dir, "thumbnail_orig.jpg")

            vf = (
                f"scale=1280:720:force_original_aspect_ratio=decrease,"
                f"pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,"
                f"eq=saturation={sat:.4f}:contrast={con:.4f}"
            )
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", raw_video,
                     "-frames:v", "1", "-vf", vf, "-q:v", "2", orig_path],
                    capture_output=True, check=True, timeout=30,
                )
                # Also write as the working thumbnail
                import shutil
                shutil.copy2(orig_path, thumb_path)
                total += 1
                print(f"  OK {os.path.basename(short_dir)} @ {timestamp:.1f}s")
            except subprocess.CalledProcessError as e:
                print(f"  ERROR {os.path.basename(short_dir)}: {e.stderr[-200:]}")

    print(f"\nRegenerated {total} clean thumbnail(s).")
    return 0


# ── --all mode ─────────────────────────────────────────────────────────────

def process_all() -> int:
    from core.config import load_config

    os.chdir(_PROJECT_ROOT)
    config = load_config()
    db_path = config.get("paths", {}).get("database", "output/shorts_factory.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(_PROJECT_ROOT, db_path)

    # Read language from config
    language = config.get("metadata", {}).get("language", "en")

    # Derive per-video state file from the first clip's thumbnail_path
    def _video_dir_from_rows(rows_: list) -> str | None:
        for r_ in rows_:
            thumb_ = (r_["thumbnail_path"] or "").replace("\\", "/")
            if not thumb_:
                continue
            parts_ = thumb_.split("/")
            if len(parts_) >= 2:
                candidate_ = os.path.join(_PROJECT_ROOT, parts_[0], parts_[1])
                if os.path.isdir(candidate_):
                    return candidate_
        return None

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT clip_id, title, thumbnail_path FROM clips WHERE status='scheduled'"
    ).fetchall()
    con.close()

    _vdir = _video_dir_from_rows(rows)
    state_file = (
        os.path.join(_vdir, "thumbnail_overlaid_clips.json")
        if _vdir
        else os.path.join(_PROJECT_ROOT, "output", "thumbnail_overlaid_clips.json")
    )
    done_ids: set[str] = set()

    processed = 0
    used_hooks: set[str] = set()
    for row in rows:
        clip_id = row["clip_id"]
        title = row["title"] or ""
        thumb = row["thumbnail_path"] or ""

        if not thumb or not os.path.isfile(thumb):
            print(f"  SKIP {clip_id} — thumbnail not found: {thumb}")
            continue

        try:
            add_text_overlay(thumb, title, used_hooks=used_hooks, language=language)
            hook = _make_hook(title, used=used_hooks, language=language)
            used_hooks.add(hook)
            done_ids.add(clip_id)
            processed += 1
            print(f"  OK {clip_id}: {hook}")
        except Exception as e:
            print(f"  ERROR {clip_id}: {e}")

    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        json.dump({"done": sorted(done_ids)}, f, indent=2)

    print(f"\nOverlaid {processed} thumbnail(s).")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == "--regen-originals":
        return regen_originals()
    if args[0] == "--all":
        return process_all()
    if len(args) >= 2:
        path, text = args[0], " ".join(args[1:])
        if not os.path.isfile(path):
            print(f"File not found: {path}")
            return 1
        out = add_text_overlay(path, text)
        print(f"Saved: {out}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
