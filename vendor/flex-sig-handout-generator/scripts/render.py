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

# Reproducible PDFs: fontTools stamps head.modified with the current time into
# every font subset, so otherwise-identical renders differ inside a compressed
# stream. SOURCE_DATE_EPOCH (honored by fontTools) pins it; external value wins.
os.environ.setdefault("SOURCE_DATE_EPOCH", "0")

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
# Weight-band lb display — DERIVED from kg cutpoints (CR-1). Mirrors the
# identical helpers in the bowel-prep-generator skill; flex-sig is an
# independent 3-band set, so it carries its own copy rather than importing
# across skills. Each band in procedure.yaml declares a half-open kg interval
# [kg_lo, kg_hi); lb labels derive from it so adjacent bands can't gap.
# ---------------------------------------------------------------------------

LB_PER_KG = 2.20462


def _kg_to_lb(kg):
    """Round a kg weight to whole pounds (used only for display labels)."""
    return int(round(kg * LB_PER_KG))


def lb_bounds(band):
    """Inclusive whole-pound range a band covers, derived from its kg cutpoints.

    Returns (lo, hi) where either may be None for an open end:
      lo is None  -> open-low  (kg_lo is 0/None)
      hi is None  -> open-high (kg_hi is None)
    """
    kg_lo = band.get("kg_lo")
    kg_hi = band.get("kg_hi")
    lo = _kg_to_lb(kg_lo) if kg_lo else None
    hi = (_kg_to_lb(kg_hi) - 1) if kg_hi is not None else None
    return lo, hi


def lb_phrase(band, lang="en", style="plain"):
    """Localized lb label for a band, derived from its kg cutpoints.

    styles:
      "plain"   -> "33–45 lb" / "Under 33 lb" / "Over 111 lb"   (apex, flex-sig)
      "bracket" -> "[33–45 lb]" / "[Under 33 lb]" / "[Over 111 lb]"  (mobile BAND_LB)
      "plus"    -> "(112+ lb)"   (open-high only; CLENPIQ / SUPREP BAND_LB cells)

    Spanish swaps the open-end words: "Menos de N lb" / "Más de N lb".
    The range form is language-neutral (digits + en dash + "lb").
    """
    lo, hi = lb_bounds(band)
    en = lang == "en"
    if style == "plus":
        if lo is None:
            raise ValueError(f"lb_phrase style 'plus' needs an open-high band, got {band.get('id')!r}")
        return f"({lo}+ lb)"
    if lo is None and hi is not None:
        core = f"Under {hi + 1} lb" if en else f"Menos de {hi + 1} lb"
    elif hi is None and lo is not None:
        core = f"Over {lo - 1} lb" if en else f"Más de {lo - 1} lb"
    elif lo is not None and hi is not None:
        core = f"{lo}–{hi} lb"  # en dash
    else:
        raise ValueError(f"band {band.get('id')!r} has no kg cutpoints to derive lb from")
    if style == "bracket":
        return f"[{core}]"
    if style == "plain":
        return core
    raise ValueError(f"unknown lb_phrase style {style!r}")


def select_band(weight_kg, bands):
    """Return the first band whose half-open [kg_lo, kg_hi) contains weight_kg.

    The canonical kg-only binning primitive: kg is the unit of selection, and a
    contiguous partition guarantees exactly one interval matches every weight.
    Used by validate.py / the test sweep (no UX wires weight entry today), so it
    intentionally ignores protocol/variant — multiple bands may share an
    interval (e.g. under-15 + under-15-enema) and the first is returned.
    """
    for b in bands:
        lo = b.get("kg_lo") or 0
        hi = b.get("kg_hi")
        if weight_kg >= lo and (hi is None or weight_kg < hi):
            return b
    raise ValueError(f"no band contains weight_kg={weight_kg}")

# Shared design tokens + feedback-cell layout. Auto-prepended to every
# template's <head> so future cross-skill style changes (color tokens,
# font stack, feedback CTA layout) live in ONE file. Templates' own
# <style> blocks still load AFTER and win on override.
_SHARED_PRINT_CSS_PATH = Path.home() / "peds-gi-prep-system" / "shared" / "print-base.css"
try:
    _SHARED_PRINT_CSS = _SHARED_PRINT_CSS_PATH.read_text(encoding="utf-8") if _SHARED_PRINT_CSS_PATH.exists() else ""
except OSError:
    _SHARED_PRINT_CSS = ""


def _inject_shared_print_css(html: str) -> str:
    if not _SHARED_PRINT_CSS:
        return html
    return html.replace("<head>", f"<head>\n<style>{_SHARED_PRINT_CSS}</style>", 1)


