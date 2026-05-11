#!/usr/bin/env python3
"""
Render flexible-sigmoidoscopy bowel-prep handout PDFs from procedure.yaml +
practice.yaml.

Usage:
    python render.py --out <output_dir>
    python render.py --out ~/Desktop/test --location pmch --lang en
    python render.py --out ~/Desktop/test --band over-40kg --lang both --location all

Design:
- procedure.yaml is the clinical/operational source of truth (weight bands,
  enema dosing, locations, NPO).
- practice.yaml holds branding, contact, QR target URLs.
- templates/flex-sig-print.{lang}.html is the single print HTML — driven through
  WeasyPrint to produce the PDF for each (band, location, language) combination.
- DOCX rendering is intentionally NOT exposed via the CLI (PDF-only handout per
  user request). The bowel-prep-generator skill retains that path; if it's
  needed here later, port `render_docx` over.
- Mobile HTML is also NOT generated here (no flex-sig mobile site exists yet).
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

SKILL_DIR      = Path(__file__).resolve().parent.parent
TEMPLATES      = SKILL_DIR / "templates"
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


def build_band_placeholders(procedure, band, lang, location=None):
    """Per-band placeholders. The simpler vs. full diet content lives in the
    template and is gated on the SIMPLE_DIET flag (`{{SIMPLE_DIET_BLOCK_*}}`
    sections — see template for marker comments).

    NPO clear-liquids cutoff is per-location: when the location's
    `clear_hours` field is set (see procedure.yaml), it overrides the
    procedure-level default. Other NPO values are not location-specific.
    """
    npo = procedure.get("npo", {})
    clear_hours = (location or {}).get("clear_hours", npo.get("clear_hours", 2))
    drink_cup = procedure.get(f"drink_cup_{lang}", procedure.get("drink_cup_en", "1 cup (8 oz)"))
    return {
        "{{HTML_TITLE}}":        band.get(f"html_title_{lang}", band.get("html_title_en", "")),
        "{{BAND_LABEL}}":        band.get(f"label_{lang}", band.get("label_en", "")),
        "{{PROCEDURE_LABEL}}":   procedure.get(f"label_{lang}", procedure.get("label_en", "")),
        "{{ENEMA_TEXT}}":        band.get(f"enema_text_{lang}", band.get("enema_text_en", "")).strip(),
        "{{SHOPPING_TEXT}}":     band.get(f"shopping_text_{lang}", band.get("shopping_text_en", "")).strip(),
        "{{INFANT_WARNING}}":    band.get(f"infant_warning_{lang}", band.get("infant_warning_en", "")).strip(),
        "{{NPO_SOLID}}":         str(npo.get("solid_hours", 8)),
        "{{NPO_FORMULA}}":       str(npo.get("formula_hours", 6)),
        "{{NPO_BREASTMILK}}":    str(npo.get("breastmilk_hours", 4)),
        "{{NPO_CLEAR}}":         str(clear_hours),
        "{{DRINK_CUP}}":         drink_cup,
    }


# ---------------------------------------------------------------------------
# Conditional template-block rendering
# ---------------------------------------------------------------------------
# The print template includes:
#   <!--IF:SIMPLE_DIET-->...<!--ENDIF:SIMPLE_DIET-->
#   <!--IF:FULL_DIET-->...<!--ENDIF:FULL_DIET-->
#   <!--IF:INFANT_CALLOUT-->...<!--ENDIF:INFANT_CALLOUT-->
# The renderer keeps OR strips each block based on the band's flags.

_IF_PAT = re.compile(
    r"<!--IF:([A-Z_]+)-->(.*?)<!--ENDIF:\1-->",
    re.DOTALL,
)


def apply_conditional_blocks(html, flags):
    """Keep block contents when the named flag is true; otherwise drop the
    whole block. `flags` is a dict {flag_name: bool}."""
    def _sub(m):
        name = m.group(1)
        body = m.group(2)
        return body if flags.get(name) else ""
    return _IF_PAT.sub(_sub, html)


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
    candidates = ["/opt/homebrew/lib", "/usr/local/lib"]
    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    parts = existing.split(":") if existing else []
    for c in candidates:
        if Path(c).is_dir() and c not in parts:
            parts.append(c)
    if parts:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(parts)


def render_pdf(procedure_id, procedure, band, location, location_id, lang, theme, out_path):
    template_path = TEMPLATES / f"{procedure_id}-print.{lang}.html"
    if not template_path.exists():
        raise RuntimeError(f"Template not found: {template_path}")

    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    # Apply per-band conditional blocks BEFORE token substitution so we don't
    # leave orphan tokens behind (and so the unreplaced-token check is honest).
    flags = {
        "SIMPLE_DIET": bool(band.get("simple_diet")),
        "FULL_DIET":   not bool(band.get("simple_diet")),
        "INFANT_CALLOUT": bool(band.get("infant_callout")),
    }
    html = apply_conditional_blocks(html, flags)

    # No flex-sig-specific mobile site yet → mobile_url stays empty and the
    # template hides the cover-mobile QR via an IF:HAS_MOBILE block (we don't
    # currently render that block — we just drop the cover-mobile QR by leaving
    # mobile_url empty and relying on the template's :empty styling).
    sub = (location or {}).get("mobile_subdomain", "") or ""
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
        **build_band_placeholders(procedure, band, lang, location=location),
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


# ---------------------------------------------------------------------------
# Secret/internal: DOCX rendering is intentionally NOT exposed via CLI here.
# The bowel-prep-generator skill carries a working DOCX renderer; if you need
# editable DOCX output for flex-sig later, port `render_docx` from there.
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description="Render flexible-sigmoidoscopy bowel-prep PDFs.")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--procedure", default="flex-sig", help="Procedure id (default: flex-sig)")
    ap.add_argument("--band", default="all",
                    help="Band id (under-15kg | 20-40kg | over-40kg | all). Default: all")
    ap.add_argument("--location", default="all", help="scc | pmch | all")
    ap.add_argument("--lang", default="both", choices=["en", "es", "both"])
    ap.add_argument("--theme", default="color", choices=["color", "print-light", "both"])
    ap.add_argument("--flat", action="store_true",
                    help="Write all PDFs directly into --out instead of nesting "
                         "under <LOCATION>/<Language>/ subfolders")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = _procedure_data()
    procedures = data["procedures"]
    if args.procedure not in procedures:
        sys.exit(f"ERROR: procedure {args.procedure!r} not found. Available: {list(procedures.keys())}")
    procedure = procedures[args.procedure]
    bands_all = procedure.get("bands", [])
    if args.band != "all":
        bands = [b for b in bands_all if b["id"] == args.band]
        if not bands:
            sys.exit(f"ERROR: band {args.band!r} not found. Available: {[b['id'] for b in bands_all]}")
    else:
        bands = bands_all

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
                for band in bands:
                    stem = band["filename_stem"]
                    if args.flat:
                        target_dir = out_dir
                    else:
                        lang_label = {"en": "English", "es": "Spanish"}[lang]
                        target_dir = out_dir / loc_suffix / lang_label
                        target_dir.mkdir(parents=True, exist_ok=True)
                    fname = f"{args.procedure}-{stem}-{loc_suffix}{lang_suffix}-print{theme_suffix}.pdf"
                    out = target_dir / fname
                    render_pdf(args.procedure, procedure, band, location, lid, lang, theme, out)
                    written.append(out)
                    print(f"  wrote {out}")

    print(f"\n{len(written)} file(s) written to {out_dir}")


if __name__ == "__main__":
    main()
