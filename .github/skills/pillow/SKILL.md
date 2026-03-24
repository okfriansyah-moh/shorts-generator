---
name: pillow
description: "Pillow/PIL patterns for Shorts Factory. Use when implementing the thumbnail module. Covers frame selection scoring, image composition, text overlay with stroke, color/contrast enhancement, and JPEG output constraints."
---

# Pillow / PIL Thumbnail Skill

## When to Use

- Implementing the thumbnail module
- Selecting best frame from a clip for thumbnail
- Adding text overlays with outline/stroke
- Adjusting color saturation and contrast
- Generating JPEG at exact 1280×720

## Library

- **Package:** `Pillow` (PyPI, fork of PIL)
- **Version:** >= 9.0.0

## Thumbnail Pipeline Overview

```
1. Select best frame from clip → frame scoring
2. Resize + pad to 1280×720 → exact dimensions
3. Enhance saturation + contrast → visual pop
4. Add text overlay with stroke → hook words
5. Save as JPEG quality 90+ → output
```

## Frame Selection (Best Frame Scoring)

Sample frames from the clip and score each:

```python
import cv2
import numpy as np

def select_best_frame(
    video_path: str,
    start_ms: int,
    end_ms: int,
    face_bboxes: list | None = None,
) -> np.ndarray:
    """Score frames and return the best one for thumbnail."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)

    # Sample at 1fps, prefer first 30% of clip (hook moment)
    sample_interval = int(fps)
    start_frame = int(start_ms * fps / 1000)
    end_frame = int(end_ms * fps / 1000)
    # Bias toward first 30%
    priority_end = start_frame + int((end_frame - start_frame) * 0.3)

    best_frame = None
    best_score = -1.0

    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    frame_num = start_frame

    while frame_num < end_frame:
        ret, frame = cap.read()
        if not ret:
            break

        score = score_frame(frame, frame_num <= priority_end)

        if score > best_score:
            best_score = score
            best_frame = frame.copy()

        frame_num += sample_interval
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)

    cap.release()
    return best_frame

def score_frame(frame: np.ndarray, is_priority_zone: bool) -> float:
    """Score a single frame for thumbnail quality."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Clarity: Laplacian variance (higher = sharper)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    clarity = min(laplacian_var / 1000.0, 1.0)

    # Color variance: HSV hue variance (higher = more colorful)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    color = min(np.var(hsv[:, :, 0]) / 100.0, 1.0)

    # Brightness: avoid too dark/light
    mean_brightness = np.mean(gray)
    brightness = 1.0 - abs(mean_brightness - 127) / 127.0

    # Priority zone bonus (first 30% of clip)
    zone_bonus = 0.2 if is_priority_zone else 0.0

    # Weighted score
    return clarity * 1.0 + color * 2.0 + brightness * 0.5 + zone_bonus
```

**Scoring weights:**
| Factor | Weight | Rationale |
|--------|--------|-----------|
| Color variance | 2.0 | Colorful frames attract clicks |
| Clarity | 1.0 | Sharp frames look professional |
| Brightness | 0.5 | Avoid extremes |
| Priority zone (first 30%) | +0.2 bonus | Hook moment preferred |
| Face present | +3.0 | If face detection data available |

## Image Composition

```python
from PIL import Image

def create_thumbnail_base(frame: np.ndarray) -> Image.Image:
    """Convert OpenCV frame to Pillow and fit to 1280×720."""
    # Convert BGR → RGB
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)

    # Resize preserving aspect ratio
    img.thumbnail((1280, 720), Image.Resampling.LANCZOS)

    # Pad to exact 1280×720 (center)
    bg = Image.new("RGB", (1280, 720), color=(0, 0, 0))
    offset = ((1280 - img.width) // 2, (720 - img.height) // 2)
    bg.paste(img, offset)

    return bg
```

## Color & Contrast Enhancement