# Cross-skill shared partials (footer/legal, feedback bar, NPO table) live in the
# meta repo so a one-line edit propagates to every skill. The backend re-points
# _SHARED_PARTIALS_DIR to its vendored copy (vendor/shared/partials).
_SHARED_PARTIALS_DIR = Path.home() / "peds-gi-prep-system" / "shared" / "partials"
_SHARED_PARTIALS_CACHE = {}  # {lang: {token: content}}


def _load_shared_partials(lang):
    """Read shared/partials/_*.<lang>.html → {{PARTIAL_<UPPER>}}: content.
    Cached per-language. Returns {} when the dir is absent, so the loader is
    inert until shared partials exist."""
    if lang in _SHARED_PARTIALS_CACHE:
        return _SHARED_PARTIALS_CACHE[lang]
    out = {}
    if _SHARED_PARTIALS_DIR.is_dir():
        suffix = f".{lang}.html"
        for p in sorted(_SHARED_PARTIALS_DIR.glob(f"_*{suffix}")):
            name = p.name[1:-len(suffix)]
            token = "{{PARTIAL_" + name.upper() + "}}"
            out[token] = p.read_text(encoding="utf-8")
    _SHARED_PARTIALS_CACHE[lang] = out
    return out


# Calm theme — replace the template's own <style> with the shared Calm
# stylesheet (calm-print.css) + calm-egd.css (the NPO-table classes flex-sig
# shares with EGD). Mirrors the bowel-prep skill's _swap_calm_style.
_CALM_PRINT_CSS_PATH = Path.home() / "peds-gi-prep-system" / "shared" / "calm-print.css"
_CALM_EGD_CSS_PATH = Path.home() / "peds-gi-prep-system" / "shared" / "calm-egd.css"
try:
    _CALM_PRINT_CSS = _CALM_PRINT_CSS_PATH.read_text(encoding="utf-8") if _CALM_PRINT_CSS_PATH.exists() else ""
    _CALM_EGD_CSS = _CALM_EGD_CSS_PATH.read_text(encoding="utf-8") if _CALM_EGD_CSS_PATH.exists() else ""
except OSError:
    _CALM_PRINT_CSS = _CALM_EGD_CSS = ""


def _swap_calm_style(html: str) -> str:
    """Replace the template's first <style>…</style> with the Calm CSS, run on
    the raw template before token substitution so the Calm CSS's
    {{PRACTICE_FOOTER}}/{{BAND_LABEL}} tokens resolve in the normal pass."""
    if not _CALM_PRINT_CSS:
        return html
    css = _CALM_PRINT_CSS + "\n" + _CALM_EGD_CSS
    return re.sub(r"<style>.*?</style>",
                  lambda _: f"<style>\n{css}\n</style>",
                  html, count=1, flags=re.S)


# Shared WCAG 2.1 AA base for the MOBILE renders (focus, skip link, contrast,
# keyboard/ARIA). One source for every current and future mobile site, sibling
# to print-base.css above. See ~/peds-gi-prep-system/shared/mobile-base.css +
# mobile-a11y.js. Kept byte-identical across the giready skills.
_SHARED_DIR = Path.home() / "peds-gi-prep-system" / "shared"
try:
    _SHARED_MOBILE_CSS = (_SHARED_DIR / "mobile-base.css").read_text(encoding="utf-8")
except OSError:
    _SHARED_MOBILE_CSS = ""
try:
    _SHARED_MOBILE_JS = (_SHARED_DIR / "mobile-a11y.js").read_text(encoding="utf-8")
except OSError:
    _SHARED_MOBILE_JS = ""
try:
    _SHARED_MOBILE_TOKENS = (_SHARED_DIR / "mobile-tokens.css").read_text(encoding="utf-8")
except OSError:
    _SHARED_MOBILE_TOKENS = ""


def _inject_landmarks(html: str) -> str:
    """Promote the mobile page chrome to ARIA/HTML5 landmark regions.

    Kept byte-identical with bowel-prep-generator/scripts/render.py. Idempotent;
    anchors are uniform across every mobile template (one .topbar, .container,
    .footer, .medical-disclaimer aside per page):
      - <div class="topbar">…</div>  -> <header class="topbar">…</header>  (banner)
      - the .container body content   -> wrapped in <main>                 (main)
      - .footer + copyright + policy nav + disclaimer -> wrapped in <footer> (contentinfo)
    The inner .topbar/.footer divs keep their class (hence their CSS), so only
    the element semantics change — the render is visually inert.
    """
    if "<main" in html or 'class="site-footer"' in html:
        return html  # idempotent: landmarks already present
    html = re.sub(
        r'<div class="topbar">(.*?)</div>(\s*)</div>',
        r'<header class="topbar">\1</div>\2</header>',
        html, count=1, flags=re.S,
    )
    html = html.replace(
        '<div class="container">',
        '<div class="container">\n<main>', 1,
    )
    html = html.replace(
        '<div class="footer">',
        '</main>\n<footer class="site-footer">\n<div class="footer">', 1,
    )
    html = re.sub(
        r'(<aside class="medical-disclaimer".*?</aside>)',
        r'\1\n</footer>',
        html, count=1, flags=re.S,
    )
    return html


