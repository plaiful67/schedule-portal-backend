#!/usr/bin/env python3
"""
Render EGD (Upper Endoscopy) handout PDFs from procedure.yaml + practice.yaml.

Usage:
    python render.py --out <output_dir>
    python render.py --out ~/Desktop/test --location pmch --lang en --theme color

Design:
- procedure.yaml is the clinical/operational source of truth (NPO timing,
  locations, mobile site).
- practice.yaml holds branding, contact, QR target URLs.
- templates/{procedure_id}-print.{lang}.html is the print HTML — rendered
  through WeasyPrint to produce the PDF.
- DOCX rendering is NOT wired in v1 (PDF + published website only). The
  scaffolding exists in the bowel-prep-generator skill if needed later.
"""

import argparse
import base64
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml\n")
    sys.exit(1)

SKILL_DIR     = Path(__file__).resolve().parent.parent
TEMPLATES     = SKILL_DIR / "templates"
PROCEDURE_PATH = SKILL_DIR / "data" / "procedure.yaml"
PRACTICE_PATH  = SKILL_DIR / "practice.yaml"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
_PRACTICE_CACHE = None
_PROCEDURE_CACHE = None


def _practice():
    global _PRACTICE_CACHE
    if _PRACTICE_CACHE is None:
        if not PRACTICE_PATH.exists():
            raise RuntimeError(f"practice.yaml not found at {PRACTICE_PATH}")
        with open(PRACTICE_PATH, encoding="utf-8") as f:
            _PRACTICE_CACHE = yaml.safe_load(f)
    return _PRACTICE_CACHE


def _procedure_data():
    global _PROCEDURE_CACHE
    if _PROCEDURE_CACHE is None:
        if not PROCEDURE_PATH.exists():
            raise RuntimeError(f"procedure.yaml not found at {PROCEDURE_PATH}")
        with open(PROCEDURE_PATH, encoding="utf-8") as f:
            _PROCEDURE_CACHE = yaml.safe_load(f)
    return _PROCEDURE_CACHE


def _qr_target(key):
    return _practice()["qr_targets"][key]


# ---------------------------------------------------------------------------
# Placeholder builders
# ---------------------------------------------------------------------------
def build_practice_placeholders(lang):
    """{{PRACTICE_*}} placeholders sourced from practice.yaml."""
    p = _practice()["practice"]
    stack = p.get(f"cover_stack_{lang}") or p.get("cover_stack_en") or ["", "", ""]
    stack = (stack + ["", "", ""])[:3]
    return {
        "{{PRACTICE_STACK_LINE_1}}": stack[0],
        "{{PRACTICE_STACK_LINE_2}}": stack[1],
        "{{PRACTICE_STACK_LINE_3}}": stack[2],
        "{{PRACTICE_FOOTER}}":       p.get(f"footer_{lang}") or p.get("footer_en") or "",
        "{{PRACTICE_LOGO_FILE}}":    p.get("logo_filename", ""),
        "{{PRACTICE_LOGO_ALT}}":     p.get("logo_alt", ""),
    }


def build_location_placeholders(location, lang):
    if not location:
        return {}
    return {
        "{{LOCATION_NAME}}":         location.get(f"name_{lang}", location.get("name_en", "")),
        "{{LOCATION_ADDRESS}}":      location.get("address", ""),
        "{{LOCATION_PHONE}}":        location.get("phone", ""),
        "{{LOCATION_PHONE_LABEL}}":  location.get(f"phone_label_{lang}", location.get("phone_label_en", "")),
        "{{LOCATION_ARRIVAL}}":      location.get(f"arrival_{lang}", location.get("arrival_en", "")),
        "{{LOCATION_MAPS_URL}}":     location.get(f"maps_url_{lang}", location.get("maps_url_en", "")),
    }


def build_egd_placeholders(procedure, lang, location=None):
    """{{HTML_TITLE}}, {{PROCEDURE_LABEL}}, NPO timing strings, etc.

    NPO clear-liquids cutoff is per-location: when the location's
    `clear_hours` field is set (see procedure.yaml), it overrides the
    procedure-level default. Other NPO values are not location-specific.
    """
    npo = procedure.get("npo", {})
    title  = procedure.get(f"html_title_{lang}", procedure.get("html_title_en", ""))
    label  = procedure.get(f"label_{lang}", procedure.get("label_en", ""))
    clear_hours = (location or {}).get("clear_hours", npo.get("clear_hours", 2))
    return {
        "{{HTML_TITLE}}":         title,
        "{{PROCEDURE_LABEL}}":    label,
        "{{DURATION_MIN}}":       str(procedure.get("duration_min", "")),
        "{{NPO_SOLID}}":          str(npo.get("solid_hours", 8)),
        "{{NPO_FORMULA}}":        str(npo.get("formula_hours", 6)),
        "{{NPO_BREASTMILK}}":     str(npo.get("breastmilk_hours", 4)),
        "{{NPO_CLEAR}}":          str(clear_hours),
        "{{NPO_THICKENER}}":      str(npo.get("thickener_hours", 6)),
    }


