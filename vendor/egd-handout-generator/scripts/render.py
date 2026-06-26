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

# Reproducible PDFs: fontTools stamps head.modified with the current time into
# every font subset, so otherwise-identical renders differ inside a compressed
# stream. SOURCE_DATE_EPOCH (honored by fontTools) pins it; external value wins.
os.environ.setdefault("SOURCE_DATE_EPOCH", "0")

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml\n")
    sys.exit(1)

SKILL_DIR     = Path(__file__).resolve().parent.parent
TEMPLATES     = SKILL_DIR / "templates"
PROCEDURE_PATH = SKILL_DIR / "data" / "procedure.yaml"
PRACTICE_PATH  = SKILL_DIR / "practice.yaml"

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
# stylesheet (calm-print.css) + calm-egd.css (the EGD/flex-sig table classes
# calm-print.css doesn't carry). Mirrors the bowel-prep skill's _swap_calm_style.
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


def procedure_qr_target(procedure, key, lang=None):
    """Resolve a QR target URL with procedure-level override fallback.

    Procedures may override `youtube_url_<lang>` and `gikids_url` directly
    on the procedure block (used by the pH-MII variant, which sends families
    to a different YouTube video + GIKids reference page than the EGD-only
    handout). Falls back to the practice.yaml qr_targets dict.
    """
    if procedure:
        if lang:
            ov = procedure.get(f"{key}_{lang}")
            if ov:
                return ov
        ov = procedure.get(key)
        if ov:
            return ov
    full_key = f"{key}_{lang}" if lang else key
    return _qr_target(full_key)


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
        # EGD has no weight band. The Calm stylesheet's @page running header +
        # cover string-set reference {{BAND_LABEL}}; map it to the procedure
        # label so the swap resolves cleanly (no-op on the color/print-light
        # templates, which don't carry the token).
        "{{BAND_LABEL}}":         label,
        "{{DURATION_MIN}}":       str(procedure.get("duration_min", "")),
        "{{NPO_SOLID}}":          str(npo.get("solid_hours", 8)),
        "{{NPO_FORMULA}}":        str(npo.get("formula_hours", 6)),
        "{{NPO_BREASTMILK}}":     str(npo.get("breastmilk_hours", 4)),
        "{{NPO_CLEAR}}":          str(clear_hours),
        "{{NPO_THICKENER}}":      str(npo.get("thickener_hours", 6)),
        # Phase 2: meds.giready.com QR shown inside the Medications callout
        # on every mobile HTML and print PDF.
        "{{MEDS_GIREADY_QR}}":    _meds_giready_qr_data_uri(),
    }


def _med_stops_for(procedure_id):
    """Filter `medication_stops:` entries that apply to `procedure_id`."""
    entries = _procedure_data().get("medication_stops") or []
    return [e for e in entries if procedure_id in (e.get("consumed_by") or [])]


def _format_stop_days(days, lang):
    if lang == "es":
        return f"{days} días antes"
    return f"{days} days before"


# Short-form weekday + month names, duplicated from schedule-portal-backend's
# personalization.py so the skill stays self-contained but produces the same
# format used elsewhere in the personalized handout ("Wed, May 28" / "mié, 28 may").
_EN_WEEKDAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_ES_WEEKDAYS_SHORT = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_EN_MONTHS_SHORT   = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_ES_MONTHS_SHORT   = ["", "ene", "feb", "mar", "abr", "may", "jun",
                      "jul", "ago", "sep", "oct", "nov", "dic"]


def _format_short_date(d, lang):
    """Match `personalization.format_appt_date_short` exactly so dates inside
    the med-stop table align with dates the portal stamps into headings."""
    if lang == "es":
        return f"{_ES_WEEKDAYS_SHORT[d.weekday()]}, {d.day} {_ES_MONTHS_SHORT[d.month]}"
    return f"{_EN_WEEKDAYS_SHORT[d.weekday()]}, {_EN_MONTHS_SHORT[d.month]} {d.day}"


def build_egdph_placeholders(procedure, lang, location=None, procedure_id="egdph", appt_dt=None):
    """Extends `build_egd_placeholders` with the pH-MII–specific tokens.

    {{MED_STOPS_TBODY}} renders the inner <tbody> rows of the medication-stop
    table — the surrounding <table> + styling stay in the template. Driven
    by the `medication_stops:` block in procedure.yaml so the schedule portal
    can reuse the same rules.

    When `appt_dt` is provided (personalized renders), each row appends a
    small second line under the "X days before" text showing the calendar
    date by which the family must stop (appt_date - stop_days). The public
    static handout doesn't know an appointment date so this line is omitted.
    """
    import datetime as _dt
    base = build_egd_placeholders(procedure, lang, location=location)
    rows = []
    for entry in _med_stops_for(procedure_id):
        label = entry.get(f"label_{lang}", entry.get("label_en", ""))
        examples = entry.get(f"examples_{lang}", entry.get("examples_en", ""))
        stop_days = int(entry.get("stop_days", 0))
        stop = _format_stop_days(stop_days, lang)
        date_line = ""
        if appt_dt is not None:
            target_date = appt_dt.date() - _dt.timedelta(days=stop_days)
            label_text = "antes del" if lang == "es" else "by"
            # Inline (<br>+<span>), never a block <div>: a block-level child mixed
            # with inline content in a <td> makes WeasyPrint emit an anonymous block
            # that its tagged-PDF writer renders as a nested TD-in-TD → PDF/UA clause
            # 7.2-9 ("TD shall be contained in TR"). Keeping the cell all-inline avoids it.
            date_line = (
                f"<br><span class=\"med-stop-date\">"
                f"{label_text} {_format_short_date(target_date, lang)}"
                f"</span>"
            )
        rows.append(
            "<tr>"
            f"<td><strong>{label}</strong>"
            f"<br><span class=\"med-examples\">{examples}</span></td>"
            f"<td class=\"col-stop\"><span class=\"stop-hours\">{stop}</span>{date_line}</td>"
            "</tr>"
        )
    base["{{MED_STOPS_TBODY}}"] = "\n".join(rows)
    return base


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
    URL doesn't change per location/lang — so cache after first generation.
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
    homebrew_lib = "/opt/homebrew/lib"
    if os.path.isdir(homebrew_lib):
        cur = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if homebrew_lib not in cur.split(":"):
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (homebrew_lib + ":" + cur).rstrip(":")