def _inject_shared_mobile_a11y(html: str) -> str:
    """Add the shared a11y base (CSS + skip link + enhancement JS) to a mobile
    HTML render. Idempotent; each step no-ops if its anchor is absent."""
    if "a11y-skip" in html or "mobile-a11y" in html:
        return html
    if "<body>" in html and re.search(r"<h1(?![^>]*\bid=)", html):
        html = re.sub(r"<h1(?![^>]*\bid=)", '<h1 id="gi-main" tabindex="-1"', html, count=1)
        skip = '<a class="a11y-skip" href="#gi-main">Skip to main content</a>'
        html = html.replace("<body>", f"<body>\n{skip}", 1)
    html = _inject_landmarks(html)
    # Calm design tokens (light): prepend as the FIRST <style> after <head> so the
    # template's own <style> and mobile-base.css (injected last) still win. Gated on
    # the /*GI-MOBILE-TOKENS*/ marker the token-extraction left in each handout
    # template's <style> — scopes injection to the handout mobile pages, leaving
    # doses/qrg (no marker) untouched.
    if _SHARED_MOBILE_TOKENS and "<head>" in html and "/*GI-MOBILE-TOKENS*/" in html:
        html = html.replace("<head>", f"<head>\n<style>{_SHARED_MOBILE_TOKENS}</style>", 1)
    if _SHARED_MOBILE_CSS and "</head>" in html:
        html = html.replace("</head>", f"<style>{_SHARED_MOBILE_CSS}</style>\n</head>", 1)
    if _SHARED_MOBILE_JS and "</body>" in html:
        html = html.replace("</body>", f"<script>{_SHARED_MOBILE_JS}</script>\n</body>", 1)
    return html


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
_PRACTICE_CACHE = None
_PROCEDURE_CACHE = None


def _shared_dir():
    """Resolve the shared/ dir: vendored (vendor/shared, backend image) first,
    then the local meta-repo checkout."""
    for c in (SKILL_DIR.parent / "shared",
              Path.home() / "peds-gi-prep-system" / "shared"):
        if c.is_dir():
            return c
    return None


def _deep_merge_under(base, overlay):
    """Return `overlay` merged on top of `base` (overlay wins); recurse dicts."""
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = _deep_merge_under(out[k], v)
        else:
            out[k] = v
    return out


