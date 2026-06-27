#!/usr/bin/env python3
"""Direct clip renderer — bypasses pipeline overhead entirely.

Reads generated clips from DB, runs combined compositor+renderer FFmpeg
command, generates thumbnails, and updates DB with video_path/thumbnail_path.

Layout is read from the account config (compositor.default_layout +
compositor.face_region), so changing account.yaml automatically affects
future renders without touching this script.

Usage:
    python3 scripts/direct_render_clips.py --video-id VIDEO_ID --batch START END
    python3 scripts/direct_render_clips.py --video-id VIDEO_ID --batch START END --account ACCOUNT
"""

import argparse
import os
import sqlite3
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

DB_PATH = os.path.join(PROJECT_ROOT, "output/shorts_factory.db")

# ── Layout filter builders ────────────────────────────────────────────────

def _gameplay_only_filter() -> str:
    """Full-frame gameplay with blurred background fill (no facecam)."""
    return (
        "[0:v]split=2[bg_in][fg_in];"
        "[bg_in]scale=108:192:force_original_aspect_ratio=increase,"
        "crop=108:192,"
        "scale=1080:1920:flags=bilinear[bg];"
        "[fg_in]crop=ih*9/16:ih,"
        "scale=1080:1920[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[v];"
        "[0:a]volume=0.7[a]"
    )


# PiP region → (x_ratio, y_ratio, w_ratio, h_ratio) within source frame
# These define where to crop the face from the source video.
_PIP_REGIONS = {
    "bottom_left":        (0.00, 0.65, 0.28, 0.35),
    "bottom_right":       (0.72, 0.65, 0.28, 0.35),
    "bottom_center":      (0.25, 0.65, 0.50, 0.35),
    "bottom_middle":      (0.25, 0.65, 0.50, 0.35),
    "middle_left":        (0.00, 0.30, 0.28, 0.40),
    "middle_right":       (0.72, 0.30, 0.28, 0.40),
    "upper_middle_left":  (0.00, 0.10, 0.28, 0.35),
    "top_left":           (0.00, 0.00, 0.28, 0.30),
    "top_right":          (0.72, 0.00, 0.28, 0.30),
    "center":             (0.25, 0.25, 0.50, 0.50),
}


def _split_filter(face_region: str, src_w: int = 1280, src_h: int = 720) -> str:
    """Split layout: gameplay top 65% + face cam bottom 35%.

    Crops the face from face_region in the source, scales to fill the
    bottom 35% of a 1080×1920 frame.  Gameplay fills the top 65%.
    """
    pip = _PIP_REGIONS.get(face_region, _PIP_REGIONS["bottom_left"])
    pip_x = int(pip[0] * src_w)
    pip_y = int(pip[1] * src_h)
    pip_w = int(pip[2] * src_w)
    pip_h = int(pip[3] * src_h)

    gameplay_h = 1248   # 65% of 1920
    face_h     = 672    # 35% of 1920
    out_w      = 1080

    return (
        # Split into three streams: background fill, gameplay, and face
        f"[0:v]split=3[bg_src][gp_src][face_src];"
        # Background: blurred full frame
        f"[bg_src]scale=108:192:force_original_aspect_ratio=increase,"
        f"crop=108:192,scale={out_w}:1920:flags=bilinear[bg];"
        # Gameplay: center-crop to 9:16 aspect, scale to top 65%
        f"[gp_src]crop=ih*{out_w/gameplay_h:.4f}:ih,"
        f"scale={out_w}:{gameplay_h}[gameplay];"
        # Face: crop PiP region, scale to bottom 35%
        f"[face_src]crop={pip_w}:{pip_h}:{pip_x}:{pip_y},"
        f"scale={out_w}:{face_h}[face];"
        # Stack: gameplay on top, face on bottom
        f"[bg][gameplay]overlay=0:0[mid];"
        f"[mid][face]overlay=0:{gameplay_h}[v];"
        f"[0:a]volume=0.7[a]"
    )


def _build_filter(layout: str, face_region: str, src_w: int, src_h: int) -> str:
    if layout == "split":
        return _split_filter(face_region, src_w, src_h)
    return _gameplay_only_filter()


