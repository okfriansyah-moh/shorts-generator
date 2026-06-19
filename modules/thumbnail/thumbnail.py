"""Thumbnail generation module — extracts best frame, converts to 9:16, applies
hook text with auto-contrast color.

Pipeline:
1. FFmpeg extracts a raw frame at 15% into the clip → saved as thumbnail_orig.jpg
2. Pillow converts to 1080×1920 (9:16) with blurred background fill
3. Auto-contrast color is picked by sampling the image region behind the text
4. A deduplicated, punchy 2–4 word hook is burned in with stroke + gradient bar

Output is a 1080×1920 JPEG (9:16) at quality 95.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import urllib.request
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

from contracts.clip import ClipDefinition
from contracts.face import FaceDetectionResult
from contracts.hook import HookResult
from contracts.ingestion import IngestionResult
from contracts.thumbnail import ThumbnailResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Output dimensions ───────────────────────────────────────────────────────

SHORTS_W, SHORTS_H = 1080, 1920  # 9:16

# ── Vibrant candidate colors (RGBA) for text ────────────────────────────────
# Randomised by contrast against the background, not by index

_CANDIDATE_COLORS: list[tuple[int, int, int, int]] = [
    (255, 255,   0, 255),  # yellow
    (255, 255, 255, 255),  # white
    (255,  60,  60, 255),  # red
    (  0, 255, 180, 255),  # cyan-green
    (255, 140,   0, 255),  # orange
    (180, 255,   0, 255),  # lime
    (  0, 200, 255, 255),  # sky blue
    (255,   0, 200, 255),  # magenta
]

# ── Filler phrases stripped before building thumbnail hook ──────────────────

_FILLER_PHRASES: tuple[str, ...] = (
    "you need to see this",
    "stop and watch",
    "wait for the best part",
    "one of the wildest",
    "this gaming moment",
    "a moment so good",
    "watch this",
    "a moment",
    "the highlight",
    "this is the type of content",
    "the type of content",
    "type of content",
    "makes shorts addictive",
    "this is why",
    "here is why",
    "incredible moment",
    "insane play",
    "everyone is talking about",
    "will leave you speechless",
    "seen to be believed",
)

# ── Module-level dedup: video_id → set of already-used hooks ─────────────────
# Populated during a single pipeline run; reset when a new video_id is seen.

_last_video_id: str = ""
_used_hooks: set[str] = set()

# ── Font loading ─────────────────────────────────────────────────────────────

_FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "fonts",
)
_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/Library/Fonts/Impact.ttf",
    "/Library/Fonts/Arial Black.ttf",
    # Linux — prefer condensed/heavy faces for thumbnail impact
    "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/lato/Lato-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
]
_ANTON_URL = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"
_ANTON_PATH = os.path.join(_FONT_DIR, "Anton-Regular.ttf")


def _get_font_path() -> str | None:
    for path in _FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    if os.path.isfile(_ANTON_PATH):
        return _ANTON_PATH
    try:
        os.makedirs(_FONT_DIR, exist_ok=True)
        logger.info("Downloading Anton font for thumbnails")
        urllib.request.urlretrieve(_ANTON_URL, _ANTON_PATH)
        if os.path.isfile(_ANTON_PATH):
            return _ANTON_PATH
    except Exception as exc:
        logger.warning("Could not download Anton font: %s", exc)
    return None


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _get_font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ── 9:16 canvas builder ──────────────────────────────────────────────────────


def _to_9x16(img: Image.Image) -> Image.Image:
    """Convert any aspect ratio image to 1080×1920 with blurred background fill."""
    img = img.convert("RGBA")
    orig_w, orig_h = img.size

    canvas = Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 255))

    # Blurred fill: scale source to cover entire canvas, then heavily blur + darken
    bg_scale = max(SHORTS_W / orig_w, SHORTS_H / orig_h)
    bg = img.resize(
        (int(orig_w * bg_scale), int(orig_h * bg_scale)), Image.LANCZOS
    )
    bx = (bg.width - SHORTS_W) // 2
    by = (bg.height - SHORTS_H) // 2
    bg = bg.crop((bx, by, bx + SHORTS_W, by + SHORTS_H))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
    bg = Image.alpha_composite(
        bg, Image.new("RGBA", (SHORTS_W, SHORTS_H), (0, 0, 0, 120))
    )
    canvas.paste(bg, (0, 0))

    # Main image: scale to fill width, centre vertically
    scale = SHORTS_W / orig_w
    new_w = SHORTS_W
    new_h = int(orig_h * scale)
    main = img.resize((new_w, new_h), Image.LANCZOS)
    paste_y = (SHORTS_H - new_h) // 2
    canvas.paste(main, (0, paste_y), main)

    return canvas


# ── Auto-contrast color picker ───────────────────────────────────────────────


def _relative_luminance(r: int, g: int, b: int) -> float:
    """WCAG relative luminance (0 = black, 1 = white)."""
    def _ch(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * _ch(r) + 0.7152 * _ch(g) + 0.0722 * _ch(b)


def _contrast_ratio(c1: tuple[int, int, int], c2: tuple[int, int, int]) -> float:
    l1 = _relative_luminance(*c1)
    l2 = _relative_luminance(*c2)
    bright, dark = max(l1, l2), min(l1, l2)
    return (bright + 0.05) / (dark + 0.05)


def _pick_text_color(img: Image.Image, text_y: int) -> tuple[int, int, int, int]:
    """Sample the region behind the text block and pick the highest-contrast color."""
    W, H = img.size
    region = img.crop((0, text_y, W, H)).convert("RGB")
    small = region.resize((40, 20), Image.LANCZOS)
    stat = ImageStat.Stat(small)
    bg = (int(stat.mean[0]), int(stat.mean[1]), int(stat.mean[2]))

    best = _CANDIDATE_COLORS[0]
    best_ratio = 0.0
    for color in _CANDIDATE_COLORS:
        ratio = _contrast_ratio(color[:3], bg)
        if ratio > best_ratio:
            best_ratio = ratio
            best = color
    return best


# ── Hook text builder ────────────────────────────────────────────────────────

# Gaming action words that make punchy thumbnail hooks
_GAMING_POWER_WORDS: tuple[str, ...] = (
    "combo", "boss", "dodge", "kill", "clutch", "attack", "escape", "ambush",
    "execute", "destroy", "dominate", "rampage", "headshot", "ace", "win",
    "fight", "rush", "enemy", "ultimate", "power", "critical", "defend",
    "block", "slam", "strike", "wreck", "obliterate", "survive", "parry",
)

# Filler-stripped phrases that remain boring even after stripping
_BORING_WORDS: frozenset[str] = frozenset(
    {
        "this", "that", "the", "a", "an", "is", "are", "was", "were",
        "will", "watch", "see", "look", "here", "now", "just", "even",
        "never", "always", "gets", "get", "can", "you", "and", "or",
        "but", "so", "to", "of", "in", "on", "it", "its", "for",
        "with", "from", "about", "like", "all", "one", "has", "have",
        "be", "been", "being", "do", "did", "does", "had", "if",
        "we", "they", "them", "their", "what", "when", "where", "how",
        "up", "out", "no", "not", "at", "by", "my", "me", "he", "she",
        "go", "going", "come", "coming", "then", "than", "more", "most",
        "make", "making", "left", "right", "still", "way", "thing", "things",
        "kind", "type", "best", "good", "great", "big", "new", "old",
        "every", "each", "some", "any", "much", "many", "too", "very",
        "i", "im", "your", "our", "us",
    }
)


def _build_hook_text(
    hook_result: HookResult,
    max_words: int = 4,
    used: set[str] | None = None,
) -> str:
    """Build a punchy, deduplicated hook for the thumbnail.

    Priority:
    1. Gaming power words from keyword_source (most specific)
    2. Cleaned hook_text — filler stripped, boring words removed
    3. Cleaned story_text as last resort
    """
    used = used or set()

    # ── Strategy 1: keyword_source gaming words ──
    kw = [k.upper() for k in hook_result.keyword_source if k in _GAMING_POWER_WORDS]
    if len(kw) >= 2:
        candidate = " ".join(kw[:max_words])
        if candidate not in used:
            return candidate
    if len(kw) == 1:
        candidate = f"NINJA GAIDEN {kw[0]}"
        if candidate not in used:
            return candidate

    # ── Strategy 2: strip filler from hook_text ──
    text = hook_result.hook_text.lower()
    for phrase in _FILLER_PHRASES:
        text = text.replace(phrase, " ")
    words = [
        w.strip(".,!?—-").upper()
        for w in text.split()
        if w.strip(".,!?—-").lower() not in _BORING_WORDS and w.strip(".,!?—-")
    ]
    # Deduplicate adjacent identical words
    deduped: list[str] = []
    for w in words:
        if not deduped or w != deduped[-1]:
            deduped.append(w)

    for start in range(len(deduped)):
        raw_chunk = deduped[start : start + max_words]
        # Remove duplicate words within the chunk (preserve order)
        seen_words: set[str] = set()
        unique_chunk: list[str] = []
        for w in raw_chunk:
            if w not in seen_words:
                seen_words.add(w)
                unique_chunk.append(w)
        if len(unique_chunk) < 2:
            continue
        chunk = " ".join(unique_chunk)
        if chunk and chunk not in used:
            return chunk

    # ── Strategy 3: story_text fallback ──
    story_words = [
        w.strip(".,!?—-").upper()
        for w in hook_result.story_text.split()
        if w.strip(".,!?—-").lower() not in _BORING_WORDS and w.strip(".,!?—-")
    ]
    for start in range(len(story_words)):
        chunk = " ".join(story_words[start : start + max_words])
        if chunk and chunk not in used:
            return chunk

    # ── Final fallback: generic hook with clip index suffix ──
    base = f"NINJA GAIDEN CLIP"
    n = sum(1 for h in used if h.startswith(base))
    return f"{base} {n + 1}" if n else base


# ── Frame extraction ─────────────────────────────────────────────────────────


def _select_timestamp(clip: ClipDefinition) -> float:
    """Return frame timestamp (seconds) at 15% into the clip."""
    start_s = clip.start_time / 1000.0
    duration_s = (clip.end_time - clip.start_time) / 1000.0
    return start_s + duration_s * 0.15


def _select_timestamp_ms(clip: ClipDefinition) -> int:
    duration_ms = clip.end_time - clip.start_time
    return clip.start_time + int(duration_ms * 0.15)


def _has_face(face_result: FaceDetectionResult | None) -> bool:
    if face_result is None:
        return False
    return any(len(sd.bounding_boxes) > 0 for sd in face_result.scene_data)


def _extract_raw_frame(
    video_path: str, timestamp: float, output_path: str, saturation: float, contrast: float
) -> None:
    """Extract a single enhanced frame from the video using FFmpeg."""
    vf = (
        f"eq=saturation={saturation:.4f}:contrast={contrast:.4f}"
    )
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{timestamp:.3f}",
        "-i", video_path,
        "-frames:v", "1",
        "-vf", vf,
        "-q:v", "2",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg frame extraction failed (exit {result.returncode}): "
            f"{result.stderr[-1500:]}"
        )


# ── Text rendering ───────────────────────────────────────────────────────────


def _render_text_on_image(img: Image.Image, hook: str) -> Image.Image:
    """Render hook text on the 9:16 canvas with gradient bar and auto-contrast color."""
    W, H = img.size  # 1080 × 1920
    margin = int(W * 0.08)
    max_width_px = W - margin * 2

    font_size = max(72, W // 8)
    font = _load_font(font_size)
    line_height = int(font_size * 1.22)

    draw_temp = ImageDraw.Draw(img)

    def _wrap(text: str) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            test = (current + " " + word).strip()
            bb = draw_temp.textbbox((0, 0), test, font=font)
            if (bb[2] - bb[0]) <= max_width_px:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    lines = _wrap(hook)
    block_h = len(lines) * line_height

    # Centre text vertically in the lower 55% of the canvas
    zone_top = int(H * 0.45)
    text_y = zone_top + (H - zone_top - block_h) // 2

    # Auto-contrast color from the background region
    text_color = _pick_text_color(img, text_y)

    # Gradient bar behind text
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bar_draw = ImageDraw.Draw(overlay)
    bar_top = text_y - int(H * 0.025)
    for y in range(bar_top, H):
        alpha = int(200 * (y - bar_top) / max(1, H - bar_top))
        bar_draw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)
    stroke = max(3, font_size // 16)
    cur_y = text_y
    for line in lines:
        lb = draw.textbbox((0, 0), line, font=font)
        lw = lb[2] - lb[0]
        lx = (W - lw) // 2 - lb[0]
        for dx in range(-stroke, stroke + 1):
            for dy in range(-stroke, stroke + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((lx + dx, cur_y + dy), line, font=font, fill=(0, 0, 0, 255))
        draw.text((lx, cur_y), line, font=font, fill=text_color)
        cur_y += line_height

    return img


# ── Public entry point ───────────────────────────────────────────────────────


def process(
    clip: ClipDefinition,
    face_result: FaceDetectionResult | None,
    hook_result: HookResult,
    ingestion_result: IngestionResult,
    config: dict,
    output_dir: str,
) -> ThumbnailResult:
    """Generate a 1080×1920 (9:16) JPEG thumbnail for the clip.

    Steps:
    1. Extract a raw frame at 15% into the clip with FFmpeg (saved as ``_orig.jpg``).
    2. Convert to 9:16 using blurred background fill via Pillow.
    3. Pick the highest-contrast vibrant color from the text placement region.
    4. Render a deduplicated 2–4 word hook with stroke and gradient bar.
    5. Save as JPEG quality 95.

    Idempotent: if ``thumbnail.jpg`` already exists and is non-empty, the cached
    result is returned without re-running FFmpeg or Pillow.

    Args:
        clip: ClipDefinition for the clip being processed.
        face_result: Face detection result (used for ``face_visible`` flag only).
        hook_result: Hook text and keywords for overlay text selection.
        ingestion_result: Source video metadata (path for frame extraction).
        config: Full pipeline config dict.
        output_dir: Root output directory (e.g. ``output/``).

    Returns:
        ThumbnailResult DTO with path to the generated thumbnail.

    Raises:
        RuntimeError: If FFmpeg or Pillow fails.
    """
    global _last_video_id, _used_hooks

    thumb_cfg = config.get("thumbnail", {})
    saturation = thumb_cfg.get("saturation_boost", 1.15)
    contrast = thumb_cfg.get("contrast_boost", 1.10)
    quality = thumb_cfg.get("quality", 95)
    max_hook_words = thumb_cfg.get("max_hook_words", 4)

    video_dir_name = config.get("_runtime", {}).get("video_dir_name", clip.video_id)
    clip_dir = os.path.abspath(
        os.path.join(output_dir, video_dir_name, "clips", f"shorts-{clip.clip_index + 1}")
    )
    os.makedirs(clip_dir, exist_ok=True)

    output_path = os.path.join(clip_dir, "thumbnail.jpg")
    orig_path = os.path.join(clip_dir, "thumbnail_orig.jpg")

    # Reset per-video dedup set when processing a new video
    if clip.video_id != _last_video_id:
        _last_video_id = clip.video_id
        _used_hooks = set()

    # Idempotency: return cached result if already generated.
    if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
        hook = _build_hook_text(hook_result, max_words=max_hook_words, used=_used_hooks)
        _used_hooks.add(hook)
        logger.info(
            "Thumbnail already exists; returning cached result",
            extra={
                "clip_id": clip.clip_id,
                "video_id": clip.video_id,
                "stage": "thumbnail",
                "status": "cached",
            },
        )
        return ThumbnailResult(
            clip_id=clip.clip_id,
            image_path=output_path,
            resolution=(SHORTS_W, SHORTS_H),
            text_overlay=hook,
            face_visible=_has_face(face_result),
            frame_timestamp_ms=_select_timestamp_ms(clip),
            frame_score=0.0,
        )

    timestamp = _select_timestamp(clip)

    # ── Step 1: extract raw frame ──
    _extract_raw_frame(ingestion_result.path, timestamp, orig_path, saturation, contrast)
    if not os.path.isfile(orig_path) or os.path.getsize(orig_path) == 0:
        raise RuntimeError(f"FFmpeg produced no frame: {orig_path}")

    # ── Step 2: build hook text (deduplicated) ──
    hook = _build_hook_text(hook_result, max_words=max_hook_words, used=_used_hooks)
    _used_hooks.add(hook)

    # ── Step 3: Pillow — 9:16 + auto-contrast color + text ──
    raw = Image.open(orig_path)
    canvas = _to_9x16(raw)
    canvas = _render_text_on_image(canvas, hook)
    canvas.convert("RGB").save(output_path, "JPEG", quality=quality)

    if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Thumbnail generation produced no output: {output_path}")

    logger.info(
        "Thumbnail generated",
        extra={
            "clip_id": clip.clip_id,
            "video_id": clip.video_id,
            "stage": "thumbnail",
            "status": "ok",
            "output_path": output_path,
            "hook": hook,
        },
    )
    return ThumbnailResult(
        clip_id=clip.clip_id,
        image_path=output_path,
        resolution=(SHORTS_W, SHORTS_H),
        text_overlay=hook,
        face_visible=_has_face(face_result),
        frame_timestamp_ms=_select_timestamp_ms(clip),
        frame_score=0.0,
    )