def _practice():
    global _PRACTICE_CACHE
    if _PRACTICE_CACHE is None:
        if not PRACTICE_PATH.exists():
            raise RuntimeError(f"practice.yaml not found at {PRACTICE_PATH}")
        with open(PRACTICE_PATH, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        sd = _shared_dir()
        core_path = (sd / "practice-core.yaml") if sd else None
        if core_path and core_path.exists():
            with open(core_path, encoding="utf-8") as f:
                core = yaml.safe_load(f) or {}
            local = _deep_merge_under(core, local)
        _PRACTICE_CACHE = local
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
        "{{DISCLAIMER}}":            p.get(f"disclaimer_{lang}") or p.get("disclaimer_en") or "",
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
        # Phase 2: meds.giready.com QR shown inside the Medications callout.
        "{{MEDS_GIREADY_QR}}":   _meds_giready_qr_data_uri(),
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
    # [A-Z0-9_]+ (digits allowed) so flags like INCLUDE_GLP1 actually gate — with
    # [A-Z_]+ the block never matched and the GLP-1 warning leaked onto every band.
    r"<!--IF:([A-Z0-9_]+)-->(.*?)<!--ENDIF:\1-->",
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


_MEDS_GIREADY_QR_DATA_URI_CACHE = None


def _meds_giready_qr_data_uri():
    """Return the meds.giready.com QR as a data URI. Constant per process —
    URL doesn't change per band/location/lang — cached after first generation.
    Used inside the Medications callout on the mobile + print handouts."""
    global _MEDS_GIREADY_QR_DATA_URI_CACHE
    if _MEDS_GIREADY_QR_DATA_URI_CACHE is not None:
        return _MEDS_GIREADY_QR_DATA_URI_CACHE
    url = _qr_target("meds_giready_url")
    _MEDS_GIREADY_QR_DATA_URI_CACHE = _png_to_data_uri(_generate_qr(url, size_px=150))
    return _MEDS_GIREADY_QR_DATA_URI_CACHE


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

    # Calm theme: swap the template's own <style> for the shared Calm stylesheet
    # (before substitution, so calm-print.css's {{PRACTICE_FOOTER}}/{{BAND_LABEL}}
    # tokens resolve in the normal pass).
    if theme == "calm":
        html = _swap_calm_style(html)

    # Apply per-band conditional blocks BEFORE token substitution so we don't
    # leave orphan tokens behind (and so the unreplaced-token check is honest).
    flags = {
        "SIMPLE_DIET": bool(band.get("simple_diet")),
        "FULL_DIET":   not bool(band.get("simple_diet")),
        "INFANT_CALLOUT": bool(band.get("infant_callout")),
        "INCLUDE_GLP1": bool(band.get("include_glp1_warning")),
    }
    html = apply_conditional_blocks(html, flags)

    # No flex-sig-specific mobile site yet → mobile_url stays empty and the
    # template hides the cover-mobile QR via an IF:HAS_MOBILE block (we don't
    # currently render that block — we just drop the cover-mobile QR by leaving
    # mobile_url empty and relying on the template's :empty styling).
    sub = (location or {}).get("mobile_subdomain", "") or ""
    mobile_url = (f"https://{sub}.giready.com/" + ("es/" if lang == "es" else "")) if sub else ""
    # ?feedback=1 auto-opens survey.js; &source=print swaps q3 to the
    # print-vs-phone question and tags the D1 row.
    feedback_url = (mobile_url + "?feedback=1&source=print") if mobile_url else ""
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""
    youtube_url = _qr_target("youtube_url_es" if lang == "es" else "youtube_url_en")
    portal_url = _qr_target("portal_url")
    gikids_url = _qr_target("gikids_url")
    location_phone_tel = re.sub(r"\D", "", location.get("phone", ""))

    # The cover (`qr-mobile`) and mid-doc (`qr-feedback`) both encode the
    # ?feedback=1 URL so families who scan either get the survey modal.
    qr_uris = {
        "qr-mobile":   _png_to_data_uri(_generate_qr(feedback_url, size_px=150)) if feedback_url else "",
        "qr-feedback": _png_to_data_uri(_generate_qr(feedback_url, size_px=120)) if feedback_url else "",
        "qr-maps":     _png_to_data_uri(_generate_qr(maps_url)) if maps_url else "",
        "qr-youtube":  _png_to_data_uri(_generate_qr(youtube_url)) if youtube_url else "",
        "qr-portal":   _png_to_data_uri(_generate_qr(portal_url)) if portal_url else "",
        "qr-gikids":   _png_to_data_uri(_generate_qr(gikids_url)) if gikids_url else "",
    }

    replacements = {
        **build_practice_placeholders(lang),
        **build_location_placeholders(location, lang),
        **build_band_placeholders(procedure, band, lang, location=location),
        # MOBILE_URL is the clickable href on the cover-QR anchor; keep it
        # in lockstep with the QR PNG so click and scan land in the same
        # place (mobile page + auto-opened survey, tagged source=print).
        "{{MOBILE_URL}}":         feedback_url or mobile_url,
        "{{FEEDBACK_URL}}":       feedback_url,
        "{{MAPS_URL}}":            maps_url,
        "{{YOUTUBE_URL}}":         youtube_url,
        "{{PORTAL_URL}}":          portal_url,
        "{{GIKIDS_URL}}":          gikids_url,
        "{{LOCATION_PHONE_TEL}}":  location_phone_tel,
    }
    # Expand shared partials FIRST so any tokens they introduce (e.g.
    # {{FEEDBACK_URL}} inside the shared feedback bar) are resolved by the
    # regular pass below. Inert until shared/partials/ exists.
    for token, value in _load_shared_partials(lang).items():
        html = html.replace(token, value)
    for token, value in replacements.items():
        html = html.replace(token, value)

    html = _inject_qr_into_imgs(html, qr_uris)

    if theme and theme != "color":
        html = re.sub(r'<body\b([^>]*)>', rf'<body\1 class="theme-{theme}">', html, count=1)

    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders in {out_path.name}: {sorted(set(unreplaced))}")

    html = _inject_shared_print_css(html)

    _ensure_weasyprint_libpath()
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "WeasyPrint failed to import. On macOS this usually means Pango/Cairo "
            "are missing — install with `brew install pango`. Original: " + repr(e)
        )
    from pdf_tagging import write_pdf_tagged
    write_pdf_tagged(HTML(string=html, base_url=str(template_path.parent)), str(out_path))


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
    ap.add_argument("--theme", default="color", choices=["color", "print-light", "calm", "both"])
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
