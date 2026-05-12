#!/usr/bin/env python3
"""Shrink the directions-PDF map images.

Why: the four full-res map PNGs in templates/maps/ are 1.1–6.4 MB each, and
WeasyPrint embeds them mostly-as-is into the directions PDFs. The result is
4.2 MB (SCC) / 6.2 MB (PMCH) per directions PDF — heavy for the scheduler
portal flow that merges directions onto every /render response.

This script preserves the originals as `*-original.png` (gitignored) and
writes JPEG-encoded versions at a target max width with quality 85. The
JPEGs land alongside as `{name}.jpg`; the directions templates reference
the .jpg filenames. Rerunning is idempotent: it always reads from the
`-original.png` so changing the quality knob below and rerunning gives
deterministic output.

Usage:
    python scripts/shrink_map_images.py            # default: 1200px max width
    python scripts/shrink_map_images.py --max 1500 --quality 90
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

MAPS_DIR = Path(__file__).resolve().parent.parent / "templates" / "maps"


def shrink_one(src: Path, max_width: int, quality: int) -> tuple[int, int]:
    """Read src (always the -original.png), write {stem}.jpg next to it.
    Returns (input_bytes, output_bytes) for reporting."""
    out = src.with_name(src.name.removesuffix("-original.png") + ".jpg")
    with Image.open(src) as im:
        im = im.convert("RGB")  # JPEG can't carry alpha
        if im.width > max_width:
            ratio = max_width / im.width
            new_size = (max_width, int(round(im.height * ratio)))
            im = im.resize(new_size, Image.LANCZOS)
        im.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    return src.stat().st_size, out.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max", dest="max_width", type=int, default=1200,
                    help="Max output width in pixels (default 1200).")
    ap.add_argument("--quality", type=int, default=85,
                    help="JPEG quality 1-100 (default 85).")
    args = ap.parse_args()

    if not MAPS_DIR.is_dir():
        raise SystemExit(f"maps dir not found: {MAPS_DIR}")

    # First, snapshot any unsnapshotted PNG to *-original.png so the
    # script is reversible and reruns are idempotent.
    for png in sorted(MAPS_DIR.glob("*.png")):
        if png.stem.endswith("-original"):
            continue
        original = png.with_name(png.stem + "-original.png")
        if not original.exists():
            png.rename(original)
            print(f"snapshot {png.name} -> {original.name}")
        else:
            # The plain .png shouldn't coexist with its -original sibling.
            # Remove it so we always read from -original.
            if png.exists():
                png.unlink()
                print(f"removed stale {png.name} (using -original.png as source)")

    originals = sorted(MAPS_DIR.glob("*-original.png"))
    if not originals:
        print("nothing to do — no *-original.png files found.")
        return 0

    total_in = total_out = 0
    for src in originals:
        in_bytes, out_bytes = shrink_one(src, args.max_width, args.quality)
        total_in += in_bytes
        total_out += out_bytes
        out_name = src.name.removesuffix("-original.png") + ".jpg"
        pct = 100.0 * out_bytes / in_bytes if in_bytes else 0
        print(f"  {src.name:36s}  {in_bytes/1024:7.1f} KB  ->  "
              f"{out_name:32s}  {out_bytes/1024:7.1f} KB  ({pct:.1f}%)")

    pct_total = 100.0 * total_out / total_in if total_in else 0
    print(f"\ntotal: {total_in/1024:.0f} KB -> {total_out/1024:.0f} KB ({pct_total:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
