#!/usr/bin/env python3
"""Render the standalone driving-directions PDF for a procedure location.

Reads the directions print template (`templates/{location}-directions-print.html`)
and the location's Google-Maps URL from `data/dosing.yaml`, generates a Maps QR
code via the same helpers render.py uses, substitutes it in by id, and writes
a single-page PDF.

Usage:
    .venv/bin/python scripts/render_directions.py --location scc
    .venv/bin/python scripts/render_directions.py --location pmch \
        --out ~/Desktop/pmch-directions.pdf
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
DOSING_PATH = SKILL_DIR / "data" / "dosing.yaml"

sys.path.insert(0, str(SKILL_DIR / "scripts"))
import render  # for _generate_maps_qr / _png_to_data_uri / _ensure_weasyprint_libpath

# Per-location template stem (the language suffix is appended at render time).
TEMPLATE_STEM_BY_LOCATION = {
    "scc":  "scc-directions-print",
    "pmch": "pmch-directions-print",
}
# Default output: <loc>-directions.pdf for English; <loc>-directions-es.pdf for Spanish.
OUT_DIR = Path.home() / "Desktop" / "peds-gi-system"


def _template_path(location_id: str, lang: str) -> Path:
    stem = TEMPLATE_STEM_BY_LOCATION[location_id]
    # English uses the bare stem (no .en suffix) for backwards compatibility.
    suffix = ".html" if lang == "en" else f".{lang}.html"
    return TEMPLATES / f"{stem}{suffix}"


def _default_out(location_id: str, lang: str) -> Path:
    suffix = "" if lang == "en" else f"-{lang}"
    return OUT_DIR / f"{location_id}-directions{suffix}.pdf"


def _maps_url_for(location_id: str, lang: str = "en") -> str:
    """Look up the Google-Maps URL from data/dosing.yaml."""
    with open(DOSING_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    locations = data.get("locations", {})
    if location_id not in locations:
        raise SystemExit(
            f"ERROR: location {location_id!r} not found in {DOSING_PATH} "
            f"(available: {list(locations.keys())})"
        )
    loc = locations[location_id]
    url = loc.get(f"maps_url_{lang}") or loc.get("maps_url_en")
    if not url:
        raise SystemExit(f"ERROR: no maps_url_en for location {location_id!r}")
    return url


def render_one(location_id: str, out_path: Path, lang: str = "en") -> Path:
    template_path = _template_path(location_id, lang)
    if not template_path.exists():
        raise SystemExit(
            f"ERROR: no directions template for {location_id!r} ({lang}) "
            f"(expected {template_path})"
        )

    maps_url = _maps_url_for(location_id, lang)
    qr_bytes = render._generate_maps_qr(maps_url)
    data_uri = render._png_to_data_uri(qr_bytes)

    html = template_path.read_text(encoding="utf-8")
    html = re.sub(
        r'<img id="qr-maps"[^>]*>',
        f'<img id="qr-maps" src="{data_uri}" alt="Google Maps QR">',
        html, count=1,
    )

    render._ensure_weasyprint_libpath()
    from weasyprint import HTML
    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(template_path.parent)).write_pdf(str(out_path))
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--location", choices=["scc", "pmch", "all"], default="all",
                    help="Procedure location (default: all)")
    ap.add_argument("--lang", choices=["en", "es", "both"], default="both",
                    help="Language (default: both)")
    ap.add_argument("--out", help="Output PDF path. "
                    "Default: ~/Desktop/peds-gi-system/{location}-directions[-es].pdf. "
                    "Only used when --location and --lang are single values.")
    args = ap.parse_args()

    targets = ["scc", "pmch"] if args.location == "all" else [args.location]
    langs = ["en", "es"] if args.lang == "both" else [args.lang]
    single = args.location != "all" and args.lang != "both"

    written = []
    for loc in targets:
        for lang in langs:
            if args.out and single:
                out = Path(args.out).expanduser()
            else:
                out = _default_out(loc, lang)
            path = render_one(loc, out, lang)
            print(f"  wrote {path}")
            written.append(path)

    print(f"\n{len(written)} directions PDF(s) written.")


if __name__ == "__main__":
    main()