def _probe_dimensions(video_path: str) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path],
        capture_output=True, text=True, timeout=10,
    )
    parts = result.stdout.strip().split(",")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    return 1280, 720


def _load_account_config(account: str) -> dict:
    """Load compositor settings from account config yaml."""
    try:
        import yaml  # type: ignore
        cfg_path = os.path.join(PROJECT_ROOT, "config", "accounts", account, "account.yaml")
        with open(cfg_path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("compositor", {})
    except Exception:
        return {}


_VALID_REGIONS = set(_PIP_REGIONS.keys())

def _parse_face_region_from_filename(filename: str) -> str | None:
    """Extract face region from filename suffix.

    Supports two conventions:
      1. _fl-POSITION   e.g. ninjagaiden_fl-middle_left.mp4
      2. -POSITION      e.g. ultra_instinct-dbz-bottom_left.mp4

    Also accepts short codes: bl, br, bc, ml, mr, tl, tr, uml, c

    Returns the region string, or None to fall back to account config.
    """
    import re
    stem = os.path.splitext(os.path.basename(filename))[0]

    abbrevs = {
        "bl": "bottom_left", "br": "bottom_right", "bc": "bottom_center",
        "ml": "middle_left", "mr": "middle_right",
        "tl": "top_left",    "tr": "top_right",
        "uml": "upper_middle_left", "c": "center",
    }

    # Convention 1: _fl-<region>
    match = re.search(r'_fl-([a-z_]+)$', stem)
    if match:
        token = match.group(1)
        return token if token in _VALID_REGIONS else abbrevs.get(token)

    # Convention 2: -<region> at end of stem (match longest known region first)
    for region in sorted(_VALID_REGIONS, key=len, reverse=True):
        if stem.endswith(f"-{region}") or stem.endswith(f"_{region}"):
            return region
    # Also try short codes after - or _
    for code, region in abbrevs.items():
        if stem.endswith(f"-{code}") or stem.endswith(f"_{code}"):
            return region

    return None


def _resolve_layout(source_video: str, account: str) -> tuple[str, str]:
    """Return (layout, face_region) by merging filename override → account config.

    Priority: filename _fl- suffix > account config default.
    If filename specifies a face region, layout is forced to "split".
    """
    comp_cfg    = _load_account_config(account)
    acct_layout = comp_cfg.get("default_layout", "gameplay_only")
    acct_region = comp_cfg.get("face_region", "bottom_left")

    filename_region = _parse_face_region_from_filename(source_video)
    if filename_region:
        return "split", filename_region   # filename always forces split layout

    return acct_layout, acct_region


# ── Clip DB helpers ───────────────────────────────────────────────────────

def get_clips(video_id: str, start_idx: int, end_idx: int) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT clip_id, start_time, end_time FROM clips WHERE video_id=? ORDER BY start_time",
        (video_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows[start_idx:end_idx + 1]


def get_video_source(video_id: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT file_path FROM videos WHERE video_id=?", (video_id,))
    row = c.fetchone()
    conn.close()
    if row:
        # Translate stored session path → current session path if needed
        path = row[0]
        basename = os.path.basename(path)
        # Try to find the file under raw/ in case session path changed
        for root, _, files in os.walk(os.path.join(PROJECT_ROOT, "raw")):
            if basename in files:
                return os.path.join(root, basename)
        if os.path.exists(path):
            return path
    return ""


def get_output_dir(video_id: str) -> str:
    """Derive output clips dir from existing clip folders or DB."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT video_path FROM clips WHERE video_id=? AND video_path IS NOT NULL LIMIT 1", (video_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        # e.g. .../c9e10a40da590d0d_ultra_instinct-dbz/clips/shorts-1/clip.mp4
        return os.path.dirname(os.path.dirname(row[0]))  # → .../clips/
    # Fallback: scan output dirs
    import glob
    matches = glob.glob(os.path.join(PROJECT_ROOT, "output", "*", f"{video_id}_*", "clips"))
    if matches:
        return matches[0]
    matches = glob.glob(os.path.join(PROJECT_ROOT, "output", f"{video_id}_*", "clips"))
    if matches:
        return matches[0]
    return os.path.join(PROJECT_ROOT, "output", video_id, "clips")


def update_db(clip_id: str, video_path: str, thumb_path: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """UPDATE clips
           SET video_path=?,
               thumbnail_path=?,
               status=CASE WHEN status='failed' THEN 'generated' ELSE status END,
               error_message=CASE WHEN status='failed' THEN NULL ELSE error_message END,
               updated_at=CURRENT_TIMESTAMP
           WHERE clip_id=?""",
        (video_path, thumb_path, clip_id),
    )
    conn.commit()
    conn.close()


# ── Render ────────────────────────────────────────────────────────────────

def render_clip(
    clip_id: str,
    start_ms: float,
    end_ms: float,
    clip_num: int,
    source_video: str,
    output_clips_dir: str,
    filter_complex: str,
) -> tuple[str | None, str | None]:
    start_s    = start_ms / 1000.0
    duration_s = (end_ms - start_ms) / 1000.0

    clip_dir = os.path.join(output_clips_dir, f"shorts-{clip_num}")
    os.makedirs(clip_dir, exist_ok=True)

    output_path = os.path.join(clip_dir, "clip.mp4")
    thumb_path  = os.path.join(clip_dir, "thumbnail.jpg")

    print(f"  Rendering clip {clip_num}: {start_s:.0f}s–{start_s+duration_s:.0f}s → {output_path}")

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_s), "-t", str(duration_s),
        "-i", source_video,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-r", "30",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"  ERROR: FFmpeg failed:\n{result.stderr[-800:]}")
        return None, None

    # Thumbnail: frame from mid-clip
    mid_s = start_s + duration_s / 2
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(mid_s), "-i", source_video,
         "-vframes", "1", "-q:v", "2", thumb_path],
        capture_output=True, timeout=30,
    )

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"  ✓ {size_mb:.1f}MB")
    return output_path, thumb_path


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--batch", nargs=2, type=int, metavar=("START", "END"), required=True)
    parser.add_argument("--account", default=None,
                        help="Account name to load compositor config from (auto-detected if omitted)")
    args = parser.parse_args()

    # Auto-detect account from video source path
    account = args.account
    if not account:
        src = get_video_source(args.video_id)
        # raw/mrkimbum12/... → extract account name
        parts = src.replace("\\", "/").split("/")
        raw_idx = next((i for i, p in enumerate(parts) if p == "raw"), -1)
        if raw_idx >= 0 and raw_idx + 1 < len(parts):
            account = parts[raw_idx + 1]
        else:
            account = "mrkimbum12"  # fallback

    source_video = get_video_source(args.video_id)
    if not source_video:
        print(f"ERROR: could not find source video for {args.video_id}")
        sys.exit(1)

    # Default layout from account config
    comp_cfg    = _load_account_config(account)
    layout      = comp_cfg.get("default_layout", "gameplay_only")
    face_region = comp_cfg.get("face_region", "bottom_left")

    # Per-video override: _fl-POSITION suffix in filename takes priority
    filename_region = _parse_face_region_from_filename(source_video)
    if filename_region:
        face_region = filename_region
        layout      = "split"

    print(f"Layout: {layout}  |  face_region: {face_region}  |  source: {os.path.basename(source_video)}")

    output_clips_dir = get_output_dir(args.video_id)
    src_w, src_h = _probe_dimensions(source_video)
    filter_complex = _build_filter(layout, face_region, src_w, src_h)

    clips = get_clips(args.video_id, args.batch[0], args.batch[1])
    print(f"Processing {len(clips)} clip(s) (index {args.batch[0]}–{args.batch[1]})...")

    for i, (clip_id, start_ms, end_ms) in enumerate(clips):
        clip_num = args.batch[0] + i + 1
        video_path, thumb_path = render_clip(
            clip_id, start_ms, end_ms, clip_num,
            source_video, output_clips_dir, filter_complex,
        )
        if video_path:
            update_db(clip_id, video_path, thumb_path)

    print("Batch done.")


if __name__ == "__main__":
    main()