def render_pdf(procedure_id, procedure, location, location_id, lang, theme, out_path,
               add_ons=None, knob_picks=None):
    template_path = TEMPLATES / f"{procedure_id}-print.{lang}.html"
    if not template_path.exists():
        raise RuntimeError(f"Template not found: {template_path}")

    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    # Calm theme: swap the template's own <style> for the shared Calm
    # stylesheet (before substitution, so calm-print.css's
    # {{PRACTICE_FOOTER}}/{{BAND_LABEL}} tokens resolve in the loop below).
    if theme == "calm":
        html = _swap_calm_style(html)

    # Procedure-level `mobile_subdomain` (e.g. egdph) wins over the location's
    # default (e.g. egd86) so variant handouts point to their own subdomain.
    sub = procedure.get("mobile_subdomain") or location.get("mobile_subdomain", "") or _procedure_data().get("mobile_site", {}).get("subdomain", "")
    mobile_url = (f"https://{sub}.giready.com/" + ("es/" if lang == "es" else "")) if sub else ""
    # ?feedback=1 auto-opens survey.js; &source=print swaps q3 to the
    # print-vs-phone question and tags the D1 row.
    feedback_url = (mobile_url + "?feedback=1&source=print") if mobile_url else ""
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""
    youtube_url = procedure_qr_target(procedure, "youtube_url", lang)
    portal_url = _qr_target("portal_url")
    gikids_url = procedure_qr_target(procedure, "gikids_url")
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

    # Variant-specific placeholder builders: egdph adds {{MED_STOPS_TBODY}}.
    if procedure_id == "egdph":
        procedure_placeholders = build_egdph_placeholders(procedure, lang, location=location, procedure_id=procedure_id)
    else:
        procedure_placeholders = build_egd_placeholders(procedure, lang, location=location)

    # Composition overlay: assemble title + add-on blurbs from the registry.
    # Scoped to the plain-EGD base: egdph uses a different title (built by
    # build_egdph_placeholders) and its template has no {{ADDON_BLURBS}} slot.
    if procedure_id == "egd":
        import compose as _compose
        comp = _compose.compose("egd", add_ons or [], knob_picks or {}, lang)
        if add_ons:
            procedure_placeholders["{{HTML_TITLE}}"] = comp.title
            procedure_placeholders["{{PROCEDURE_LABEL}}"] = comp.title
        procedure_placeholders["{{ADDON_BLURBS}}"] = comp.blurbs_html

    replacements = {
        **build_practice_placeholders(lang),
        **build_location_placeholders(location, lang),
        **procedure_placeholders,
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


def main():
    ap = argparse.ArgumentParser(description="Render EGD handout PDFs.")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--procedure", default="egd", help="Procedure id (default: egd)")
    ap.add_argument("--location", default="all", help="scc | pmch | all")
    ap.add_argument("--lang", default="both", choices=["en", "es", "both"])
    ap.add_argument("--theme", default="color", choices=["color", "print-light", "calm", "both"])
    ap.add_argument("--add-ons", default="", help="comma-separated add-on ids, e.g. bal,dise")
    ap.add_argument("--ppi", default=None, choices=["hold", "continue"], help="PPI knob pick")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = _procedure_data()
    procedures = data["procedures"]
    if args.procedure not in procedures:
        sys.exit(f"ERROR: procedure {args.procedure!r} not found. Available: {list(procedures.keys())}")
    procedure = procedures[args.procedure]

    locations_data = data["locations"]
    if args.location == "all":
        # Honor optional per-procedure `locations:` allowlist (e.g. egdph → pmch only).
        allowed = procedure.get("locations") or list(locations_data.keys())
        location_ids = [lid for lid in locations_data.keys() if lid in allowed]
    else:
        location_ids = [args.location]
    for lid in location_ids:
        if lid not in locations_data:
            sys.exit(f"ERROR: location {lid!r} not found. Available: {list(locations_data.keys())}")

    langs  = ["en", "es"] if args.lang  == "both" else [args.lang]
    themes = ["color", "print-light"] if args.theme == "both" else [args.theme]

    add_ons = [a for a in args.add_ons.split(",") if a]
    knob_picks = {"ppi_handling": args.ppi} if args.ppi else {}

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
                render_pdf(args.procedure, procedure, location, lid, lang, theme, out,
                           add_ons=add_ons, knob_picks=knob_picks)
                written.append(out)
                print(f"  wrote {out}")

    print(f"\n{len(written)} file(s) written to {out_dir}")


if __name__ == "__main__":
    main()