```python
from PIL import ImageEnhance

def enhance_thumbnail(img: Image.Image) -> Image.Image:
    """Boost saturation and contrast for visual impact."""
    # +15% saturation
    img = ImageEnhance.Color(img).enhance(1.15)
    # +10% contrast
    img = ImageEnhance.Contrast(img).enhance(1.10)
    return img
```

## Text Overlay with Stroke

```python
from PIL import ImageDraw, ImageFont

def add_text_overlay(
    img: Image.Image,
    text: str,
    font_size: int = 72,     # config.thumbnail.font_size
    max_words: int = 3,       # config.thumbnail.max_text_words
    stroke_width: int = 4,
) -> Image.Image:
    """Add bold text with black outline at bottom-center."""

    # Truncate to max words
    words = text.split()[:max_words]
    display_text = " ".join(words).upper()

    draw = ImageDraw.Draw(img)

    # Load font (try system fonts, fall back to default)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
        except OSError:
            font = ImageFont.load_default()

    # Calculate text position (bottom-center)
    bbox = draw.textbbox((0, 0), display_text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (1280 - text_width) // 2
    y = 720 - text_height - 40  # 40px from bottom

    # Draw text with stroke (outline)
    draw.text(
        (x, y),
        display_text,
        font=font,
        fill="white",
        stroke_width=stroke_width,
        stroke_fill="black",
    )

    return img
```

**Text specs:**
| Property | Value | Source |
|----------|-------|--------|
| Font | Bold sans-serif | System font |
| Size | 72pt | `config.thumbnail.font_size` |
| Color | White | Fixed |
| Stroke | Black, 4px | Fixed |
| Position | Bottom-center | 40px from bottom |
| Max words | 3 | `config.thumbnail.max_text_words` |
| Transform | UPPERCASE | Engagement optimization |

## Save as JPEG

```python
def save_thumbnail(img: Image.Image, output_path: str, quality: int = 90):
    """Save thumbnail as JPEG with exact constraints."""
    assert img.size == (1280, 720), f"Wrong size: {img.size}"

    # Atomic write
    tmp_path = f"{output_path}.tmp"
    img.save(tmp_path, "JPEG", quality=quality, optimize=True)
    os.replace(tmp_path, output_path)
```

**Output constraints:**
| Property | Value | Source |
|----------|-------|--------|
| Format | JPEG | `config.thumbnail.format` |
| Resolution | 1280×720 | `config.thumbnail.width/height` |
| Quality | 90 | `config.thumbnail.quality` |

## Full Thumbnail Generation

```python
def generate_thumbnail(
    video_path: str,
    clip: ClipDefinition,
    hook_result: HookResult,
    config: dict,
) -> ThumbnailResult:
    """Complete thumbnail module entry point."""
    # 1. Select best frame
    frame = select_best_frame(video_path, clip.start_time, clip.end_time)

    # 2. Create base image
    img = create_thumbnail_base(frame)

    # 3. Enhance
    img = enhance_thumbnail(img)

    # 4. Add text
    text = hook_result.hook_text  # First few words of hook
    img = add_text_overlay(img, text, config["thumbnail"]["font_size"], config["thumbnail"]["max_text_words"])

    # 5. Save
    output_path = f"output/{clip.video_id}/thumbnails/{clip.clip_id}.jpg"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    save_thumbnail(img, output_path, config["thumbnail"]["quality"])

    return ThumbnailResult(
        clip_id=clip.clip_id,
        thumbnail_path=output_path,
        width=1280,
        height=720,
        text=text,
    )
```

## Anti-Patterns

```python
# ❌ Wrong resolution (YouTube rejects non-standard thumbnails)
img = img.resize((1920, 1080))

# ❌ No text stroke (invisible on varied backgrounds)
draw.text((x, y), text, fill="white")  # No outline

# ❌ Too many words (unreadable at thumbnail size)
text = "This Is Way Too Many Words For A Thumbnail"

# ❌ Low quality JPEG (artifacts visible)
img.save(path, quality=50)

# ✅ 1280×720, stroked text, max 3 words, quality 90+
```