# ---------------------------------------------------------------------------
# QR generation
# ---------------------------------------------------------------------------
def _generate_qr(url, size_px=246):
    try:
        import qrcode
        from PIL import Image
        import io as _io
    except ImportError:
        return None
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((size_px, size_px), Image.NEAREST)
    buf = _io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _png_to_data_uri(png_bytes):
    if not png_bytes:
        return ""
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def _inject_qr_into_imgs(html, qr_uris):
    def _swap(match, new_src):
        tag = match.group(0)
        return re.sub(r'\bsrc="[^"]*"', f'src="{new_src}"', tag, count=1)
    for qr_id, data_uri in qr_uris.items():
        if not data_uri:
            continue
        html = re.sub(
            r'<img\b[^>]*\bid="' + re.escape(qr_id) + r'"[^>]*>',
            lambda m, uri=data_uri: _swap(m, uri),
            html,
        )
    return html


# ---------------------------------------------------------------------------
# WeasyPrint plumbing
# ---------------------------------------------------------------------------
def _ensure_weasyprint_libpath():
    if sys.platform != "darwin":
        return
    homebrew_lib = "/opt/homebrew/lib"
    if os.path.isdir(homebrew_lib):
        cur = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if homebrew_lib not in cur.split(":"):
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (homebrew_lib + ":" + cur).rstrip(":")


def render_pdf(procedure_id, procedure, location, location_id, lang, theme, out_path):
    template_path = TEMPLATES / f"{procedure_id}-print.{lang}.html"
    if not template_path.exists():
        raise RuntimeError(f"Template not found: {template_path}")

    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    sub = location.get("mobile_subdomain", "") or _procedure_data().get("mobile_site", {}).get("subdomain", "")
    mobile_url = (f"https://{sub}.giready.com/" + ("es/" if lang == "es" else "")) if sub else ""
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""
    youtube_url = _qr_target("youtube_url_es" if lang == "es" else "youtube_url_en")
    portal_url = _qr_target("portal_url")
    gikids_url = _qr_target("gikids_url")
    location_phone_tel = re.sub(r"\D", "", location.get("phone", ""))

    qr_uris = {
        "qr-mobile":  _png_to_data_uri(_generate_qr(mobile_url, size_px=150)) if mobile_url else "",
        "qr-maps":    _png_to_data_uri(_generate_qr(maps_url)) if maps_url else "",
        "qr-youtube": _png_to_data_uri(_generate_qr(youtube_url)) if youtube_url else "",
        "qr-portal":  _png_to_data_uri(_generate_qr(portal_url)) if portal_url else "",
        "qr-gikids":  _png_to_data_uri(_generate_qr(gikids_url)) if gikids_url else "",
    }

    replacements = {
        **build_practice_placeholders(lang),
        **build_location_placeholders(location, lang),
        **build_egd_placeholders(procedure, lang, location=location),
        "{{MOBILE_URL}}":         mobile_url,
        "{{MAPS_URL}}":            maps_url,
        "{{YOUTUBE_URL}}":         youtube_url,
        "{{PORTAL_URL}}":          portal_url,
        "{{GIKIDS_URL}}":          gikids_url,
        "{{LOCATION_PHONE_TEL}}":  location_phone_tel,
    }
    for token, value in replacements.items():
        html = html.replace(token, value)

    html = _inject_qr_into_imgs(html, qr_uris)

    if theme and theme != "color":
        html = re.sub(r'<body\b([^>]*)>', rf'<body\1 class="theme-{theme}">', html, count=1)

    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders in {out_path.name}: {sorted(set(unreplaced))}")

    _ensure_weasyprint_libpath()
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "WeasyPrint failed to import. On macOS this usually means Pango/Cairo "
            "are missing — install with `brew install pango`. Original: " + repr(e)
        )
    HTML(string=html, base_url=str(template_path.parent)).write_pdf(str(out_path))


def main():
    ap = argparse.ArgumentParser(description="Render EGD handout PDFs.")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--procedure", default="egd", help="Procedure id (default: egd)")
    ap.add_argument("--location", default="all", help="scc | pmch | all")
    ap.add_argument("--lang", default="both", choices=["en", "es", "both"])
    ap.add_argument("--theme", default="color", choices=["color", "print-light", "both"])
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = _procedure_data()
    procedures = data["procedures"]
    if args.procedure not in procedures:
        sys.exit(f"ERROR: procedure {args.procedure!r} not found. Available: {list(procedures.keys())}")
    procedure = procedures[args.procedure]

    locations_data = data["locations"]
    location_ids = list(locations_data.keys()) if args.location == "all" else [args.location]
    for lid in location_ids:
        if lid not in locations_data:
            sys.exit(f"ERROR: location {lid!r} not found. Available: {list(locations_data.keys())}")

    langs  = ["en", "es"] if args.lang  == "both" else [args.lang]
    themes = ["color", "print-light"] if args.theme == "both" else [args.theme]

    written = []
    for lid in location_ids:
        location = locations_data[lid]
        loc_suffix = lid.upper()
        for lang in langs:
            lang_suffix = "" if lang == "en" else f"-{lang}"
            for theme in themes:
                theme_suffix = "" if theme == "color" else f"-{theme}"
                lang_label = {"en": "English", "es": "Spanish"}[lang]
                target_dir = out_dir / loc_suffix / lang_label
                target_dir.mkdir(parents=True, exist_ok=True)
                fname = f"{args.procedure}-{loc_suffix}{lang_suffix}-print{theme_suffix}.pdf"
                out = target_dir / fname
                render_pdf(args.procedure, procedure, location, lid, lang, theme, out)
                written.append(out)
                print(f"  wrote {out}")

    print(f"\n{len(written)} file(s) written to {out_dir}")


if __name__ == "__main__":
    main()
