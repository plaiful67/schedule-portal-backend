#!/usr/bin/env python3
"""
Render bowel prep handouts (HTML + DOCX, English + Spanish) from dosing.yaml.

Usage:
    python render.py --out <output_dir> [--band <id>] [--lang en|es|both] [--format html|docx|both]

Examples:
    # Regenerate everything for all bands and both languages
    python render.py --out ./outputs

    # Just the 31-40 kg band, both languages, both formats
    python render.py --out ./outputs --band 31-40

    # Only Spanish HTML for the <15 kg infant handout
    python render.py --out ./outputs --band under-15 --lang es --format html

Design:
- dosing.yaml is the single source of truth for dosing numbers and localized labels.
- templates/ contains one file per (protocol, language, format) combination with
  {{PLACEHOLDER}} tokens.
- This script computes the six target strings per (band, language) from structured
  numeric data and pre-written precleanout sentences, then substitutes them into
  the HTML template (plain text) or the DOCX template (document.xml inside the zip).
- Bands with protocol: infant use the infant templates, which have fewer
  placeholders since the infant handout contains no oral dosing.
"""

import argparse
import base64
import html as html_lib
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

# Reproducible PDFs: fontTools stamps head.modified with the current time into
# every font subset, so otherwise-identical renders differ inside a compressed
# stream. SOURCE_DATE_EPOCH (honored by fontTools) pins it; external value wins.
os.environ.setdefault("SOURCE_DATE_EPOCH", "0")

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml --break-system-packages\n")
    sys.exit(1)

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
DOSING_PATH = SKILL_DIR / "data" / "dosing.yaml"
PRACTICE_PATH = SKILL_DIR / "practice.yaml"

# Shared design tokens + feedback-cell layout. Auto-prepended to every
# template's <head> so future cross-skill style changes (color tokens,
# font stack, feedback CTA layout) live in ONE file. Templates' own
# <style> blocks still load AFTER and win on override. See
# ~/peds-gi-prep-system/shared/print-base.css for the source.
_SHARED_PRINT_CSS_PATH = Path.home() / "peds-gi-prep-system" / "shared" / "print-base.css"
try:
    _SHARED_PRINT_CSS = _SHARED_PRINT_CSS_PATH.read_text(encoding="utf-8") if _SHARED_PRINT_CSS_PATH.exists() else ""
except OSError:
    _SHARED_PRINT_CSS = ""


def _inject_shared_print_css(html: str) -> str:
    """Splice the shared print-base.css in as the first <style> after <head>."""
    if not _SHARED_PRINT_CSS:
        return html
    return html.replace("<head>", f"<head>\n<style>{_SHARED_PRINT_CSS}</style>", 1)


# The Calm print design lives in ONE shared stylesheet; for --theme calm we swap
# it in for the base template's own <style>, so every family gets the Calm look
# without a duplicated calm/ template. Keyed to the shared print class vocabulary.
_CALM_PRINT_CSS_PATH = Path.home() / "peds-gi-prep-system" / "shared" / "calm-print.css"
try:
    _CALM_PRINT_CSS = _CALM_PRINT_CSS_PATH.read_text(encoding="utf-8") if _CALM_PRINT_CSS_PATH.exists() else ""
except OSError:
    _CALM_PRINT_CSS = ""


def _swap_calm_style(html: str) -> str:
    """Replace the template's first <style>…</style> with the shared Calm CSS.
    Run on the raw template (before token substitution) so the Calm CSS's
    {{PRACTICE_FOOTER}} / {{BAND_LABEL}} tokens are resolved by the normal pass."""
    if not _CALM_PRINT_CSS:
        return html
    return re.sub(r"<style>.*?</style>",
                  lambda _: f"<style>\n{_CALM_PRINT_CSS}\n</style>",
                  html, count=1, flags=re.S)


# Shared accessibility (WCAG 2.1 AA) base for the MOBILE renders: focus
# visibility, skip link, muted-text contrast (CSS) + keyboard/ARIA semantics
# for the checklist, feedback FAB, and tables (JS). One source for every
# current and future mobile site, sibling to print-base.css above. See
# ~/peds-gi-prep-system/shared/mobile-base.css + mobile-a11y.js.
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

    Runs on the fully-rendered HTML at the shared a11y chokepoint. Idempotent.
    Anchors are uniform across every mobile template (one .topbar, one
    .container, one .footer, one .medical-disclaimer aside per page):
      - <div class="topbar">…</div>  -> <header class="topbar">…</header>  (banner)
      - the .container body content   -> wrapped in <main>                 (main)
      - .footer + copyright + policy nav + disclaimer -> wrapped in <footer> (contentinfo)
    The inner .topbar/.footer divs keep their class (hence their CSS), so only
    the element semantics change — the render is visually inert. Each step is a
    no-op if its anchor is absent, so atypical templates pass through untouched.
    """
    if "<main" in html or 'class="site-footer"' in html:
        return html  # idempotent: landmarks already present
    # banner — rename the topbar wrapper div to <header> (keep class + CSS).
    html = re.sub(
        r'<div class="topbar">(.*?)</div>(\s*)</div>',
        r'<header class="topbar">\1</div>\2</header>',
        html, count=1, flags=re.S,
    )
    # main — open just inside .container; closed just before the footer block.
    html = html.replace(
        '<div class="container">',
        '<div class="container">\n<main>', 1,
    )
    # contentinfo — close <main>, open the semantic <footer> before the address
    # block, and close it after the medical-disclaimer aside (the last footer
    # element on every page), so address + copyright + policy nav all land
    # inside one contentinfo landmark.
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
    """Add the shared a11y base to a mobile HTML render.

    - CSS appended as the LAST <style> (before </head>) so it wins on cascade.
    - A skip link is inserted as the first child of <body>, targeting the page
      <h1> (which gains id="gi-main" tabindex="-1").
    - The enhancement JS is appended just before </body>, after the template's
      own inline scripts have built the interactive checklist + FAB.
    Each step is a no-op if its anchor (</head>, <body>, <h1, </body>) is
    absent, so non-standard templates pass through untouched.
    """
    if "a11y-skip" in html or "mobile-a11y" in html:
        return html  # idempotent: already injected
    # Skip link FIRST, on the original markup — before CSS/JS injection adds any
    # text that could shadow the <h1> the regex targets. Only when there's an
    # id-less <h1> to land focus on.
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
# Phrasing — how structured dosing numbers become prose in each language.
# These are the ONLY places language-specific wording lives in code. Edit here
# if you want to change phrasing globally (e.g. "tablet" → "pill"). Per-band
# values live in dosing.yaml.
# ---------------------------------------------------------------------------

ML_PER_OZ = 29.5735
ML_ROUND = 50  # Round mL to nearest 50 mL — nobody measures 591 mL exactly.


def oz_to_ml(oz):
    """Convert fluid ounces to millilitres, rounded to the nearest ML_ROUND."""
    return int(round(oz * ML_PER_OZ / ML_ROUND) * ML_ROUND)


# ---------------------------------------------------------------------------
# Weight-band lb display — DERIVED from the kg cutpoints (CR-1).
#
# Each band declares a half-open kg interval [kg_lo, kg_hi) in dosing.yaml
# (kg_lo: 0 = open-low, kg_hi: null = open-high). The pound labels shown to
# parents/staff are computed from those cutpoints so adjacent bands can never
# gap or overlap. This is the SINGLE source for lb wording — render.py, the
# mobile build scripts, the apex landing, and the scheduler all route their lb
# strings through lb_phrase(); validate.py asserts the lb baked into
# dosing.yaml's own labels matches lb_bounds().
#
#   lb_lo = round(kg_lo * LB_PER_KG)            (the band's lb floor)
#   lb_hi = round(kg_hi * LB_PER_KG) - 1        (one below the next band's floor)
#
# so e.g. 41-50 = [41,51) -> 90..111 lb and over-50 = [51,null) -> 112+ lb,
# which is contiguous with 41-50's 111 by construction.
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


REMOVE_PARAGRAPH_MARKER = "__OMIT_PARAGRAPH__"


def _tablet_word(n, lang):
    if lang == "en":
        return "tablet" if n == 1 else "tablets"
    return "tableta" if n == 1 else "tabletas"


def _dulcolax_label_en(n):
    """Dulcolax tablet-noun label, agreeing with the count (tablet / tablets)."""
    return f"Dulcolax {_tablet_word(n, 'en')}"


def _dulcolax_label_es(n):
    """Spanish Dulcolax tablet-noun label with article+noun agreement."""
    return f"{'la' if n == 1 else 'las'} {_tablet_word(n, 'es')} de Dulcolax"


def _miralax_dose_phrase(band, lang):
    """The exact MiraLAX dose phrase printed in the BIG PREP time-box.

    Shared between build_strings (handout text) and build_calendar_events
    (calendar event description) so the two can never drift.
    """
    capfuls = band["miralax_capfuls"]
    grams = band["miralax_grams"]
    note = band.get(f"miralax_note_{lang}", "") or ""
    oz = band["gatorade_oz"]
    ml = oz_to_ml(oz)
    if lang == "en":
        return f"{capfuls} capfuls (~{grams} g{note}) of MiraLAX in {oz} oz (~{ml} mL) of Gatorade"
    return f"{capfuls} tapas (~{grams} g{note}) de MiraLAX en {oz} oz (~{ml} mL) de Gatorade"


def _shopping_totals(band):
    """Big-prep + rescue shopping totals for a standard-protocol band.

    The Plan-Ahead shopping row promises "enough for big prep with rescue",
    so totals cover the BIG PREP dose PLUS the full rescue plan (see
    build_contingency_block). Grams come from contingency_total_grams
    (17 g/cap) rather than caps*17 because the big-prep miralax_grams values
    round differently per band. Bands without contingency_* fields degrade
    to big-prep-only. Shared between build_strings and build_calendar_events.
    """
    capfuls = band["miralax_capfuls"]
    grams = band["miralax_grams"]
    oz = band["gatorade_oz"]
    rescue_caps = band.get("contingency_evening_caps", 0) + band.get("contingency_morning_caps", 0)
    caps = capfuls + rescue_caps
    grams_total = band.get("contingency_total_grams", grams) if rescue_caps else grams
    gatorade_oz = oz + band.get("contingency_evening_oz", 0) + band.get("contingency_morning_oz", 0)
    return {
        "caps": caps,
        "grams": grams_total,
        # Round powder oz to nearest whole number (28.35 g/oz) — patients
        # freak out at decimal places.
        "miralax_oz": round(grams_total / 28.35),
        "gatorade_oz": gatorade_oz,
        "gatorade_ml": oz_to_ml(gatorade_oz),
    }


# ---------------------------------------------------------------------------
# Pre-cleanout callout block (constipation / suspected constipation).
#
# Prior versions used a single sentence with the dose+volume range and a "for
# 3–5 days" tail, which led to decision fatigue. Per 2026-05-XX clinical
# guidance the callout now offers two concrete options per band where dose
# ranges allow (max-dose × min-duration vs min-dose × max-duration); bands
# with a fixed dose (41-50 kg) emit a single-option block. The personalize
# feature pivots each option's start date off the procedure date via the
# data-pz-day attribute already used elsewhere in the page.
# ---------------------------------------------------------------------------

def build_precleanout_block(band, lang):
    """Return the {{HTML_PRECLEANOUT_BLOCK}} markup for a standard-protocol band.

    Bands may declare 1 or 2 options:
      - Option A: required.  Fields: precleanout_a_text_{lang}, precleanout_a_offset_days
                 (optional label: precleanout_a_label_{lang}).
      - Option B: optional.  Same fields with `_b_` prefix; if text is empty the renderer
                 emits a single-option block.
    Per-band labels override the default "Option A / Option B" wording, allowing
    bands like 41-50 kg to use "Option A — 3-day course" / "Option B — weekend
    big-day + daily maintenance" instead.
    """
    a_text = (band.get(f"precleanout_a_text_{lang}") or "").strip()
    b_text = (band.get(f"precleanout_b_text_{lang}") or "").strip()
    a_off  = band.get("precleanout_a_offset_days")
    b_off  = band.get("precleanout_b_offset_days")
    a_lbl  = (band.get(f"precleanout_a_label_{lang}") or "").strip()
    b_lbl  = (band.get(f"precleanout_b_label_{lang}") or "").strip()
    maintenance = (band.get(f"precleanout_maintenance_{lang}") or "").strip()
    if not a_text:
        return ""

    if lang == "en":
        title  = "&#9888;&#65039; Any history or suspicion of constipation?"
        intro_two = ("<strong>If yes, start a pre-cleanout</strong> before the regular "
                     "prep timeline below:")
        intro_one = ("<strong>If yes, start a pre-cleanout</strong> before the regular "
                     "prep timeline below:")
        date_tmpl = " &mdash; <em>start on {date}</em>"
        outro = "Call or message the office with any questions."
    else:
        title  = "&#9888;&#65039; ¿Antecedente o sospecha de estreñimiento?"
        intro_two = ("<strong>Si la respuesta es sí, comience una pre-limpieza</strong> antes "
                     "del cronograma regular de preparación. Elija la opción que mejor se "
                     "ajuste a su rutina:")
        intro_one = ("<strong>Si la respuesta es sí, comience una pre-limpieza</strong> antes "
                     "del cronograma regular de preparación:")
        date_tmpl = " &mdash; <em>comenzar el {date}</em>"
        outro = "Llame o envíe un mensaje a la oficina con cualquier pregunta."

    def _opt_li(label, text, offset):
        date_span = ""
        if offset is not None:
            date_span = (f'<span data-pz-day="{offset}" '
                         f'data-pz-template="{date_tmpl}"></span>')
        return f'    <li><strong>{label}:</strong> {text}{date_span}</li>'

    if b_text:
        # Two-option block. Use band-provided labels if present; fall back to defaults
        # ("Option A — lower dose, longer duration" / "Option B — higher dose, shorter
        # duration") so a band that omits labels still renders sensibly.
        if not a_lbl:
            a_lbl = "Option A &mdash; lower dose, longer duration" if lang == "en" else "Opción A &mdash; dosis menor, duración más larga"
        if not b_lbl:
            b_lbl = "Option B &mdash; higher dose, shorter duration" if lang == "en" else "Opción B &mdash; dosis mayor, duración más corta"
        body = (
            f'  <p style="margin: 0 0 2pt;">{intro_two}</p>\n'
            f'  <ul class="precleanout-options" style="margin: 0; padding-left: 18px;">\n'
            f'{_opt_li(a_lbl, a_text, a_off)}\n'
            f'{_opt_li(b_lbl, b_text, b_off)}\n'
            f'  </ul>'
        )
        return (
            f'<div class="callout">\n'
            f'  <div class="callout-title">{title}</div>\n'
            f'{body}\n'
            f'</div>'
        )

    # Single-option: keep the lead question, the dose line, and the
    # maintenance line on their own paragraphs so a parent can scan-read the
    # callout (lead → dose → maintenance → outro). Previously these were all
    # collapsed into one running paragraph.
    a_date_span = ""
    if a_off is not None:
        a_date_span = (f'<span data-pz-day="{a_off}" '
                       f'data-pz-template="{date_tmpl}"></span>')
    if lang == "en":
        lead = ("&#9888;&#65039; <strong>If any history or suspicion of "
                "constipation, please do a pre-cleanout</strong> before the "
                "regular prep timeline below:")
    else:
        lead = ("&#9888;&#65039; <strong>Si hay antecedente o sospecha de "
                "estreñimiento, haga una pre-limpieza</strong> antes del "
                "cronograma regular de preparación:")
    parts = [
        f'  <p style="margin: 0 0 1pt;">{lead}</p>',
        f'  <p style="margin: 0 0 1pt;">{a_text}{a_date_span}</p>',
    ]
    if maintenance:
        parts.append(f'  <p style="margin: 0;">{maintenance}</p>')
    body = "\n".join(parts)
    return (
        f'<div class="callout">\n'
        f'{body}\n'
        f'</div>'
    )


def build_contingency_block(band, lang, location):
    """Rescue plan shown when the BIG PREP isn't producing clear/pale-yellow stools.

    Per-band dosing fields drive the rescue capfuls/oz; the location's
    `clears_npo_hours` drives the morning cutoff (2 h SCC, 3 h PMCH).

    A `pz-only` span is appended to the morning step so that, when the family
    has personalized the procedure date+time on the mobile page (or the
    scheduler back-end runs `apply_pz_substitutions`), the rescue cutoff is
    shown as a concrete clock time. Bands without `contingency_*` fields
    (infant protocols) return an empty string so the placeholder collapses.
    """
    if band.get("protocol") != "standard":
        return ""
    if "contingency_evening_caps" not in band:
        return ""
    npo_hours = location.get("clears_npo_hours", 2) if location else 2
    npo_minutes = npo_hours * 60
    trigger = band.get("contingency_trigger_hours", 4)
    ev_caps = band["contingency_evening_caps"]
    ev_oz = band["contingency_evening_oz"]
    mn_caps = band["contingency_morning_caps"]
    mn_oz = band["contingency_morning_oz"]

    # Two spans personalize the morning bullet:
    # - morning_date_pz inserts "(Wed, May 20)" right after "Morning"
    # - morning_pz appends " — by 5:00 AM" at the end of the bullet
    # Both collapse to nothing when no procedure date is supplied.
    morning_date_pz = ('<span class="pz-only" data-pz-day="0" '
                       'data-pz-template=" ({date})"></span>')
    morning_pz = (f'<span class="pz-only" data-pz-time-mins="-{npo_minutes}" '
                  f'data-pz-template=" &mdash; by {{time}}"></span>')
    if lang == "en":
        cap_word_ev = "capfuls" if ev_caps != 1 else "capful"
        cap_word_mn = "capfuls" if mn_caps != 1 else "capful"
        return (
            '<div class="contingency-body">\n'
            f'  <p class="contingency-lead"><strong class="rescue-heading">Rescue plan</strong> &mdash; '
            f'if stools are <strong>not clear or pale yellow {trigger} hours after starting</strong> '
            f'(or if no stools), give extra MiraLAX:</p>\n'
            '  <ul>\n'
            f'    <li><strong>Evening:</strong> give <strong>{ev_caps} more {cap_word_ev} of MiraLAX '
            f'in {ev_oz} oz of Gatorade</strong>.</li>\n'
            f'    <li><strong>Morning</strong>{morning_date_pz}: give <strong>{mn_caps} more {cap_word_mn} in {mn_oz} oz '
            f'of Gatorade</strong>, at least <strong>{npo_hours} hours before procedure</strong>{morning_pz}.</li>\n'
            '  </ul>\n'
            '</div>'
        )
    # Spanish
    cap_word_ev = "tapas" if ev_caps != 1 else "tapa"
    cap_word_mn = "tapas" if mn_caps != 1 else "tapa"
    morning_pz_es = (f'<span class="pz-only" data-pz-time-mins="-{npo_minutes}" '
                     f'data-pz-template=" &mdash; antes de las {{time}}"></span>')
    return (
        '<div class="contingency-body">\n'
        f'  <p class="contingency-lead"><strong class="rescue-heading">Plan de rescate</strong> &mdash; '
        f'si las heces <strong>no son claras o amarillas pálidas {trigger} horas después de iniciar</strong> '
        f'(o si no hay heces), dé MiraLAX adicional:</p>\n'
        '  <ul>\n'
        f'    <li><strong>Por la noche:</strong> dé <strong>{ev_caps} {cap_word_ev} más de MiraLAX '
        f'en {ev_oz} oz de Gatorade</strong>.</li>\n'
        f'    <li><strong>La mañana</strong>{morning_date_pz}: dé <strong>{mn_caps} {cap_word_mn} más en {mn_oz} oz '
        f'de Gatorade</strong>, al menos <strong>{npo_hours} horas antes del procedimiento</strong>{morning_pz_es}.</li>\n'
        '  </ul>\n'
        '</div>'
    )


def build_location_placeholders(location, lang):
    """Build LOCATION_* placeholders from a locations.<id> block in dosing.yaml."""
    if not location:
        return {}
    return {
        "{{LOCATION_NAME}}":         location.get(f"name_{lang}", location.get("name_en", "")),
        "{{LOCATION_ADDRESS}}":      location.get("address", ""),
        "{{LOCATION_PHONE}}":        location.get("phone", ""),
        "{{LOCATION_PHONE_LABEL}}":  location.get(f"phone_label_{lang}", location.get("phone_label_en", "")),
        "{{LOCATION_ARRIVAL}}":      location.get(f"arrival_{lang}", location.get("arrival_en", "")),
        "{{LOCATION_MAPS_URL}}":     location.get(f"maps_url_{lang}", location.get("maps_url_en", "")),
        "{{NPO_CLEARS_HOURS}}":      str(location.get("clears_npo_hours", 2)),
        "{{LOCATION_ARRIVAL_MINUTES}}":          str(location.get("arrival_minutes_before", 60)),
        "{{LOCATION_ARRIVAL_FACILITY_SHORT}}":   location.get(f"arrival_facility_short_{lang}",
                                                              location.get("arrival_facility_short_en", "the surgery center")),
    }


def _cup_tracker_html(band, lang, drink_cup, total_oz):
    """Build the mobile-only MiraLAX cup tracker — one tappable checkbox per
    cup so a teen can keep count of how many they've finished.

    Cup size is the band's own `drink_cup` increment (3/5/7/8 oz across the
    standard bands), not a flat 4/8. Cup count = ceil(total Gatorade ÷ cup oz).
    The token is referenced ONLY by the mobile templates, so it never reaches
    the print PDF. Interactivity (toggle + localStorage persistence) is layered
    on by the shared mobile-a11y.js; the bare checkboxes still toggle without JS.
    """
    m = re.search(r"(\d+)\s*oz", drink_cup or "")
    cup_oz = int(m.group(1)) if m else 8
    try:
        cups = max(1, -(-int(total_oz) // cup_oz))  # ceil division
    except (TypeError, ValueError):
        return ""
    aria = "Cup {n} of {t}" if lang == "en" else "Vaso {n} de {t}"
    cells = "".join(
        f'<label class="cup"><input type="checkbox" aria-label="{aria.format(n=i, t=cups)}">'
        f'<span aria-hidden="true">{i}</span></label>'
        for i in range(1, cups + 1)
    )
    key = f"giready-cups-{band['id']}-{lang}"
    if lang == "en":
        head = (f'\U0001F964 <strong>Cup tracker</strong> &mdash; tap each cup as your child '
                f'finishes it. Aim for about <strong>{cups} cups</strong> (~{cup_oz} oz each), '
                f'one every 30 minutes.')
        # aria-live progress template; mobile-a11y.js fills %n%/%t%. Localized
        # here (the shared JS is language-agnostic) so the announced count
        # matches the page language instead of always reading English.
        prog_tmpl = "%n% of %t% cups done"
    else:
        head = (f'\U0001F964 <strong>Contador de vasos</strong> &mdash; toque cada vaso cuando '
                f'su niño lo termine. La meta es aproximadamente <strong>{cups} vasos</strong> '
                f'(~{cup_oz} oz cada uno), uno cada 30 minutos.')
        prog_tmpl = "%n% de %t% vasos completados"
    return (
        f'<div class="cup-tracker" data-cup-key="{key}" data-total="{cups}" data-progress-tmpl="{prog_tmpl}">\n'
        f'  <div class="cup-tracker-head">{head}</div>\n'
        f'  <div class="cup-grid">{cells}</div>\n'
        f'  <p class="cup-progress" aria-live="polite"></p>\n'
        '</div>'
    )


def build_strings(band, lang, location=None):
    """Return a dict of placeholder → rendered string for a standard-protocol band.

    `location` drives the rescue/contingency block's NPO-window interpolation
    (2 h SCC vs 3 h PMCH). Optional — bands without contingency_* fields
    render an empty contingency block regardless.
    """
    tabs = band["dulcolax_tablets"]
    mg = band["dulcolax_mg_total"]
    bedtime_tabs = band.get("dulcolax_bedtime_tablets", tabs)  # default: all at bedtime if not split
    dayof_tabs = band.get("dulcolax_dayof_tablets", 0)
    bedtime_mg = bedtime_tabs * 5
    dayof_mg = dayof_tabs * 5
    capfuls = band["miralax_capfuls"]
    grams = band["miralax_grams"]
    note = band.get(f"miralax_note_{lang}", "") or ""
    oz = band["gatorade_oz"]
    ml = oz_to_ml(oz)
    precleanout = band[f"precleanout_{lang}"]

    def tablet_word_en(n): return "tablet" if n == 1 else "tablets"
    def tablet_word_es(n): return "tableta" if n == 1 else "tabletas"

    # Per-band time + cup overrides (for the 15-20 special schedule)
    dayof_time = band.get("dulcolax_dayof_time", "2:00 PM")
    miralax_time = band.get("miralax_time", "3:00 PM")
    drink_cup = band.get(f"drink_cup_{lang}", "1 cup (8 oz)" if lang == "en" else "1 taza (8 oz)")

    # Shopping totals (big prep + rescue) — shared with the calendar export
    # via _shopping_totals; see its docstring for the rationale.
    _shop = _shopping_totals(band)
    shop_caps = _shop["caps"]
    shop_grams = _shop["grams"]
    shop_gatorade_oz = _shop["gatorade_oz"]
    shop_gatorade_ml = _shop["gatorade_ml"]
    shop_miralax_oz = _shop["miralax_oz"]
    shopping_note = band.get(f"miralax_shopping_note_{lang}", "") or ""

    if lang == "en":
        tablet_word = tablet_word_en(tabs)
        html_dulcolax_short = f"{tabs} {tablet_word} ({mg} mg)"
        html_miralax_short = f"{capfuls} capfuls (~{grams} g{note})"
        html_miralax_short_plain = f"{shop_caps} capfuls ({shop_miralax_oz} oz or {shop_grams} g)"
        html_gatorade_vol = f"{oz} oz (~{ml} mL)"
        html_gatorade_shopping_vol = f"{shop_gatorade_oz} oz (~{shop_gatorade_ml} mL)"

        docx_dulcolax_long = f"{tabs} Dulcolax 5 mg {tablet_word} ({mg} mg total)"
        docx_dulcolax_bedtime_long = (
            f"{bedtime_tabs} Dulcolax 5 mg {tablet_word_en(bedtime_tabs)} ({bedtime_mg} mg)"
            if bedtime_tabs > 0 else REMOVE_PARAGRAPH_MARKER
        )
        docx_dulcolax_dayof_long = (
            f"{dayof_tabs} Dulcolax 5 mg {tablet_word_en(dayof_tabs)} ({dayof_mg} mg)"
            if dayof_tabs > 0 else REMOVE_PARAGRAPH_MARKER
        )

        docx_miralax_shopping = (
            f"At least {shop_caps} capfuls (~{shop_grams} g) of MiraLAX"
            + (f" ({shopping_note})" if shopping_note else "")
            + f" and {shop_gatorade_oz} oz (~{shop_gatorade_ml} mL) of clear Gatorade "
            "(no red or purple) — enough for the big prep plus the rescue plan"
        )
        docx_miralax_5pm = (
            f"{capfuls} capfuls (~{grams} g{note}) of MiraLAX in "
            f"{oz} oz (~{ml} mL) of Gatorade"
        )
        html_precleanout = precleanout
        docx_precleanout = precleanout
    elif lang == "es":
        tab_word = tablet_word_es(tabs)
        html_dulcolax_short = f"{tabs} {tab_word} ({mg} mg)"
        html_miralax_short = f"{capfuls} tapas (~{grams} g{note})"
        html_miralax_short_plain = f"{shop_caps} tapas ({shop_miralax_oz} oz o {shop_grams} g)"
        html_gatorade_vol = f"{oz} oz (~{ml} mL)"
        html_gatorade_shopping_vol = f"{shop_gatorade_oz} oz (~{shop_gatorade_ml} mL)"

        docx_dulcolax_long = f"{tabs} {tab_word} de Dulcolax 5 mg ({mg} mg total)"
        docx_dulcolax_bedtime_long = (
            f"{bedtime_tabs} {tablet_word_es(bedtime_tabs)} de Dulcolax 5 mg ({bedtime_mg} mg)"
            if bedtime_tabs > 0 else REMOVE_PARAGRAPH_MARKER
        )
        docx_dulcolax_dayof_long = (
            f"{dayof_tabs} {tablet_word_es(dayof_tabs)} de Dulcolax 5 mg ({dayof_mg} mg)"
            if dayof_tabs > 0 else REMOVE_PARAGRAPH_MARKER
        )

        docx_miralax_shopping = (
            f"Al menos {shop_caps} tapas (~{shop_grams} g) de MiraLAX"
            + (f" ({shopping_note})" if shopping_note else "")
            + f" y {shop_gatorade_oz} oz (~{shop_gatorade_ml} mL) de Gatorade transparente "
            "(sin rojo ni morado) — suficiente para la preparación grande más el plan de rescate"
        )
        docx_miralax_5pm = (
            f"{capfuls} tapas (~{grams} g{note}) de MiraLAX en "
            f"{oz} oz (~{ml} mL) de Gatorade"
        )
        html_precleanout = precleanout
        docx_precleanout = precleanout
    else:
        raise ValueError(f"Unsupported language: {lang}")

    # HTML short forms for the new bedtime/day-of split lines
    if lang == "en":
        html_dulcolax_bedtime_short = (f"{bedtime_tabs} {tablet_word_en(bedtime_tabs)} ({bedtime_mg} mg)"
                                        if bedtime_tabs > 0 else REMOVE_PARAGRAPH_MARKER)
        html_dulcolax_dayof_short = (f"{dayof_tabs} {tablet_word_en(dayof_tabs)} ({dayof_mg} mg)"
                                      if dayof_tabs > 0 else REMOVE_PARAGRAPH_MARKER)
    else:
        html_dulcolax_bedtime_short = (f"{bedtime_tabs} {tablet_word_es(bedtime_tabs)} ({bedtime_mg} mg)"
                                        if bedtime_tabs > 0 else REMOVE_PARAGRAPH_MARKER)
        html_dulcolax_dayof_short = (f"{dayof_tabs} {tablet_word_es(dayof_tabs)} ({dayof_mg} mg)"
                                      if dayof_tabs > 0 else REMOVE_PARAGRAPH_MARKER)

    # "2 Days Before" HTML block — only for bands with a bedtime Dulcolax dose; empty for 15-20 kg.
    # Two time-boxes: (1) bedtime Dulcolax dose; (2) prep-only mixing of the MiraLAX bottle to
    # refrigerate overnight. The mixing wording is explicit "do NOT drink yet" because patients
    # were misreading "Mix MiraLAX X capfuls" as "give MiraLAX 2 days before."
    if bedtime_tabs > 0:
        miralax_capfuls = band.get("miralax_capfuls", "")
        miralax_grams = band.get("miralax_grams", "")
        gatorade_oz = band.get("gatorade_oz", "")
        # If the bedtime Dulcolax is forgotten, parents catch up on the prep day
        # by adding the missed bedtime dose to the scheduled day-of dose. Default
        # is additive (bedtime + dayof); bands may override with
        # `dulcolax_forgot_dayof_tablets` if clinical guidance differs.
        forgot_tabs = band.get("dulcolax_forgot_dayof_tablets",
                               bedtime_tabs + dayof_tabs)
        if lang == "en":
            bedtime_dose_text = f"{bedtime_tabs} {tablet_word_en(bedtime_tabs)} ({bedtime_mg} mg)"
            forgot_text = f"{forgot_tabs} {tablet_word_en(forgot_tabs)} ({forgot_tabs * 5} mg)"
            html_two_days_before = (
                '<h2 class="section-heading step" data-pz-day="-2" data-pz-suffix=" — 2 Days Before the Procedure"><span class="icon">📅</span> 2 Days Before the Procedure</h2>\n'
                '        <div class="details-content">\n'
                '            <div class="time-box">\n'
                '                <div class="when">At bedtime</div>\n'
                f'                <div class="what">Give {_dulcolax_label_en(bedtime_tabs)} — <strong>{bedtime_dose_text}</strong> — with a sip of water.</div>\n'
                '            </div>\n'
                '            <div class="time-box">\n'
                '                <div class="when">Evening — prepare the prep</div>\n'
                f'                <div class="what"><strong>Prepare only — do NOT drink yet.</strong> Mix MiraLAX (<strong>{miralax_capfuls} capfuls / {miralax_grams} g</strong>) into Gatorade (<strong>{gatorade_oz} oz</strong>). Shake, refrigerate overnight. Your child will drink this <strong data-pz-day="-1" data-pz-template="on {{date}}">tomorrow</strong>.</div>\n'
                '            </div>\n'
                f'            <p class="note">If you forget the bedtime Dulcolax dose: on the day of prep, give <strong>{forgot_text}</strong> with or just before the MiraLAX — that\'s the bedtime dose ({bedtime_dose_text}) added to the scheduled day-of dose ({html_dulcolax_dayof_short}). Don\'t skip — combine.</p>\n'
                '        </div>\n'
                '\n        '
            )
        else:
            bedtime_dose_text = f"{bedtime_tabs} {tablet_word_es(bedtime_tabs)} ({bedtime_mg} mg)"
            forgot_text = f"{forgot_tabs} {tablet_word_es(forgot_tabs)} ({forgot_tabs * 5} mg)"
            html_two_days_before = (
                '<h2 class="section-heading step" data-pz-day="-2" data-pz-suffix=" — 2 Días Antes del Procedimiento"><span class="icon">📅</span> 2 Días Antes del Procedimiento</h2>\n'
                '        <div class="details-content">\n'
                '            <div class="time-box">\n'
                '                <div class="when">Antes de dormir</div>\n'
                f'                <div class="what">Dé {_dulcolax_label_es(bedtime_tabs)} — <strong>{bedtime_dose_text}</strong> — con un sorbo de agua.</div>\n'
                '            </div>\n'
                '            <div class="time-box">\n'
                '                <div class="when">Por la noche — preparar la preparación</div>\n'
                f'                <div class="what"><strong>Solo preparar — NO beber aún.</strong> Mezcle el MiraLAX (<strong>{miralax_capfuls} tapas / {miralax_grams} g</strong>) con el Gatorade (<strong>{gatorade_oz} oz</strong>). Agite, refrigere durante la noche. Su niño lo beberá <strong data-pz-day="-1" data-pz-template="el {{date}}">mañana</strong>.</div>\n'
                '            </div>\n'
                f'            <p class="note">Si olvida la dosis nocturna de Dulcolax: el día de la preparación, dé <strong>{forgot_text}</strong> con o justo antes del MiraLAX — eso es la dosis nocturna ({bedtime_dose_text}) sumada a la dosis programada del día ({html_dulcolax_dayof_short}). No la omita — combine.</p>\n'
                '        </div>\n'
                '\n        '
            )
    else:
        html_two_days_before = ""

    # Prep-medicine block (the "1 Day Before" timeline). When Dulcolax-day-of
    # and MiraLAX times are the same (true for all bands ≥21 kg), collapse
    # both meds into a single time-box. The 15-20 kg band keeps its earlier
    # split schedule (Dulcolax 12 PM, MiraLAX 1 PM) as two separate boxes.
    miralax_dose_phrase = _miralax_dose_phrase(band, lang)

    # Day-of meds in two sequenced time-boxes: Dulcolax first, then the MiraLAX.
    # When both fall at the same clock time (and there are tablets to give) the
    # second box reads "Then"/"Luego" instead of repeating the identical time;
    # otherwise it carries the MiraLAX clock time (e.g. the 15-20 kg split).
    sequenced = dayof_time == miralax_time and dayof_tabs > 0
    if lang == "en":
        when2 = "Then" if sequenced else miralax_time
        html_prep_medicine_block = (
            '<div class="time-box">\n'
            f'  <div class="when">{dayof_time}</div>\n'
            f'  <div class="what">Give {_dulcolax_label_en(dayof_tabs)} &mdash; <strong>{html_dulcolax_dayof_short}</strong> &mdash; with a sip of water.</div>\n'
            '</div>\n'
            '<div class="time-box">\n'
            f'  <div class="when">{when2}</div>\n'
            '  <div class="what">\n'
            f'    Start the MiraLAX solution &mdash; <strong>{miralax_dose_phrase}</strong> &mdash; from the fridge.<br>\n'
            f'    Have your child drink <strong>{drink_cup} every 30 minutes</strong> until finished.\n'
            '  </div>\n'
            '</div>'
        )
    else:
        when2 = "Luego" if sequenced else miralax_time
        html_prep_medicine_block = (
            '<div class="time-box">\n'
            f'  <div class="when">{dayof_time}</div>\n'
            f'  <div class="what">Dé {_dulcolax_label_es(dayof_tabs)} &mdash; <strong>{html_dulcolax_dayof_short}</strong> &mdash; con un sorbo de agua.</div>\n'
            '</div>\n'
            '<div class="time-box">\n'
            f'  <div class="when">{when2}</div>\n'
            '  <div class="what">\n'
            f'    Comience la solución de MiraLAX &mdash; <strong>{miralax_dose_phrase}</strong> &mdash; del refrigerador.<br>\n'
            f'    Haga que su niño beba <strong>{drink_cup} cada 30 minutos</strong> hasta terminar.\n'
            '  </div>\n'
            '</div>'
        )

    return {
        # HTML placeholders
        "{{HTML_TITLE}}": band[f"html_title_{lang}"],
        "{{BAND_LABEL}}": band[f"label_{lang}"],
        "{{HTML_DULCOLAX_SHORT}}": html_dulcolax_short,
        "{{HTML_DULCOLAX_BEDTIME_SHORT}}": html_dulcolax_bedtime_short,
        "{{HTML_DULCOLAX_DAYOF_SHORT}}": html_dulcolax_dayof_short,
        "{{HTML_DULCOLAX_DAYOF_TIME}}": dayof_time,
        "{{HTML_MIRALAX_TIME}}": miralax_time,
        "{{HTML_DRINK_CUP}}": drink_cup,
        "{{HTML_TWO_DAYS_BEFORE_BLOCK}}": html_two_days_before,
        "{{HTML_PREP_MEDICINE_BLOCK}}": html_prep_medicine_block,
        # Mobile-only MiraLAX cup tracker (referenced only by the mobile
        # templates, so it never reaches the print PDF).
        "{{HTML_CUP_TRACKER}}": _cup_tracker_html(band, lang, drink_cup, oz),
        "{{HTML_MIRALAX_SHORT}}": html_miralax_short,
        "{{HTML_MIRALAX_SHORT_PLAIN}}": html_miralax_short_plain,        # shopping total: big prep + rescue
        "{{HTML_MIRALAX_SHOPPING_NOTE}}": shopping_note,                 # per-band bottle-size hint
        "{{HTML_GATORADE_VOL}}": html_gatorade_vol,                      # big-prep mix volume (dose lines)
        "{{HTML_GATORADE_SHOPPING_VOL}}": html_gatorade_shopping_vol,    # shopping total: big prep + rescue
        "{{HTML_PRECLEANOUT}}": html_precleanout,
        "{{HTML_PRECLEANOUT_BLOCK}}": build_precleanout_block(band, lang),
        "{{HTML_CONTINGENCY_BLOCK}}": build_contingency_block(band, lang, location),
        "{{HTML_MEDICATIONS_DRUGS}}": _medications_drugs(band, lang),
        # Phase-2: meds.giready.com QR + verify line appended inside the
        # Medications callout on every mobile HTML and print PDF. Constant
        # across band/location/lang — the URL never changes. The DOCX
        # templates don't reference this token (DOCX update deferred to a
        # follow-up phase), so substitution is a no-op there.
        "{{MEDS_GIREADY_QR}}": _meds_giready_qr_data_uri(),
        # DOCX placeholders
        "{{DOCX_HEADING}}": band[f"docx_heading_{lang}"],
        "{{DOCX_DULCOLAX_LONG}}": docx_dulcolax_long,                  # total dose, used in Plan Ahead
        "{{DOCX_DULCOLAX_BEDTIME_LONG}}": docx_dulcolax_bedtime_long,
        "{{DOCX_DULCOLAX_DAYOF_LONG}}": docx_dulcolax_dayof_long,
        "{{DOCX_DULCOLAX_DAYOF_TIME}}": dayof_time,
        "{{DOCX_MIRALAX_TIME}}": miralax_time,
        "{{DOCX_DRINK_CUP}}": drink_cup,
        "{{DOCX_MIRALAX_SHOPPING}}": docx_miralax_shopping,
        "{{DOCX_MIRALAX_5PM}}": docx_miralax_5pm,
        "{{DOCX_PRECLEANOUT}}": docx_precleanout,
    }


def build_infant_strings(band, lang):
    """Return placeholder → string dict for an infant-protocol band."""
    return {
        "{{HTML_TITLE}}": band[f"html_title_{lang}"],
        "{{BAND_LABEL}}": band[f"label_{lang}"],
        "{{WARNING_WEIGHT}}": band[f"warning_weight_{lang}"],
        "{{DOCX_HEADING}}": band[f"docx_heading_{lang}"],
        "{{HTML_MEDICATIONS_DRUGS}}": _medications_drugs(band, lang),
        "{{MEDS_GIREADY_QR}}": _meds_giready_qr_data_uri(),
    }


def _lactulose_daily_table_html(tiers, lang):
    """Build the dose-by-weight table for lactulose-infant bands."""
    if lang == "en":
        headers = ("Your Child's Weight", "Lactulose Dose", "How to Give")
    else:
        headers = ("Peso de su Niño", "Dosis de Lactulosa", "Cómo Administrar")
    rows = "\n".join(
        f'      <tr><td>{t[f"label_{lang}"]}</td><td><strong>{t[f"dose_label_{lang}"]}</strong></td><td>{t[f"how_{lang}"]}</td></tr>'
        for t in tiers
    )
    return (
        '<table class="dose-table">\n'
        '  <thead>\n'
        f'    <tr><th>{headers[0]}</th><th>{headers[1]}</th><th>{headers[2]}</th></tr>\n'
        '  </thead>\n'
        '  <tbody>\n'
        f'{rows}\n'
        '  </tbody>\n'
        '</table>'
    )


def _lactulose_big_prep_table_html(tiers, gat_oz_default, lang):
    """Build the per-weight big-prep mix table for lactulose-standard bands.

    Each row tells the family how much lactulose to mix into how much Gatorade
    for their child's specific weight. Single-row tables (21-30 kg) still
    render as a one-row table so the format stays consistent.
    """
    if lang == "en":
        headers = ("Your Child's Weight", "Lactulose to Mix", "Into Gatorade")
    else:
        headers = ("Peso de su Niño", "Lactulosa a Mezclar", "En Gatorade")
    rows = []
    for t in tiers:
        oz = t.get("gatorade_oz", gat_oz_default)
        ml = oz_to_ml(oz)
        rows.append(
            f'      <tr><td>{t[f"label_{lang}"]}</td>'
            f'<td><strong>{t["lactulose_ml"]} mL</strong></td>'
            f'<td><strong>{oz} oz (~{ml} mL)</strong></td></tr>'
        )
    return (
        '<table class="dose-table">\n'
        '  <thead>\n'
        f'    <tr><th>{headers[0]}</th><th>{headers[1]}</th><th>{headers[2]}</th></tr>\n'
        '  </thead>\n'
        '  <tbody>\n' + "\n".join(rows) + '\n'
        '  </tbody>\n'
        '</table>'
    )


def _lactulose_rescue_block_html(band, lang, location):
    """Rescue plan: extra lactulose if stools aren't clearing 4 hours in."""
    npo_hours = (location or {}).get("clears_npo_hours", 2)
    ev_ml = band["rescue_evening_lactulose_ml"]
    ev_oz = band["rescue_evening_gatorade_oz"]
    mo_ml = band["rescue_morning_lactulose_ml"]
    mo_oz = band["rescue_morning_gatorade_oz"]
    if lang == "en":
        return (
            '<div class="callout">\n'
            '  <div class="callout-title">&#9888;&#65039; Rescue plan</div>\n'
            f'  <p>If stools are not clear or pale yellow <strong>4 hours</strong> after starting (or if no stools), give extra lactulose:</p>\n'
            '  <ul>\n'
            f'    <li><strong>Evening:</strong> give <strong>{ev_ml} mL more lactulose</strong> in {ev_oz} oz of Gatorade.</li>\n'
            f'    <li><strong>Morning of procedure:</strong> give <strong>{mo_ml} mL more lactulose</strong> in {mo_oz} oz of Gatorade, at least <strong>{npo_hours} hours before</strong> the procedure.</li>\n'
            '  </ul>\n'
            '</div>'
        )
    else:
        return (
            '<div class="callout">\n'
            '  <div class="callout-title">&#9888;&#65039; Plan de rescate</div>\n'
            f'  <p>Si las heces no son claras o amarillo pálido <strong>4 horas</strong> después de comenzar (o si no hay heces), dé lactulosa adicional:</p>\n'
            '  <ul>\n'
            f'    <li><strong>Por la noche:</strong> dé <strong>{ev_ml} mL más de lactulosa</strong> en {ev_oz} oz de Gatorade.</li>\n'
            f'    <li><strong>Mañana del procedimiento:</strong> dé <strong>{mo_ml} mL más de lactulosa</strong> en {mo_oz} oz de Gatorade, al menos <strong>{npo_hours} horas antes</strong> del procedimiento.</li>\n'
            '  </ul>\n'
            '</div>'
        )


def _lactulose_two_days_before_block_html(band, lang):
    """For lactulose-standard bands with a bedtime Dulcolax dose (21-30 kg).

    15-20 kg has no bedtime Dulcolax — returns empty string. For bands with a
    bedtime Dulcolax dose, also include the evening "mix lactulose + Gatorade
    and refrigerate overnight" step.
    """
    bedtime_tabs = band.get("dulcolax_bedtime_tablets", 0)
    if bedtime_tabs <= 0:
        return ""
    bedtime_mg = bedtime_tabs * 5
    dayof_tabs = band.get("dulcolax_dayof_tablets", 0)
    forgot_tabs = band.get("dulcolax_forgot_dayof_tablets", bedtime_tabs + dayof_tabs)
    forgot_mg = forgot_tabs * 5
    tiers = band["lactulose_big_prep_tiers"]
    # Build the mix-overnight tier list (one or two rows, same as the dose table).
    def _mix_rows(lang):
        rows = []
        for t in tiers:
            oz = t.get("gatorade_oz", 20)
            ml = oz_to_ml(oz)
            if lang == "en":
                rows.append(f'<li><strong>{t["label_en"]}:</strong> Mix <strong>{t["lactulose_ml"]} mL of lactulose</strong> into <strong>{oz} oz (~{ml} mL) of Gatorade</strong>. Shake, refrigerate overnight.</li>')
            else:
                rows.append(f'<li><strong>{t["label_es"]}:</strong> Mezcle <strong>{t["lactulose_ml"]} mL de lactulosa</strong> en <strong>{oz} oz (~{ml} mL) de Gatorade</strong>. Agite, refrigere durante la noche.</li>')
        return "\n              ".join(rows)
    if lang == "en":
        tab = "tablet" if bedtime_tabs == 1 else "tablets"
        ftab = "tablet" if forgot_tabs == 1 else "tablets"
        dtab = "tablet" if dayof_tabs == 1 else "tablets"
        return (
            '<h2 class="section-heading step" data-pz-day="-2" data-pz-suffix=" — 2 Days Before the Procedure"><span class="icon">📅</span> 2 Days Before the Procedure</h2>\n'
            '        <div class="details-content">\n'
            '            <div class="time-box">\n'
            '                <div class="when">At bedtime</div>\n'
            f'                <div class="what">Give {_dulcolax_label_en(bedtime_tabs)} — <strong>{bedtime_tabs} {tab} ({bedtime_mg} mg)</strong> — with a sip of water.</div>\n'
            '            </div>\n'
            '            <div class="time-box">\n'
            '                <div class="when">Evening — prepare the prep</div>\n'
            '                <div class="what"><strong>Prepare only — do NOT drink yet.</strong> Mix lactulose into Gatorade and refrigerate overnight:\n'
            f'                  <ul style="margin-top: 6px;">\n              {_mix_rows("en")}\n                  </ul>\n'
            '                </div>\n'
            '            </div>\n'
            f'            <p class="note">If you forget the bedtime Dulcolax dose: on the day of prep, give <strong>{forgot_tabs} {ftab} ({forgot_mg} mg)</strong> with or just before the lactulose — that\'s the bedtime dose ({bedtime_tabs} {tab}) added to the scheduled day-of dose ({dayof_tabs} {dtab}). Don\'t skip — combine.</p>\n'
            '        </div>\n        '
        )
    else:
        tab = "tableta" if bedtime_tabs == 1 else "tabletas"
        ftab = "tableta" if forgot_tabs == 1 else "tabletas"
        dtab = "tableta" if dayof_tabs == 1 else "tabletas"
        return (
            '<h2 class="section-heading step" data-pz-day="-2" data-pz-suffix=" — 2 Días Antes del Procedimiento"><span class="icon">📅</span> 2 Días Antes del Procedimiento</h2>\n'
            '        <div class="details-content">\n'
            '            <div class="time-box">\n'
            '                <div class="when">Antes de dormir</div>\n'
            f'                <div class="what">Dé {_dulcolax_label_es(bedtime_tabs)} — <strong>{bedtime_tabs} {tab} ({bedtime_mg} mg)</strong> — con un sorbo de agua.</div>\n'
            '            </div>\n'
            '            <div class="time-box">\n'
            '                <div class="when">Por la noche — preparar la preparación</div>\n'
            '                <div class="what"><strong>Solo preparar — NO beber aún.</strong> Mezcle la lactulosa con Gatorade y refrigere durante la noche:\n'
            f'                  <ul style="margin-top: 6px;">\n              {_mix_rows("es")}\n                  </ul>\n'
            '                </div>\n'
            '            </div>\n'
            f'            <p class="note">Si olvida la dosis nocturna de Dulcolax: el día de la preparación, dé <strong>{forgot_tabs} {ftab} ({forgot_mg} mg)</strong> con o justo antes de la lactulosa — eso es la dosis nocturna ({bedtime_tabs} {tab}) sumada a la dosis programada del día ({dayof_tabs} {dtab}). No la omita — combine.</p>\n'
            '        </div>\n        '
        )


def build_lactulose_strings(band, lang, location=None):
    """Return placeholder → rendered string dict for a lactulose-protocol band.

    Handles both `lactulose-infant` (daily-dose, no big-prep day, no Dulcolax)
    and `lactulose-standard` (Dulcolax + lactulose-in-Gatorade big-prep with a
    sub-table of doses by sub-weight).
    """
    protocol = band["protocol"]
    common = {
        "{{HTML_TITLE}}": band[f"html_title_{lang}"],
        "{{BAND_LABEL}}": band[f"label_{lang}"],
        "{{DOCX_HEADING}}": band[f"docx_heading_{lang}"],
        "{{HTML_MEDICATIONS_DRUGS}}": _medications_drugs(band, lang),
        "{{MEDS_GIREADY_QR}}": _meds_giready_qr_data_uri(),
        # Used by the lactulose-infant "for kids under {weight} only" callout.
        # Lactulose-standard doesn't reference this token, but it's harmless to
        # always provide so the same placeholder dict works for both protocols.
        "{{WARNING_WEIGHT}}": band.get(f"warning_weight_{lang}",
                                       band.get("warning_weight_en", "15 kg")),
    }

    if protocol == "lactulose-infant":
        tiers = band["lactulose_daily_tiers"]
        common["{{HTML_LACTULOSE_DAILY_TABLE}}"] = _lactulose_daily_table_html(tiers, lang)
        # Reasonable default — under-15 kids only need a small bottle; not
        # band-specific, just a hint for the Plan-Ahead section.
        common["{{HTML_LACTULOSE_BOTTLE_HINT_EN}}"] = "one small bottle"
        return common

    # lactulose-standard
    tiers = band["lactulose_big_prep_tiers"]
    gat_oz = band.get("gatorade_oz") or tiers[0].get("gatorade_oz", 20)
    common["{{HTML_LACTULOSE_BIG_PREP_TABLE}}"] = _lactulose_big_prep_table_html(tiers, gat_oz, lang)
    # Total mL to buy: sum of the highest dose tier plus a small buffer for rescue.
    max_dose = max(t["lactulose_ml"] for t in tiers)
    rescue_buffer = band.get("rescue_evening_lactulose_ml", 0) + band.get("rescue_morning_lactulose_ml", 0)
    common["{{HTML_LACTULOSE_TOTAL_BOTTLE_ML}}"] = str(max_dose + rescue_buffer + 30)  # +30 mL safety
    # Gatorade total for shopping (max tier + rescue oz buffer).
    max_gat = max(t.get("gatorade_oz", gat_oz) for t in tiers)
    rescue_gat = band.get("rescue_evening_gatorade_oz", 0) + band.get("rescue_morning_gatorade_oz", 0)
    common["{{HTML_LACTULOSE_GATORADE_TOTAL_OZ}}"] = str(max_gat + rescue_gat)
    # Dulcolax dosing strings
    dayof_tabs = band.get("dulcolax_dayof_tablets", 1)
    dayof_mg = dayof_tabs * 5
    bedtime_tabs = band.get("dulcolax_bedtime_tablets", 0)
    bedtime_mg = bedtime_tabs * 5
    total_tabs = bedtime_tabs + dayof_tabs
    total_mg = total_tabs * 5
    if lang == "en":
        dtab = "tablet" if dayof_tabs == 1 else "tablets"
        btab = "tablet" if bedtime_tabs == 1 else "tablets"
        ttab = "tablet" if total_tabs == 1 else "tablets"
        common["{{HTML_DULCOLAX_TOTAL_LONG}}"] = f"{total_tabs} Dulcolax 5 mg {ttab} ({total_mg} mg total)"
    else:
        dtab = "tableta" if dayof_tabs == 1 else "tabletas"
        btab = "tableta" if bedtime_tabs == 1 else "tabletas"
        ttab = "tableta" if total_tabs == 1 else "tabletas"
        common["{{HTML_DULCOLAX_TOTAL_LONG}}"] = f"{total_tabs} {ttab} de Dulcolax 5 mg ({total_mg} mg total)"
    common["{{HTML_DULCOLAX_DAYOF_SHORT}}"] = f"{dayof_tabs} {dtab} ({dayof_mg} mg)"
    common["{{HTML_DULCOLAX_BEDTIME_SHORT}}"] = (
        f"{bedtime_tabs} {btab} ({bedtime_mg} mg)" if bedtime_tabs > 0 else REMOVE_PARAGRAPH_MARKER
    )
    common["{{HTML_DULCOLAX_DAYOF_TIME}}"] = band.get("dulcolax_dayof_time", "3:00 PM")
    common["{{HTML_LACTULOSE_TIME}}"] = band.get("lactulose_time", "3:00 PM")
    common["{{HTML_DRINK_CUP}}"] = band.get(f"drink_cup_{lang}", "3 oz (~90 mL)")
    common["{{HTML_TWO_DAYS_BEFORE_BLOCK}}"] = _lactulose_two_days_before_block_html(band, lang)
    common["{{HTML_LACTULOSE_RESCUE_BLOCK}}"] = _lactulose_rescue_block_html(band, lang, location)
    common["{{HTML_PRECLEANOUT_BLOCK}}"] = build_precleanout_block(band, lang)
    return common


def build_clenpiq_strings(band, lang, location=None):
    """Return placeholder → rendered string dict for a clenpiq-standard band.

    CLENPIQ (sodium picosulfate / magnesium oxide / citric acid) is a
    scheduler-only alternative prep for patients 31 kg and up who cannot
    tolerate the MiraLAX + Gatorade volume. Dosing is identical across all
    eligible weights — a single unified band in dosing.yaml routes all three
    user-facing weight bands (31-40 / 41-50 / over-50) to the same handout.

    `location` drives the NPO cutoff for the post-Dose-2 clears (2 h SCC vs
    3 h PMCH); it's surfaced via `{{NPO_CLEARS_HOURS}}` rather than baked
    into a CLENPIQ-specific placeholder so the templates stay consistent
    with the standard / lactulose families.
    """
    bottle_oz = band["clenpiq_bottle_oz"]
    bottle_ml = band["clenpiq_bottle_ml"]
    total_bottles = band["clenpiq_total_bottles"]
    return {
        "{{HTML_TITLE}}":                         band[f"html_title_{lang}"],
        # The handout cover uses the unified summary label so all three
        # eligible weight bands see the same "31 kg and up — CLENPIQ" text.
        "{{BAND_LABEL}}":                         band.get(f"summary_label_{lang}",
                                                            band[f"label_{lang}"]),
        "{{DOCX_HEADING}}":                       band[f"docx_heading_{lang}"],
        "{{HTML_MEDICATIONS_DRUGS}}":             _medications_drugs(band, lang),
        "{{MEDS_GIREADY_QR}}":                    _meds_giready_qr_data_uri(),
        # Lactulose-infant template uses {{WARNING_WEIGHT}}; harmless to
        # always provide so the same placeholder dict works downstream.
        "{{WARNING_WEIGHT}}":                     band.get(f"warning_weight_{lang}",
                                                            band.get("warning_weight_en", "")),
        # Bottle + dose figures
        "{{HTML_CLENPIQ_BOTTLE_OZ}}":             str(bottle_oz),
        "{{HTML_CLENPIQ_BOTTLE_ML}}":             str(bottle_ml),
        "{{HTML_CLENPIQ_TOTAL_BOTTLES}}":         str(total_bottles),
        # Dose 1 (evening before)
        "{{HTML_CLENPIQ_DOSE1_WINDOW}}":          band[f"dose1_window_{lang}"],
        "{{HTML_CLENPIQ_DOSE1_CLEARS_CUPS}}":     str(band["dose1_clears_cups"]),
        "{{HTML_CLENPIQ_DOSE1_CLEARS_OZ}}":       str(band["dose1_clears_oz"]),
        "{{HTML_CLENPIQ_DOSE1_CLEARS_HOURS}}":    str(band["dose1_clears_hours"]),
        # Dose 2 (morning of, started 5-9 h before procedure)
        "{{HTML_CLENPIQ_DOSE2_HOURS_BEFORE_MIN}}": str(band["dose2_hours_before_min"]),
        "{{HTML_CLENPIQ_DOSE2_HOURS_BEFORE_MAX}}": str(band["dose2_hours_before_max"]),
        "{{HTML_CLENPIQ_DOSE2_CLEARS_CUPS}}":     str(band["dose2_clears_cups"]),
        "{{HTML_CLENPIQ_DOSE2_CLEARS_OZ}}":       str(band["dose2_clears_oz"]),
        # Cup size for the per-cup drinking cadence
        "{{HTML_DRINK_CUP}}":                     band[f"drink_cup_{lang}"],
        # CLENPIQ has no pre-cleanout (the prep itself supplies a stimulant
        # via picosulfate). Empty string keeps the {{HTML_PRECLEANOUT_BLOCK}}
        # slot a no-op so templates that include it render cleanly.
        "{{HTML_PRECLEANOUT_BLOCK}}":             "",
    }


def build_suprep_strings(band, lang, location=None):
    """Return placeholder → rendered string dict for a suprep-standard band.

    SUPREP (sodium / potassium / magnesium sulfate) is a scheduler-only
    sulfate-based alternative prep for patients ≥50 kg (FDA age 12+, Rx
    required). Each kit ships as 2 bottles of concentrate; each dose mixes
    1 bottle with cool water to the 12-oz fill line on the supplied
    container, drunk in full, then chased with 2 more 12-oz container
    fills of plain water (24 oz total) over the next hour.

    `location` drives the NPO cutoff for the post-Dose-2 clears (2 h SCC vs
    3 h PMCH); it's surfaced via `{{NPO_CLEARS_HOURS}}` rather than baked
    into a SUPREP-specific placeholder so the templates stay consistent
    with the standard / lactulose / clenpiq families.
    """
    return {
        "{{HTML_TITLE}}":                         band[f"html_title_{lang}"],
        "{{BAND_LABEL}}":                         band.get(f"summary_label_{lang}",
                                                            band[f"label_{lang}"]),
        "{{DOCX_HEADING}}":                       band[f"docx_heading_{lang}"],
        "{{HTML_MEDICATIONS_DRUGS}}":             _medications_drugs(band, lang),
        "{{MEDS_GIREADY_QR}}":                    _meds_giready_qr_data_uri(),
        # Lactulose-infant template uses {{WARNING_WEIGHT}}; harmless to
        # always provide so the same placeholder dict works downstream.
        "{{WARNING_WEIGHT}}":                     band.get(f"warning_weight_{lang}",
                                                            band.get("warning_weight_en", "")),
        # Bottle + dose figures
        "{{HTML_SUPREP_BOTTLE_OZ}}":              str(band["suprep_bottle_oz"]),
        "{{HTML_SUPREP_TOTAL_BOTTLES}}":          str(band["suprep_total_bottles"]),
        "{{HTML_SUPREP_FILL_LINE_OZ}}":           str(band["suprep_fill_line_oz"]),
        # Dose 1 (evening before)
        "{{HTML_SUPREP_DOSE1_WINDOW}}":           band[f"dose1_window_{lang}"],
        "{{HTML_SUPREP_DOSE1_SOLUTION_OZ}}":      str(band["dose1_solution_oz"]),
        "{{HTML_SUPREP_DOSE1_CHASERS_OZ}}":       str(band["dose1_chasers_oz"]),
        "{{HTML_SUPREP_DOSE1_CHASER_FILLS}}":     str(band["dose1_chaser_fills"]),
        "{{HTML_SUPREP_DOSE1_CHASERS_HOURS}}":    str(band["dose1_chasers_hours"]),
        # Dose 2 (morning of, started 10–12 h after Dose 1, ≥5 h before procedure)
        "{{HTML_SUPREP_DOSE2_HOURS_BEFORE_MIN}}": str(band["dose2_hours_before_min"]),
        "{{HTML_SUPREP_DOSE_SEPARATION_MIN}}":    str(band["dose_separation_hours_min"]),
        "{{HTML_SUPREP_DOSE_SEPARATION_MAX}}":    str(band["dose_separation_hours_max"]),
        "{{HTML_SUPREP_DOSE2_SOLUTION_OZ}}":      str(band["dose2_solution_oz"]),
        "{{HTML_SUPREP_DOSE2_CHASERS_OZ}}":       str(band["dose2_chasers_oz"]),
        "{{HTML_SUPREP_DOSE2_CHASER_FILLS}}":     str(band["dose2_chaser_fills"]),
        "{{HTML_SUPREP_DOSE2_CHASERS_HOURS}}":    str(band["dose2_chasers_hours"]),
        # Drug-interaction window unique to SUPREP
        "{{HTML_SUPREP_DRUG_INTERACTION_NOTE}}":  band[f"suprep_drug_interaction_note_{lang}"],
        "{{HTML_SUPREP_ORAL_MEDS_BLACKOUT_HOURS}}": str(band["suprep_oral_meds_blackout_hours"]),
        # Cup size for the per-cup drinking cadence (12 oz fill line)
        "{{HTML_DRINK_CUP}}":                     band[f"drink_cup_{lang}"],
        # SUPREP has no separate pre-cleanout (sulfate stimulant is built in,
        # and the FDA label prohibits concurrent stimulant laxatives).
        "{{HTML_PRECLEANOUT_BLOCK}}":             "",
    }


def _medications_drugs(band, lang):
    """The drug list for the Medications callout. GLP-1 agonists are only
    relevant for adolescents (~≥40 kg in our protocol), so smaller bands
    drop the GLP-1 mention entirely. Bands missing the include_glp1_warning
    field default to True (keeps the warning) so legacy YAML still renders."""
    include_glp1 = band.get("include_glp1_warning", True)
    if lang == "en":
        return ("iron, anti-diarrhea medicine, GLP-1 (Ozempic, Wegovy, Mounjaro)"
                if include_glp1 else "iron, anti-diarrhea medicine")
    else:
        return ("hierro, antidiarreico, GLP-1 (Ozempic, Wegovy, Mounjaro)"
                if include_glp1 else "hierro, antidiarreico")


# ---------------------------------------------------------------------------
# Calendar-export events ("Add this schedule to your calendar" on mobile pages)
# ---------------------------------------------------------------------------
# build_calendar_events() emits the structured event list that the personalize
# partial serializes as {{PZ_EVENTS_JSON}}. Browser-side JS turns it into
# .ics / Google Calendar entries once the parent enters the procedure
# datetime — the date never leaves the device.
#
# TIMING ONLY by design: dose amounts appear exclusively as build-time
# verbatim handout strings (the same fields the handout text uses) — the
# client never computes a dose. This is the documented SaMD line.
#
# Event time forms (exactly one per event):
#   allDay: true + day              → all-day event on (procedure date + day)
#   day + start [+ end "HH:MM"]     → wall-clock event on that day
#   offsetMin [+ offsetEndMin]      → minutes relative to the procedure datetime
# Optional: latestEndOffsetMin (clamp — SUPREP dose 2 must end ≥5 h before
# the procedure), durationMin, alarmMin (omitted → JS defaults).

_TAG_RE = re.compile(r"<[^>]+>")


def _plain(html_text):
    """Strip tags + unescape entities + collapse whitespace → plain text
    suitable for a calendar event description."""
    text = _TAG_RE.sub("", html_text or "")
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _hhmm_from_12h(t12):
    """'3:00 PM' → '15:00'."""
    from datetime import datetime as _dtt
    return _dtt.strptime(t12.strip(), "%I:%M %p").strftime("%H:%M")


def _12h(hhmm):
    """'14:00' → '2:00 PM' (matches the handouts' clock-time style)."""
    from datetime import datetime as _dtt
    t = _dtt.strptime(hhmm.strip(), "%H:%M")
    return t.strftime("%-I:%M %p")


_CALENDAR_CFG_CACHE = None


def _calendar_cfg():
    global _CALENDAR_CFG_CACHE
    if _CALENDAR_CFG_CACHE is None:
        _CALENDAR_CFG_CACHE = load_dosing().get("calendar", {})
    return _CALENDAR_CFG_CACHE


def _ev(eid, title_discreet, title_detailed, desc, **kw):
    ev = {"id": eid, "titleDiscreet": title_discreet,
          "titleDetailed": title_detailed, "desc": _plain(desc)}
    ev.update({k: v for k, v in kw.items() if v is not None})
    return ev


def _cal_meds_stop(band, lang, cal):
    drugs = _medications_drugs(band, lang)
    if lang == "en":
        return _ev("meds_stop",
                   "Pause certain medicines",
                   f"Stop {drugs}",
                   f"Stop {drugs} 7 days before the procedure. "
                   "Check any medicine at meds.giready.com.",
                   allDay=True, day=cal["meds_stop_day"])
    return _ev("meds_stop",
               "Pausar ciertos medicamentos",
               f"Suspender {drugs}",
               f"Suspenda {drugs} 7 días antes del procedimiento. "
               "Verifique cualquier medicamento en meds.giready.com.",
               allDay=True, day=cal["meds_stop_day"])


def _cal_low_residue(lang, cal, family):
    clears12 = _12h(cal["clears_start_hhmm"])
    if lang == "en":
        suffix = "EGD + colonoscopy prep" if family == "combined" else "colonoscopy prep"
        return _ev("low_residue_start",
                   "Start low-fiber diet",
                   f"Start low-residue diet — {suffix}",
                   "Low-residue (“white”) diet for the 3 days before the "
                   "procedure, through lunch the day before. After "
                   f"{clears12} the day before, switch to clear liquids only "
                   "— no dairy. Sample meals are on your instructions page.",
                   allDay=True, day=cal["low_residue_start_day"])
    suffix = ("preparación para endoscopia + colonoscopia" if family == "combined"
              else "preparación para colonoscopia")
    return _ev("low_residue_start",
               "Comenzar dieta baja en fibra",
               f"Comenzar dieta baja en residuos — {suffix}",
               "Dieta baja en residuos (“blanca”) durante los 3 días antes "
               "del procedimiento, hasta el almuerzo del día anterior. Después de las "
               f"{clears12} del día anterior, cambie a solo líquidos claros — sin "
               "lácteos. Ideas de comidas en su página de instrucciones.",
               allDay=True, day=cal["low_residue_start_day"])


def _cal_clears_start(lang, cal, family):
    clears12 = _12h(cal["clears_start_hhmm"])
    if lang == "en":
        suffix = "EGD + colonoscopy prep" if family == "combined" else "colonoscopy prep"
        return _ev("clears_start",
                   "Clear liquids only — begins",
                   f"Clear liquids only begins — {suffix}",
                   f"After {clears12} — clear liquids only. No solid food, no "
                   "dairy. OK: water, apple juice, white grape juice, lemonade, "
                   "clear soda, clear broth, popsicles, plain Jell-O. Nothing "
                   "red or purple.",
                   day=-1, start=cal["clears_start_hhmm"])
    suffix = ("preparación para endoscopia + colonoscopia" if family == "combined"
              else "preparación para colonoscopia")
    return _ev("clears_start",
               "Solo líquidos claros — comienza",
               f"Comienzan solo líquidos claros — {suffix}",
               f"Después de las {clears12} — solo líquidos claros. Sin alimentos "
               "sólidos, sin lácteos. Permitido: agua, jugo de manzana, jugo de uva "
               "blanca, limonada, refresco claro, caldo claro, paletas heladas, "
               "gelatina simple. Nada rojo ni morado.",
               day=-1, start=cal["clears_start_hhmm"])


def _cal_clears_allday(lang, family):
    if lang == "en":
        suffix = "EGD + colonoscopy prep" if family == "combined" else "colonoscopy prep"
        return _ev("clears_allday",
                   "Clear liquids only — all day",
                   f"Clear liquids only all day — {suffix}",
                   "Clear liquids only all day — no solid food, no dairy. OK: "
                   "water, apple juice, white grape juice, lemonade, clear soda, "
                   "clear broth, popsicles, plain Jell-O. Nothing red or purple.",
                   allDay=True, day=-1)
    suffix = ("preparación para endoscopia + colonoscopia" if family == "combined"
              else "preparación para colonoscopia")
    return _ev("clears_allday",
               "Solo líquidos claros — todo el día",
               f"Solo líquidos claros todo el día — {suffix}",
               "Solo líquidos claros todo el día — sin alimentos sólidos, sin "
               "lácteos. Permitido: agua, jugo de manzana, jugo de uva blanca, "
               "limonada, refresco claro, caldo claro, paletas heladas, gelatina "
               "simple. Nada rojo ni morado.",
               allDay=True, day=-1)


def _cal_common_tail(lang, location, cal, family):
    """npo_clears_stop, arrival, procedure — identical across protocols."""
    npo = location["clears_npo_hours"]
    arr_min = location["arrival_minutes_before"]
    name = location[f"name_{lang}"]
    address = location["address"]
    phone = location.get("phone", "")
    phone_label = location.get(f"phone_label_{lang}", "")
    facility = location.get(f"arrival_facility_short_{lang}", name)
    arrival_sentence = location.get(f"arrival_{lang}", "")
    proc_dur = cal.get("procedure_duration_min", 60)
    if lang == "en":
        proc_detail = "EGD + colonoscopy" if family == "combined" else "Colonoscopy"
        return [
            _ev("npo_clears_stop",
                "Stop all drinks",
                f"Stop all clear liquids ({npo} hours before procedure)",
                f"Stop all clear liquids {npo} hours before the procedure. "
                "After this, nothing to eat or drink — this keeps your child "
                "safe during anesthesia.",
                offsetMin=-npo * 60),
            _ev("arrival",
                "Arrive for appointment",
                f"Arrive at {facility} — check-in",
                f"{arrival_sentence}. {name}: {address}. {phone_label}: {phone}.",
                offsetMin=-arr_min, offsetEndMin=0, loc=address),
            _ev("procedure",
                "Appointment",
                proc_detail,
                f"{name}: {address}. {phone_label}: {phone}.",
                offsetMin=0, durationMin=proc_dur, loc=address),
        ]
    proc_detail = ("Endoscopia (EGD) + colonoscopia" if family == "combined"
                   else "Colonoscopia")
    return [
        _ev("npo_clears_stop",
            "Suspender todos los líquidos",
            f"Suspender líquidos claros ({npo} horas antes del procedimiento)",
            f"Suspenda todos los líquidos claros {npo} horas antes del "
            "procedimiento. Después de esto, nada de comer ni beber — esto "
            "mantiene a su niño seguro durante la anestesia.",
            offsetMin=-npo * 60),
        _ev("arrival",
            "Llegar a la cita",
            f"Llegue a {facility} — registro",
            f"{arrival_sentence}. {name}: {address}. {phone_label}: {phone}.",
            offsetMin=-arr_min, offsetEndMin=0, loc=address),
        _ev("procedure",
            "Cita",
            proc_detail,
            f"{name}: {address}. {phone_label}: {phone}.",
            offsetMin=0, durationMin=proc_dur, loc=address),
    ]


def _cal_standard_events(band, lang, cal, family, is_lact=False):
    """Shared body for protocol: standard and lactulose-standard."""
    events = [_cal_meds_stop(band, lang, cal),
              _cal_low_residue(lang, cal, family)]

    # Buy supplies (all-day, "buy at least 2 days before")
    tabs = band["dulcolax_tablets"]
    if is_lact:
        tiers = band["lactulose_big_prep_tiers"]
        max_dose = max(t["lactulose_ml"] for t in tiers)
        rescue_ml = (band.get("rescue_evening_lactulose_ml", 0)
                     + band.get("rescue_morning_lactulose_ml", 0))
        total_ml = max_dose + rescue_ml + 30  # +30 mL safety, same as handout
        gat_oz = band.get("gatorade_oz") or tiers[0].get("gatorade_oz", 20)
        max_gat = max(t.get("gatorade_oz", gat_oz) for t in tiers)
        rescue_gat = (band.get("rescue_evening_gatorade_oz", 0)
                      + band.get("rescue_morning_gatorade_oz", 0))
        if lang == "en":
            buy_desc = (f"Lactulose (prescription): about {total_ml} mL total. "
                        f"Clear Gatorade (no red or purple): {max_gat + rescue_gat} oz. "
                        f"Dulcolax 5 mg tablets: {tabs}.")
            buy_detail = "Buy prep supplies — lactulose, Gatorade, Dulcolax"
        else:
            buy_desc = (f"Lactulosa (con receta): aproximadamente {total_ml} mL en total. "
                        f"Gatorade transparente (sin rojo ni morado): {max_gat + rescue_gat} oz. "
                        f"Tabletas de Dulcolax 5 mg: {tabs}.")
            buy_detail = "Comprar suministros de preparación — lactulosa, Gatorade, Dulcolax"
    else:
        shop = _shopping_totals(band)
        note = band.get(f"miralax_shopping_note_{lang}", "") or ""
        if lang == "en":
            buy_desc = (f"MiraLAX: {shop['caps']} capfuls ({shop['miralax_oz']} oz or "
                        f"{shop['grams']} g){' — ' + note if note else ''}. "
                        f"Clear Gatorade (no red or purple): {shop['gatorade_oz']} oz "
                        f"(~{shop['gatorade_ml']} mL). Dulcolax 5 mg tablets: {tabs}. "
                        "Enough for the big prep plus the rescue plan.")
            buy_detail = "Buy prep supplies — MiraLAX, Gatorade, Dulcolax"
        else:
            buy_desc = (f"MiraLAX: {shop['caps']} tapas ({shop['miralax_oz']} oz o "
                        f"{shop['grams']} g){' — ' + note if note else ''}. "
                        f"Gatorade transparente (sin rojo ni morado): {shop['gatorade_oz']} oz "
                        f"(~{shop['gatorade_ml']} mL). Tabletas de Dulcolax 5 mg: {tabs}. "
                        "Suficiente para la preparación grande más el plan de rescate.")
            buy_detail = "Comprar suministros de preparación — MiraLAX, Gatorade, Dulcolax"
    events.append(_ev("buy_supplies",
                      "Buy supplies" if lang == "en" else "Comprar suministros",
                      buy_detail, buy_desc,
                      allDay=True, day=cal["buy_supplies_day"]))

    # Bedtime Dulcolax + mix-and-refrigerate (day -2; mirrors the
    # "2 Days Before" section, which only exists when bedtime tablets > 0).
    bedtime_tabs = band.get("dulcolax_bedtime_tablets", 0)
    if bedtime_tabs > 0:
        bedtime_text = f"{bedtime_tabs} {_tablet_word(bedtime_tabs, lang)} ({bedtime_tabs * 5} mg)"
        if is_lact:
            tiers = band["lactulose_big_prep_tiers"]
            if lang == "en":
                mix_rows = "; ".join(
                    f"{t['label_en']}: mix {t['lactulose_ml']} mL of lactulose into "
                    f"{t.get('gatorade_oz', 20)} oz of Gatorade" for t in tiers)
                mix_desc = (f"At bedtime: give {_dulcolax_label_en(bedtime_tabs)} — {bedtime_text} — with a "
                            "sip of water. Evening: prepare only — do NOT drink yet. "
                            f"{mix_rows}. Shake, refrigerate overnight. Your child will "
                            "drink this tomorrow.")
            else:
                mix_rows = "; ".join(
                    f"{t['label_es']}: mezcle {t['lactulose_ml']} mL de lactulosa en "
                    f"{t.get('gatorade_oz', 20)} oz de Gatorade" for t in tiers)
                mix_desc = (f"Antes de dormir: dé {_dulcolax_label_es(bedtime_tabs)} — {bedtime_text} — "
                            "con un sorbo de agua. Por la noche: solo preparar — NO beber aún. "
                            f"{mix_rows}. Agite, refrigere durante la noche. Su niño lo beberá "
                            "mañana.")
        else:
            caps = band["miralax_capfuls"]
            grams = band["miralax_grams"]
            gat_oz = band["gatorade_oz"]
            if lang == "en":
                mix_desc = (f"At bedtime: give {_dulcolax_label_en(bedtime_tabs)} — {bedtime_text} — with a "
                            "sip of water. Evening: prepare only — do NOT drink yet. Mix "
                            f"MiraLAX ({caps} capfuls / {grams} g) into Gatorade ({gat_oz} oz). "
                            "Shake, refrigerate overnight. Your child will drink this tomorrow.")
            else:
                mix_desc = (f"Antes de dormir: dé {_dulcolax_label_es(bedtime_tabs)} — {bedtime_text} — "
                            "con un sorbo de agua. Por la noche: solo preparar — NO beber aún. "
                            f"Mezcle el MiraLAX ({caps} tapas / {grams} g) con el Gatorade "
                            f"({gat_oz} oz). Agite, refrigere durante la noche. Su niño lo "
                            "beberá mañana.")
        if lang == "en":
            events.append(_ev("bedtime_dulcolax_mix",
                              "Evening: prep steps",
                              "Bedtime Dulcolax + mix the prep drink (refrigerate)",
                              mix_desc, allDay=True, day=-2))
        else:
            events.append(_ev("bedtime_dulcolax_mix",
                              "Por la noche: pasos de preparación",
                              "Dulcolax antes de dormir + mezclar la bebida (refrigerar)",
                              mix_desc, allDay=True, day=-2))

    events.append(_cal_clears_start(lang, cal, family))

    # THE BIG PREP — day before the procedure. ("dulcolax_dayof_*" in
    # dosing.yaml means "day of PREP", which is day -1 relative to the
    # procedure — the template heading carries data-pz-day="-1".)
    dayof_tabs = band.get("dulcolax_dayof_tablets", 0)
    dayof_mg = dayof_tabs * 5
    dayof_short = f"{dayof_tabs} {_tablet_word(dayof_tabs, lang)} ({dayof_mg} mg)"
    dayof_time12 = band.get("dulcolax_dayof_time", "3:00 PM")
    drink_time12 = (band.get("lactulose_time") if is_lact
                    else band.get("miralax_time")) or "3:00 PM"
    drink_cup = band.get(f"drink_cup_{lang}",
                         "1 cup (8 oz)" if lang == "en" else "1 taza (8 oz)")
    if is_lact:
        tiers = band["lactulose_big_prep_tiers"]
        if lang == "en":
            dose_rows = "; ".join(
                f"{t['label_en']}: {t['lactulose_ml']} mL of lactulose in "
                f"{t.get('gatorade_oz', 20)} oz of Gatorade" for t in tiers)
            drink_phrase = f"the lactulose + Gatorade mix from the fridge ({dose_rows})"
            drink_name = "lactulose"
        else:
            dose_rows = "; ".join(
                f"{t['label_es']}: {t['lactulose_ml']} mL de lactulosa en "
                f"{t.get('gatorade_oz', 20)} oz de Gatorade" for t in tiers)
            drink_phrase = f"la mezcla de lactulosa + Gatorade del refrigerador ({dose_rows})"
            drink_name = "lactulosa"
    else:
        drink_phrase = (f"the MiraLAX solution — {_miralax_dose_phrase(band, lang)} — from the fridge"
                        if lang == "en" else
                        f"la solución de MiraLAX — {_miralax_dose_phrase(band, lang)} — del refrigerador")
        drink_name = "MiraLAX"

    if dayof_time12 == drink_time12 and dayof_tabs > 0:
        if lang == "en":
            events.append(_ev("big_prep",
                              "Start THE BIG PREP",
                              f"Dulcolax {dayof_short} + start the {drink_name} drink",
                              f"Give {_dulcolax_label_en(dayof_tabs)} — {dayof_short} — with a sip of "
                              f"water, then start {drink_phrase}. Have your child drink "
                              f"{drink_cup} every 30 minutes until finished.",
                              day=-1, start=_hhmm_from_12h(drink_time12)))
        else:
            events.append(_ev("big_prep",
                              "Comenzar LA GRAN PREPARACIÓN",
                              f"Dulcolax {dayof_short} + comenzar la bebida de {drink_name}",
                              f"Dé {_dulcolax_label_es(dayof_tabs)} — {dayof_short} — con un sorbo "
                              f"de agua, luego comience {drink_phrase}. Haga que su niño "
                              f"beba {drink_cup} cada 30 minutos hasta terminar.",
                              day=-1, start=_hhmm_from_12h(drink_time12)))
    else:
        # Split schedule (15-20 kg: tablets at 12 PM, drink at 1 PM) —
        # mirror the two time-boxes with two events.
        if lang == "en":
            if dayof_tabs > 0:
                events.append(_ev("big_prep_tablets",
                                  f"Give the {_tablet_word(dayof_tabs, 'en')}",
                                  f"Give Dulcolax — {dayof_short}",
                                  f"Give {_dulcolax_label_en(dayof_tabs)} — {dayof_short} — with a sip of water.",
                                  day=-1, start=_hhmm_from_12h(dayof_time12)))
            events.append(_ev("big_prep_drink",
                              "Start the prep drink",
                              f"Start the {drink_name} drink",
                              f"Start {drink_phrase}. Have your child drink {drink_cup} "
                              "every 30 minutes until finished.",
                              day=-1, start=_hhmm_from_12h(drink_time12)))
        else:
            if dayof_tabs > 0:
                events.append(_ev("big_prep_tablets",
                                  f"Dar {'la' if dayof_tabs == 1 else 'las'} {_tablet_word(dayof_tabs, 'es')}",
                                  f"Dar Dulcolax — {dayof_short}",
                                  f"Dé {_dulcolax_label_es(dayof_tabs)} — {dayof_short} — con un sorbo de agua.",
                                  day=-1, start=_hhmm_from_12h(dayof_time12)))
            events.append(_ev("big_prep_drink",
                              "Comenzar la bebida de preparación",
                              f"Comenzar la bebida de {drink_name}",
                              f"Comience {drink_phrase}. Haga que su niño beba {drink_cup} "
                              "cada 30 minutos hasta terminar.",
                              day=-1, start=_hhmm_from_12h(drink_time12)))
    return events


def _cal_clenpiq_events(band, lang, cal, family, location):
    bottle_oz = band["clenpiq_bottle_oz"]
    bottle_ml = band["clenpiq_bottle_ml"]
    cup = band[f"drink_cup_{lang}"]
    npo = location["clears_npo_hours"]
    d1_start = band["dose1_window_start_hhmm"]
    d1_end = band["dose1_window_end_hhmm"]
    d2_min = band["dose2_hours_before_min"]
    d2_max = band["dose2_hours_before_max"]
    # SUPREP/CLENPIQ: eat normally until the day before (no 3-day low-residue),
    # then low-residue through lunch + clears after 2 PM (see _cal_clears_start).
    events = [_cal_meds_stop(band, lang, cal)]
    if lang == "en":
        events.append(_ev("buy_supplies",
                          "Buy supplies",
                          "Buy prep supplies — CLENPIQ kit + clear liquids",
                          f"1 CLENPIQ kit ({band['clenpiq_total_bottles']} bottles, "
                          f"{bottle_oz} oz / {bottle_ml} mL each — ready to drink, no "
                          "mixing). Plus plenty of clear liquids, including Gatorade, "
                          "Pedialyte, or another electrolyte drink. Nothing red or purple.",
                          allDay=True, day=cal["buy_supplies_day"]))
        events.append(_cal_clears_start(lang, cal, family))
        events.append(_ev("dose1",
                          "Give first dose (evening window)",
                          "CLENPIQ Dose 1 — drink 1 bottle during this window",
                          f"Drink 1 full bottle ({bottle_oz} oz / {bottle_ml} mL) of "
                          "CLENPIQ — cranberry-flavored, ready to drink, no mixing "
                          f"needed (tastes better cold). After the dose, drink at least "
                          f"{band['dose1_clears_cups']} × {cup} cups "
                          f"({band['dose1_clears_oz']} oz total) of clear liquids over "
                          f"the next {band['dose1_clears_hours']} hours, including an "
                          "electrolyte drink.",
                          day=-1, start=d1_start, end=d1_end))
        events.append(_ev("dose2",
                          "Give second dose (morning window)",
                          f"CLENPIQ Dose 2 — {d2_min}–{d2_max} hours before procedure",
                          f"Drink the second bottle ({bottle_oz} oz / {bottle_ml} mL) of "
                          f"CLENPIQ, then at least {band['dose2_clears_cups']} × {cup} "
                          f"cups ({band['dose2_clears_oz']} oz total) of clear liquids, "
                          f"finishing at least {npo} hours before the procedure.",
                          offsetMin=-d2_max * 60, offsetEndMin=-d2_min * 60))
    else:
        events.append(_ev("buy_supplies",
                          "Comprar suministros",
                          "Comprar suministros — kit de CLENPIQ + líquidos claros",
                          f"1 kit de CLENPIQ ({band['clenpiq_total_bottles']} botellas, "
                          f"{bottle_oz} oz / {bottle_ml} mL cada una — lista para beber, "
                          "sin mezclar). Además, suficientes líquidos claros, incluyendo "
                          "Gatorade, Pedialyte u otra bebida con electrolitos. Nada rojo "
                          "ni morado.",
                          allDay=True, day=cal["buy_supplies_day"]))
        events.append(_cal_clears_start(lang, cal, family))
        events.append(_ev("dose1",
                          "Dar la primera dosis (ventana de la tarde)",
                          "CLENPIQ Dosis 1 — beba 1 botella durante esta ventana",
                          f"Beba 1 botella completa ({bottle_oz} oz / {bottle_ml} mL) de "
                          "CLENPIQ — sabor arándano, lista para beber, sin mezclar "
                          "(sabe mejor fría). Después de la dosis, beba al menos "
                          f"{band['dose1_clears_cups']} × {cup} vasos "
                          f"({band['dose1_clears_oz']} oz en total) de líquidos claros "
                          f"durante las próximas {band['dose1_clears_hours']} horas, "
                          "incluyendo una bebida con electrolitos.",
                          day=-1, start=d1_start, end=d1_end))
        events.append(_ev("dose2",
                          "Dar la segunda dosis (ventana de la mañana)",
                          f"CLENPIQ Dosis 2 — {d2_min}–{d2_max} horas antes del procedimiento",
                          f"Beba la segunda botella ({bottle_oz} oz / {bottle_ml} mL) de "
                          f"CLENPIQ, luego al menos {band['dose2_clears_cups']} × {cup} "
                          f"vasos ({band['dose2_clears_oz']} oz en total) de líquidos "
                          f"claros, terminando al menos {npo} horas antes del procedimiento.",
                          offsetMin=-d2_max * 60, offsetEndMin=-d2_min * 60))
    return events


def _cal_suprep_events(band, lang, cal, family, location):
    npo = location["clears_npo_hours"]
    fill = band["suprep_fill_line_oz"]
    bottle = band["suprep_bottle_oz"]
    sep_min = band["dose_separation_hours_min"]
    sep_max = band["dose_separation_hours_max"]
    d2_floor = band["dose2_hours_before_min"]
    d1_start = band["dose1_window_start_hhmm"]
    d1_end = band["dose1_window_end_hhmm"]

    # Dose 2 clock window on day 0: dose1 window shifted by the 10–12 h
    # separation (5 PM + 10 h = 3 AM; 8 PM + 12 h = 8 AM next day), clamped
    # client-side so it always ends ≥ dose2_hours_before_min before the
    # procedure (latestEndOffsetMin).
    def _shift(hhmm, hours):
        h, m = (int(x) for x in hhmm.split(":"))
        total = (h + hours) % 24
        return f"{total:02d}:{m:02d}"
    d2_start = _shift(d1_start, sep_min)
    d2_end = _shift(d1_end, sep_max)

    # SUPREP/CLENPIQ: eat normally until the day before (no 3-day low-residue),
    # then low-residue through lunch + clears after 2 PM (see _cal_clears_start).
    events = [_cal_meds_stop(band, lang, cal)]
    if lang == "en":
        events.append(_ev("buy_supplies",
                          "Buy supplies",
                          "Buy prep supplies — SUPREP kit + clear liquids",
                          f"1 SUPREP kit ({band['suprep_total_bottles']} bottles of "
                          f"{bottle} oz concentrate + mixing container — prescription). "
                          "Plus plenty of clear liquids, including an electrolyte drink. "
                          "Nothing red or purple.",
                          allDay=True, day=cal["buy_supplies_day"]))
        events.append(_cal_clears_start(lang, cal, family))
        events.append(_ev("dose1",
                          "Give first dose (evening window)",
                          "SUPREP Dose 1 — mix and drink during this window",
                          f"Pour one bottle of SUPREP ({bottle} oz) into the mixing "
                          f"container that comes with the kit. Add cool drinking water to "
                          f"the {fill}-oz fill line. Mix, then drink the entire "
                          f"{band['dose1_solution_oz']} oz. Over the next hour, drink "
                          f"{band['dose1_chaser_fills']} more containers of plain water "
                          f"filled to the {fill}-oz line ({band['dose1_chasers_oz']} oz "
                          "total). Cold water and a straw help with the salty taste.",
                          day=-1, start=d1_start, end=d1_end))
        events.append(_ev("dose2",
                          "Give second dose (morning window)",
                          f"SUPREP Dose 2 — {sep_min}–{sep_max} hours after Dose 1",
                          f"Take {sep_min}–{sep_max} hours after Dose 1 and at least "
                          f"{d2_floor} hours before the procedure: mix the second bottle "
                          f"of SUPREP to the {fill}-oz fill line, drink it all, then "
                          f"{band['dose2_chaser_fills']} more containers of plain water "
                          f"({band['dose2_chasers_oz']} oz total) over the next hour. "
                          f"Finish all SUPREP and required water at least {npo} hours "
                          "before the procedure.",
                          day=0, start=d2_start, end=d2_end,
                          latestEndOffsetMin=-d2_floor * 60))
    else:
        events.append(_ev("buy_supplies",
                          "Comprar suministros",
                          "Comprar suministros — kit de SUPREP + líquidos claros",
                          f"1 kit de SUPREP ({band['suprep_total_bottles']} botellas de "
                          f"{bottle} oz de concentrado + recipiente para mezclar — con "
                          "receta). Además, suficientes líquidos claros, incluyendo una "
                          "bebida con electrolitos. Nada rojo ni morado.",
                          allDay=True, day=cal["buy_supplies_day"]))
        events.append(_cal_clears_start(lang, cal, family))
        events.append(_ev("dose1",
                          "Dar la primera dosis (ventana de la tarde)",
                          "SUPREP Dosis 1 — mezcle y beba durante esta ventana",
                          f"Vierta una botella de SUPREP ({bottle} oz) en el recipiente "
                          "para mezclar que viene con el kit. Agregue agua potable fría "
                          f"hasta la línea de {fill} oz. Mezcle y beba las "
                          f"{band['dose1_solution_oz']} oz completas. Durante la "
                          f"siguiente hora, beba {band['dose1_chaser_fills']} recipientes "
                          f"más de agua simple llenados hasta la línea de {fill} oz "
                          f"({band['dose1_chasers_oz']} oz en total). Agua fría y un "
                          "popote ayudan con el sabor salado.",
                          day=-1, start=d1_start, end=d1_end))
        events.append(_ev("dose2",
                          "Dar la segunda dosis (ventana de la mañana)",
                          f"SUPREP Dosis 2 — {sep_min}–{sep_max} horas después de la Dosis 1",
                          f"Tómela {sep_min}–{sep_max} horas después de la Dosis 1 y al "
                          f"menos {d2_floor} horas antes del procedimiento: mezcle la "
                          f"segunda botella de SUPREP hasta la línea de {fill} oz, bébala "
                          f"toda, luego {band['dose2_chaser_fills']} recipientes más de "
                          f"agua simple ({band['dose2_chasers_oz']} oz en total) durante "
                          "la siguiente hora. Termine todo el SUPREP y el agua requerida "
                          f"al menos {npo} horas antes del procedimiento.",
                          day=0, start=d2_start, end=d2_end,
                          latestEndOffsetMin=-d2_floor * 60))
    return events


def _cal_infant_events(band, lang, cal, location, is_lact=False, is_enema=False):
    """Infant protocols: timing-only event set — daily doses (PEG/lactulose),
    feeding cutoffs, NPO, arrival, procedure. No dose amounts computed."""
    npo = location["clears_npo_hours"]
    events = [_cal_meds_stop(band, lang, cal)]

    if not is_enema:
        # Daily medicine dose on days -3 and -2 ("3 Days and 2 Days Before").
        med = ("lactulose" if is_lact else "MiraLAX") if lang == "en" else \
              ("lactulosa" if is_lact else "MiraLAX")
        for day in (-3, -2):
            if lang == "en":
                events.append(_ev("daily_dose",
                                  "Give today's medicine dose",
                                  f"Give one {med} dose (see dose table)",
                                  "Continue your child's normal diet. Give one "
                                  f"{med} dose today, mixed into juice or Pedialyte — "
                                  "see the dose-by-weight table on your instructions page.",
                                  allDay=True, day=day))
            else:
                events.append(_ev("daily_dose",
                                  "Dar la dosis de medicina de hoy",
                                  f"Dar una dosis de {med} (vea la tabla de dosis)",
                                  "Continúe la dieta normal de su niño. Dé una dosis de "
                                  f"{med} hoy, mezclada en jugo o Pedialyte — vea la tabla "
                                  "de dosis por peso en su página de instrucciones.",
                                  allDay=True, day=day))

    cuts = cal["infant_enema_cutoffs"] if is_enema else cal["infant_cutoffs"]
    formula12 = _12h(cuts["formula_stop"]["hhmm"])
    bm12 = _12h(cuts["breastmilk_stop"]["hhmm"])

    if is_enema:
        if lang == "en":
            events.append(_ev("clears_day",
                              "Clear liquids day",
                              "Clear liquids only today — no solid foods",
                              f"No solid foods today. Formula or milk until {formula12}; "
                              f"breast milk until {bm12}. OK: Pedialyte, clear apple "
                              "juice (no pulp), water, clear broth, popsicles or plain "
                              "gelatin (no red/purple). Apply protective ointment to the "
                              "diaper area.",
                              allDay=True, day=-1))
        else:
            events.append(_ev("clears_day",
                              "Día de líquidos claros",
                              "Solo líquidos claros hoy — sin alimentos sólidos",
                              f"Sin alimentos sólidos hoy. Fórmula o leche hasta las "
                              f"{formula12}; leche materna hasta las {bm12}. Permitido: "
                              "Pedialyte, jugo de manzana claro (sin pulpa), agua, caldo "
                              "claro, paletas heladas o gelatina simple (sin rojo/morado). "
                              "Aplique ungüento protector en el área del pañal.",
                              allDay=True, day=-1))
    else:
        solids12 = _12h(cuts["solids_stop"]["hhmm"])
        if lang == "en":
            events.append(_ev("day_before",
                              "Special diet & feeding cutoffs today",
                              "Low-residue foods + feeding cutoffs tonight",
                              f"Low-residue foods until {solids12}. Then: formula until "
                              f"{formula12}; breast milk until {bm12} (early morning, day "
                              "of procedure). After that, only clear liquids: Pedialyte, "
                              "clear apple juice (no pulp), water. Apply protective "
                              "ointment to the diaper area.",
                              allDay=True, day=-1))
            events.append(_ev("solids_stop",
                              "Stop solid foods",
                              "Stop solid foods",
                              "All solid and low-residue foods stop now. Formula is OK "
                              f"until {formula12}; breast milk until {bm12}.",
                              day=cuts["solids_stop"]["day"],
                              start=cuts["solids_stop"]["hhmm"]))
        else:
            events.append(_ev("day_before",
                              "Dieta especial y horarios de alimentación hoy",
                              "Alimentos bajos en residuos + horarios límite esta noche",
                              f"Alimentos bajos en residuos hasta las {solids12}. Luego: "
                              f"fórmula hasta las {formula12}; leche materna hasta las "
                              f"{bm12} (madrugada del día del procedimiento). Después, "
                              "solo líquidos claros: Pedialyte, jugo de manzana claro "
                              "(sin pulpa), agua. Aplique ungüento protector en el área "
                              "del pañal.",
                              allDay=True, day=-1))
            events.append(_ev("solids_stop",
                              "Suspender comidas sólidas",
                              "Suspender comidas sólidas",
                              "Todas las comidas sólidas y bajas en residuos se detienen "
                              f"ahora. La fórmula está bien hasta las {formula12}; la "
                              f"leche materna hasta las {bm12}.",
                              day=cuts["solids_stop"]["day"],
                              start=cuts["solids_stop"]["hhmm"]))

    if lang == "en":
        events.append(_ev("formula_stop",
                          "Stop formula",
                          "Stop formula" + (" / milk" if is_enema else ""),
                          ("Formula and milk stop now. " if is_enema else "Formula stops now. ")
                          + f"Breast milk is OK until {bm12}; clear liquids until "
                          f"{npo} hours before the procedure.",
                          day=cuts["formula_stop"]["day"],
                          start=cuts["formula_stop"]["hhmm"]))
        events.append(_ev("breastmilk_stop",
                          "Stop breast milk",
                          "Stop breast milk",
                          "Breast milk stops now. Only clear liquids (Pedialyte, clear "
                          f"apple juice, water) until {npo} hours before the procedure.",
                          day=cuts["breastmilk_stop"]["day"],
                          start=cuts["breastmilk_stop"]["hhmm"]))
    else:
        events.append(_ev("formula_stop",
                          "Suspender la fórmula",
                          "Suspender la fórmula" + (" / leche" if is_enema else ""),
                          ("La fórmula y la leche se detienen ahora. " if is_enema
                           else "La fórmula se detiene ahora. ")
                          + f"La leche materna está bien hasta las {bm12}; líquidos "
                          f"claros hasta {npo} horas antes del procedimiento.",
                          day=cuts["formula_stop"]["day"],
                          start=cuts["formula_stop"]["hhmm"]))
        events.append(_ev("breastmilk_stop",
                          "Suspender la leche materna",
                          "Suspender la leche materna",
                          "La leche materna se detiene ahora. Solo líquidos claros "
                          f"(Pedialyte, jugo de manzana claro, agua) hasta {npo} horas "
                          "antes del procedimiento.",
                          day=cuts["breastmilk_stop"]["day"],
                          start=cuts["breastmilk_stop"]["hhmm"]))
    return events


def build_calendar_events(band, lang, location, family="colonoscopy"):
    """Structured prep-milestone events for the mobile calendar export.

    Returns a list of event dicts (see the section comment above for the
    schema). `family` is "colonoscopy" or "combined" — combined changes only
    the detailed procedure title and diet-title suffixes (the combined
    standard template carries no extra NPO cutoffs; formula/breastmilk rules
    exist only in infant protocols).
    """
    if lang not in ("en", "es"):
        raise ValueError(f"Unsupported language: {lang}")
    if not location:
        raise ValueError("build_calendar_events requires a location dict")
    cal = _calendar_cfg()
    protocol = band.get("protocol", "")

    if protocol == "standard":
        events = _cal_standard_events(band, lang, cal, family)
    elif protocol == "lactulose-standard":
        events = _cal_standard_events(band, lang, cal, family, is_lact=True)
    elif protocol == "clenpiq-standard":
        events = _cal_clenpiq_events(band, lang, cal, family, location)
    elif protocol == "suprep-standard":
        events = _cal_suprep_events(band, lang, cal, family, location)
    elif protocol == "infant":
        events = _cal_infant_events(band, lang, cal, location)
    elif protocol == "infant-enema":
        events = _cal_infant_events(band, lang, cal, location, is_enema=True)
    elif protocol == "lactulose-infant":
        events = _cal_infant_events(band, lang, cal, location, is_lact=True)
    else:
        raise ValueError(f"Unknown protocol for calendar events: {protocol!r}")

    events.extend(_cal_common_tail(lang, location, cal, family))
    return events


def build_calendar_events_json(band, lang, location, family="colonoscopy"):
    """The {{PZ_EVENTS_JSON}} payload — compact JSON, safe to inline in a
    <script type="application/json"> tag (escapes '</')."""
    payload = {"v": 1, "events": build_calendar_events(band, lang, location, family)}
    return json.dumps(payload, ensure_ascii=False,
                      separators=(",", ":")).replace("</", "<\\/")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_html(template_path, replacements, out_path):
    with open(template_path, encoding="utf-8") as f:
        html = f.read()
    for token, value in replacements.items():
        html = html.replace(token, value)
    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders in {out_path}: {sorted(set(unreplaced))}")
    # Strip any block (a <div class="time-box">…</div> wrapper) whose content includes the omit marker
    if REMOVE_PARAGRAPH_MARKER in html:
        omit_pat = re.compile(r'<div class="time-box">(?:(?!</div>).)*?' + re.escape(REMOVE_PARAGRAPH_MARKER) + r'(?:(?!</div>).)*?</div>\s*', re.DOTALL)
        html = omit_pat.sub("", html)
    html = _inject_shared_mobile_a11y(html)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


MOBILE_QR_FILENAME = "word/media/mobile-qr.png"


def _generate_mobile_qr(mobile_path, lang="en", subdomain="prep"):
    """Generate a band-specific mobile-link QR PNG (~150x150 px) for swap-in at render time.
    Spanish renders point at the /es/ subpath; subdomain depends on location ('prep' for SCC, 'prep86' for PMCH).

    The encoded URL includes ?feedback=1&source=print so the family who
    scans the cover QR lands on the mobile page AND the survey modal
    auto-opens with the print-vs-phone q3 variant. source=print tags the
    D1 row so PDF-origin feedback can be analyzed separately from web."""
    try:
        import qrcode
        from PIL import Image
        import io as _io
    except ImportError:
        return None
    url = f"https://{subdomain}.giready.com/{mobile_path}/"
    if lang == "es":
        url = url + "es/"
    url = url + "?feedback=1&source=print"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=1)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((150, 150), Image.NEAREST)
    buf = _io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _generate_feedback_qr(mobile_path, lang="en", subdomain="prep"):
    """Feedback QR — same target as the mobile QR (?feedback=1&source=print)
    so survey.js auto-opens with the print-vs-phone q3 variant. Slightly
    smaller (120x120 px) since it ships next to a caption in a narrow
    print cell."""
    try:
        import qrcode
        from PIL import Image
        import io as _io
    except ImportError:
        return None
    url = f"https://{subdomain}.giready.com/{mobile_path}/"
    if lang == "es":
        url = url + "es/"
    url = url + "?feedback=1&source=print"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=1)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((120, 120), Image.NEAREST)
    buf = _io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


_MEDS_GIREADY_QR_DATA_URI_CACHE = None


def _meds_giready_qr_data_uri():
    """Return the data: URI for a meds.giready.com QR (PNG, ~150x150). The
    URL is constant across every band/location/lang so we generate this PNG
    once per process and reuse the data URI for every render. Used inside
    the Medications callout on the mobile + print handouts.
    Returns "" if qrcode/PIL aren't importable; the templates then render
    a broken-image placeholder, which validate.py catches in CI."""
    global _MEDS_GIREADY_QR_DATA_URI_CACHE
    if _MEDS_GIREADY_QR_DATA_URI_CACHE is not None:
        return _MEDS_GIREADY_QR_DATA_URI_CACHE
    try:
        import qrcode
        from PIL import Image
        import io as _io
    except ImportError:
        return ""
    url = _qr_target("meds_giready_url")
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=1)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((150, 150), Image.NEAREST)
    buf = _io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    _MEDS_GIREADY_QR_DATA_URI_CACHE = _png_to_data_uri(buf.getvalue())
    return _MEDS_GIREADY_QR_DATA_URI_CACHE


# All values formerly hardcoded here (SCC_MAPS_URL, YOUTUBE_URL_*, PORTAL_URL,
# and the practice info baked into print templates) now live in practice.yaml
# and are read once at startup via _practice().
_PRACTICE_CACHE = None


def _shared_dir():
    """Resolve the shared/ dir in both layouts: vendored (vendor/shared, used by
    the backend Cloud Run image) first, then the local meta-repo checkout."""
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
    """Load practice.yaml once and cache it. Returns the parsed dict.

    The shared practice-core.yaml (practice-wide identity: phone, footer,
    disclaimer, cover stack, logo) is deep-merged UNDER the skill-local file,
    so local keys win. Skill-only keys (doctors, phone_tel, qr_targets,
    template_defaults) stay in the local practice.yaml."""
    global _PRACTICE_CACHE
    if _PRACTICE_CACHE is None:
        if not PRACTICE_PATH.exists():
            raise RuntimeError(f"practice.yaml not found at {PRACTICE_PATH}. "
                               "This file holds per-practice branding/contact/QR config.")
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


def _qr_target(key):
    return _practice()["qr_targets"][key]


# ---------------------------------------------------------------------------
# Partials — sections-as-partials template architecture (Tier-1 POC).
#
# templates/partials/_<name>.{en,es}.html files are loaded once per language
# and merged into the substitution map under {{PARTIAL_<NAME>}} tokens.
# Per-band placeholders inside a partial (e.g. {{HTML_PRECLEANOUT}}) still
# get substituted by the regular replacements pass because partials are
# merged BEFORE per-band replacements in the substitution dict.
#
# See docs/PARTIALS.md for the full architecture and migration plan.
# ---------------------------------------------------------------------------
PARTIALS_DIR = TEMPLATES / "partials"
_PARTIALS_CACHE = {}  # {lang: {token: content}}


def _load_partials(lang):
    """Read templates/partials/_*.<lang>.html and return a dict of
    {{PARTIAL_<UPPER>}}: content. Cached per-language. Returns {} if the
    partials/ directory does not exist (so templates that don't use partials
    keep working unchanged)."""
    if lang in _PARTIALS_CACHE:
        return _PARTIALS_CACHE[lang]
    out = {}
    if PARTIALS_DIR.is_dir():
        suffix = f".{lang}.html"
        for p in sorted(PARTIALS_DIR.glob(f"_*{suffix}")):
            # Filename: _<name>.<lang>.html → token {{PARTIAL_<NAME>}}
            name = p.name[1:-len(suffix)]   # strip leading "_" and trailing suffix
            token = "{{PARTIAL_" + name.upper() + "}}"
            out[token] = p.read_text(encoding="utf-8")
    _PARTIALS_CACHE[lang] = out
    return out


# Backwards-compatible accessors (used elsewhere in the file).
def _scc_maps_url_base():
    return _practice()["template_defaults"]["scc_maps_url"]


def _doctors_block_html(lang):
    """Render the all-partners doctor block for the INTERNAL Drive binder only.

    Never used on the public website PDFs (deliberate liability decision — the
    public handouts carry no doctor names). Sourced from practice.yaml `doctors:`.
    """
    docs = _practice()["practice"].get("doctors", []) or []
    names = [d.get("name_short", "").strip() for d in docs if d.get("name_short")]
    if not names:
        return ""
    heading = ("Our Pediatric Gastroenterologists" if lang == "en"
               else "Nuestros Gastroenterólogos Pediátricos")
    items = "".join(f"<li>{n}</li>" for n in names)
    return (
        '<section class="doctors-block">\n'
        f'  <h2 class="doctors-heading">{heading}</h2>\n'
        f'  <ul class="doctors-list">{items}</ul>\n'
        '</section>'
    )


def _strip_legal_footer(html):
    """Remove the footer block (copyright + privacy/terms links + medical
    disclaimer) for the legal=off fork. Mirrors the scheduler's de-personalized
    strip so static + scheduler behave identically. The doctors block sits above
    this block and is preserved."""
    return re.sub(r'\s*<p class="footer-copyright">.*?</aside>', '', html, flags=re.S)


def build_practice_placeholders(lang, logo="giready", doctors="none"):
    """Return {{PRACTICE_*}} placeholders sourced from practice.yaml for the given language.

    `logo`: "giready" (default, public brand) or "pmch" (internal Drive binder).
    `doctors`: "none" (default — public website carries no doctor names) or "all"
    (internal Drive binder lists every partner).
    """
    p = _practice()["practice"]
    stack = p.get(f"cover_stack_{lang}") or p.get("cover_stack_en") or ["", "", ""]
    # Normalize to exactly 3 lines
    stack = (stack + ["", "", ""])[:3]
    logo_file = p.get("logo_filename", "")
    logo_alt = p.get("logo_alt", "")
    if logo == "pmch":
        logo_file = "logo-pmch.png"
        logo_alt = "Peyton Manning Children's Hospital"
    return {
        "{{PRACTICE_STACK_LINE_1}}": stack[0],
        "{{PRACTICE_STACK_LINE_2}}": stack[1],
        "{{PRACTICE_STACK_LINE_3}}": stack[2],
        "{{PRACTICE_FOOTER}}":       p.get(f"footer_{lang}") or p.get("footer_en") or "",
        "{{DISCLAIMER}}":            p.get(f"disclaimer_{lang}") or p.get("disclaimer_en") or "",
        "{{PRACTICE_LOGO_FILE}}":    logo_file,
        "{{PRACTICE_LOGO_ALT}}":     logo_alt,
        "{{DOCTORS_BLOCK}}":         _doctors_block_html(lang) if doctors == "all" else "",
    }


def _generate_maps_qr(maps_url):
    """Generate a Maps QR PNG (~246x246 px) for the given URL."""
    try:
        import qrcode
        from PIL import Image
        import io as _io
    except ImportError:
        return None
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(maps_url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((246, 246), Image.NEAREST)
    buf = _io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _scc_maps_url_for_lang(lang):
    """SCC Maps URL as embedded in the language-specific template (Spanish appends ?hl=es)."""
    return _scc_maps_url_base() + ("?hl=es" if lang == "es" else "")


def _scc_maps_qr_bytes_cache(lang="en"):
    """Compute SCC Maps QR bytes for the given language; used to identify the placeholder Maps QR in templates."""
    return _generate_maps_qr(_scc_maps_url_for_lang(lang))


def render_docx(template_path, replacements, out_path, mobile_path=None, lang="en", location=None):
    """Rewrite a DOCX by substituting placeholders inside word/document.xml.

    If mobile_path is provided and the template contains a placeholder image at
    word/media/mobile-qr.png, that image is replaced with a QR code encoding
    https://prep.giready.com/{mobile_path}/ for the band.
    """
    with zipfile.ZipFile(template_path, "r") as zin:
        doc_xml = zin.read("word/document.xml").decode("utf-8")
    for token, value in replacements.items():
        # XML-escape the value minimally (ampersand, angle brackets)
        # Note: dosing strings generally don't contain <, > but may contain &.
        xml_safe = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # For docx_heading fields we already stored pre-escaped entities (&lt;, &gt;)
        # so avoid double-escaping: only escape if the original value doesn't already
        # contain an escaped entity.
        if "&lt;" in value or "&gt;" in value or "&amp;" in value:
            xml_safe = value
        doc_xml = doc_xml.replace(token, xml_safe)
    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", doc_xml)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders in {out_path}: {sorted(set(unreplaced))}")

    # Strip paragraphs whose substituted content includes the omit marker (e.g., 15-20 kg has no day-of dose)
    if REMOVE_PARAGRAPH_MARKER in doc_xml:
        omit_pat = re.compile(r"<w:p\b[^>]*>(?:(?!</w:p>).)*?" + re.escape(REMOVE_PARAGRAPH_MARKER) + r"(?:(?!</w:p>).)*?</w:p>", re.DOTALL)
        doc_xml = omit_pat.sub("", doc_xml)

    subdomain = (location or {}).get("mobile_subdomain", "prep")
    mobile_qr_bytes = _generate_mobile_qr(mobile_path, lang=lang, subdomain=subdomain) if mobile_path else None

    # Maps QR swap: if location is non-default (not SCC), generate the new Maps QR and replace
    # whichever PNG in the template matches the SCC Maps QR (identified by exact byte match).
    new_maps_qr_bytes = None
    scc_maps_qr_bytes = None
    if location:
        scc_url_for_lang = _scc_maps_url_for_lang(lang)
        loc_maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or scc_url_for_lang
        if loc_maps_url != scc_url_for_lang:
            new_maps_qr_bytes = _generate_maps_qr(loc_maps_url)
            scc_maps_qr_bytes = _scc_maps_qr_bytes_cache(lang)

    with zipfile.ZipFile(template_path, "r") as zin:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data_in = zin.read(item.filename)
                if item.filename == "word/document.xml":
                    zout.writestr(item, doc_xml)
                elif item.filename == MOBILE_QR_FILENAME and mobile_qr_bytes is not None:
                    zout.writestr(item, mobile_qr_bytes)
                elif (item.filename.startswith("word/media/") and item.filename.endswith(".png")
                      and new_maps_qr_bytes is not None
                      and scc_maps_qr_bytes is not None
                      and data_in == scc_maps_qr_bytes):
                    zout.writestr(item, new_maps_qr_bytes)
                else:
                    zout.writestr(item, data_in)


# ---------------------------------------------------------------------------
# WeasyPrint print-PDF pipeline (additive — does NOT touch the html/docx paths).
# ---------------------------------------------------------------------------

def _ensure_weasyprint_libpath():
    """On macOS, WeasyPrint needs Pango/Cairo from Homebrew. Inject /opt/homebrew/lib
    (or /usr/local/lib for Intel) into DYLD_FALLBACK_LIBRARY_PATH if not already present
    so users don't need to set it manually before invoking the script."""
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


def _png_to_data_uri(png_bytes):
    """Encode raw PNG bytes as a data: URI suitable for an <img src=...>."""
    if not png_bytes:
        return ""
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def _inject_qr_into_imgs(html, qr_uris):
    """Polished print templates reference QR images by id (e.g. <img id="qr-mobile" src="qr-placeholder.png">).
    Rewrite the src attribute of each known QR id to the corresponding data URI.
    qr_uris is a dict like {"qr-mobile": "data:image/png;base64,...", "qr-maps": "...", "qr-youtube": "...", "qr-portal": "..."}.
    """
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


def render_pdf_print(template_path, replacements, out_path,
                      mobile_path=None, lang="en", location=None, theme="color",
                      variant="standard", logo="giready", legal="on", doctors="none"):
    """Render a polished print PDF via WeasyPrint.

    Mirrors render_html's substitution logic but additionally:
      - generates the mobile + Maps QRs and embeds them as base64 data URIs
        (both via {{MOBILE_QR_DATA_URI}}/{{MAPS_QR_DATA_URI}} tokens AND by
        rewriting <img id="qr-mobile">/<img id="qr-maps"> src attributes, so
        the same renderer works for both stub and polished print templates).
      - runs WeasyPrint to produce a PDF at out_path.

    `variant` selects which mobile_subdomain field to use on the location block:
      - "standard" (default) → location["mobile_subdomain"] (colonoscopy-only sites)
      - "combined" → location["mobile_subdomain_combined"] (EGD + colonoscopy sites)
    """
    _ensure_weasyprint_libpath()
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "WeasyPrint failed to import. On macOS this usually means Pango/Cairo are "
            "missing — install with `brew install pango`. Original error: " + repr(e)
        )

    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    # Calm theme: swap the base template's <style> for the shared Calm stylesheet
    # (before substitution, so the Calm CSS's {{...}} tokens resolve below).
    if theme == "calm":
        html = _swap_calm_style(html)

    # Compute the URLs each QR encodes — also exposed as text placeholders so the
    # print template can wrap captions in <a href> for clickable PDFs.
    # Variant selects which subdomain field to read: combined renders point at the
    # EGD+colonoscopy mobile sites (egdcolon{,86}.giready.com) instead of the
    # colonoscopy-only ones.
    if variant == "combined":
        subdomain = (location or {}).get("mobile_subdomain_combined") \
                    or (location or {}).get("mobile_subdomain", "prep")
    else:
        subdomain = (location or {}).get("mobile_subdomain", "prep")
    mobile_url = f"https://{subdomain}.giready.com/{mobile_path}/" + ("es/" if lang == "es" else "") if mobile_path else ""
    if location:
        maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or _scc_maps_url_for_lang(lang)
    else:
        maps_url = _scc_maps_url_for_lang(lang)
    youtube_url = _qr_target("youtube_url_es" if lang == "es" else "youtube_url_en")
    portal_url = _qr_target("portal_url")
    gikids_url = _qr_target("gikids_url")
    # tel: URI — strip non-digit chars from the phone number for iOS compatibility
    location_phone = (location or {}).get("phone", "") if location else ""
    location_phone_tel = re.sub(r"\D", "", location_phone)

    # Generate QR PNGs as data URIs.
    mobile_qr_bytes = _generate_mobile_qr(mobile_path, lang=lang, subdomain=subdomain) if mobile_path else None
    feedback_qr_bytes = _generate_feedback_qr(mobile_path, lang=lang, subdomain=subdomain) if mobile_path else None
    maps_qr_bytes = _generate_maps_qr(maps_url) if maps_url else None
    youtube_qr_bytes = _generate_maps_qr(youtube_url)
    portal_qr_bytes = _generate_maps_qr(portal_url)
    gikids_qr_bytes = _generate_maps_qr(gikids_url)
    qr_uris = {
        "qr-mobile":   _png_to_data_uri(mobile_qr_bytes),
        "qr-feedback": _png_to_data_uri(feedback_qr_bytes),
        "qr-maps":     _png_to_data_uri(maps_qr_bytes),
        "qr-youtube":  _png_to_data_uri(youtube_qr_bytes),
        "qr-portal":   _png_to_data_uri(portal_qr_bytes),
        "qr-gikids":   _png_to_data_uri(gikids_qr_bytes),
    }

    feedback_url = (mobile_url + "?feedback=1&source=print") if mobile_url else ""

    # Token-based substitution (stub templates + URL placeholders for clickable links).
    qr_replacements = {
        "{{MOBILE_QR_DATA_URI}}":   qr_uris["qr-mobile"],
        "{{FEEDBACK_QR_DATA_URI}}": qr_uris["qr-feedback"],
        "{{MAPS_QR_DATA_URI}}":     qr_uris["qr-maps"],
        # MOBILE_URL is the clickable href on the cover-QR anchor; keep it
        # in lockstep with the QR PNG so click and scan land in the same
        # place (mobile page + auto-opened survey, tagged source=print).
        "{{MOBILE_URL}}":           feedback_url or mobile_url,
        "{{FEEDBACK_URL}}":         feedback_url,
        "{{MAPS_URL}}":             maps_url,
        "{{YOUTUBE_URL}}":          youtube_url,
        "{{PORTAL_URL}}":           portal_url,
        "{{GIKIDS_URL}}":           gikids_url,
        "{{LOCATION_PHONE_TEL}}":   location_phone_tel,
    }
    practice_replacements = build_practice_placeholders(lang, logo=logo, doctors=doctors)
    # Partials must be merged FIRST so any per-band/QR/practice placeholders that
    # live inside the partial markup are still substituted by the regular pass.
    partials_replacements = _load_partials(lang)
    all_replacements = {**partials_replacements, **replacements, **qr_replacements, **practice_replacements}
    for token, value in all_replacements.items():
        html = html.replace(token, value)

    # Legal fork: strip the footer block (copyright + privacy/terms + disclaimer)
    # for the internal Drive binder. Done after substitution so {{DISCLAIMER}} is
    # already resolved; the doctors block above the footer is preserved.
    if legal == "off":
        html = _strip_legal_footer(html)

    # id-based <img> src rewrite (polished templates that don't use the data-URI tokens).
    html = _inject_qr_into_imgs(html, qr_uris)

    # Theme: tag <body> with the requested theme so CSS can switch fills/backgrounds.
    if theme and theme != "color":
        html = re.sub(r'<body\b([^>]*)>', rf'<body\1 class="theme-{theme}">', html, count=1)

    # Strip any time-box wrapper containing the omit marker (matches render_html behavior).
    if REMOVE_PARAGRAPH_MARKER in html:
        omit_pat = re.compile(r'<div class="time-box">(?:(?!</div>).)*?' + re.escape(REMOVE_PARAGRAPH_MARKER) + r'(?:(?!</div>).)*?</div>\s*', re.DOTALL)
        html = omit_pat.sub("", html)

    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders in {out_path}: {sorted(set(unreplaced))}")

    # Splice shared print-base.css in front of the template's own <style>
    # block. Template rules still win on override; the shared file adds
    # design tokens + feedback-cell fallbacks for future migration.
    html = _inject_shared_print_css(html)

    # Resolve relative URLs (logos, maps, stub images) against the templates/
    # root, not the template's own directory — so templates in subfolders
    # (e.g. calm/) still resolve shared assets like giready-logo.png.
    # Tagged PDF/UA-1 output (deterministic) — see scripts/pdf_tagging.py.
    from pdf_tagging import write_pdf_tagged
    write_pdf_tagged(HTML(string=html, base_url=str(TEMPLATES)), str(out_path))


def render_band(band, lang, fmt, out_dir, flat=False, location=None, location_id="scc", theme="color", variant="standard", logo="giready", legal="on", doctors="none"):
    """Render one (band, language, format) combination.

    `location` is the locations.<id> block from dosing.yaml; substituted into LOCATION_* placeholders.
    `location_id` is used in output filenames (e.g., -SCC vs -PMCH suffix).
    `variant` selects the document family: "standard" (colonoscopy-only) or "combined" (EGD + colonoscopy).
        - "combined" only applies to pdf-print and renders all protocols (standard
          + both infant variants), picking the per-protocol combined-*-print.{lang}.html
          template and using the location's mobile_subdomain_combined for QRs.
          Filename gets a "-combined" suffix.
    """
    protocol = band["protocol"]
    stem = band["filename_stem"]
    # SUPREP, lactulose, and CLENPIQ render into static print PDFs
    # (handled below) when the caller explicitly selects those bands.
    # Bypassed via the main() filter for the default `--band all` pass.
    if protocol == "standard":
        replacements = build_strings(band, lang, location=location)
    elif protocol in ("infant", "infant-enema"):
        replacements = build_infant_strings(band, lang)
    elif protocol == "suprep-standard":
        replacements = build_suprep_strings(band, lang, location=location)
    elif protocol in ("lactulose-infant", "lactulose-standard"):
        replacements = build_lactulose_strings(band, lang, location=location)
    elif protocol == "clenpiq-standard":
        replacements = build_clenpiq_strings(band, lang, location=location)
    else:
        raise ValueError(f"Unknown protocol: {protocol}")

    # Add location placeholders + practice-level placeholders (PRACTICE_FOOTER,
    # DISCLAIMER, logo metadata). render_pdf_print's own pipeline below also
    # merges practice_replacements in for QR-bearing pages, but the band HTML +
    # DOCX paths need them merged here so {{DISCLAIMER}} resolves in the band
    # mobile pages and DOCX outputs.
    replacements = {**replacements, **build_location_placeholders(location, lang),
                    **build_practice_placeholders(lang, logo=logo, doctors=doctors)}

    lang_suffix = "" if lang == "en" else f"-{lang}"
    loc_suffix = "SCC" if location_id == "scc" else location_id.upper()

    if flat:
        target_dir = out_dir
    else:
        lang_label = {"en": "English", "es": "Spanish"}[lang]
        folder_key = f"folder_{lang}"
        band_folder = band.get(folder_key) or band.get("folder_en") or stem
        target_dir = out_dir / lang_label / band_folder
        target_dir.mkdir(parents=True, exist_ok=True)

    if fmt == "html":
        template = TEMPLATES / f"{protocol}.{lang}.html"
        out = target_dir / f"bowel-prep-{stem}-mobile{lang_suffix}.html"
        render_html(template, replacements, out)
    elif fmt == "docx":
        template = TEMPLATES / f"{protocol}.{lang}.docx"
        out = target_dir / f"bowel-prep-{stem}-{loc_suffix}{lang_suffix}.docx"
        render_docx(template, replacements, out, mobile_path=band.get("mobile_path"), lang=lang, location=location)
    elif fmt == "pdf-print":
        # Variant-aware template selection. The combined variant ships per-protocol
        # templates: combined-print (standard bands), combined-infant-print (oral
        # MiraLAX infants), and combined-infant-enema-print (clear-liquids + saline-
        # enema infants). The standard variant uses the protocol-specific print
        # template — except for SUPREP, which reuses its mobile template (the
        # @media print rules in that template handle the letter-paper layout,
        # so we don't maintain a separate suprep-print template).
        if variant == "combined":
            if protocol == "standard":
                template = TEMPLATES / f"combined-print.{lang}.html"
            elif protocol == "infant":
                template = TEMPLATES / f"combined-infant-print.{lang}.html"
            elif protocol == "infant-enema":
                template = TEMPLATES / f"combined-infant-enema-print.{lang}.html"
            else:
                raise ValueError(f"Unknown protocol for combined variant: {protocol!r}")
        elif protocol == "suprep-standard":
            template = TEMPLATES / f"suprep-standard-print.{lang}.html"
        elif protocol == "lactulose-infant":
            template = TEMPLATES / f"lactulose-infant-print.{lang}.html"
        elif protocol == "lactulose-standard":
            template = TEMPLATES / f"lactulose-standard-print.{lang}.html"
        elif protocol == "clenpiq-standard":
            template = TEMPLATES / f"clenpiq-standard-print.{lang}.html"
        else:
            template = TEMPLATES / f"{protocol}-print.{lang}.html"
        # Calm theme reuses the base per-protocol/variant template and swaps in
        # the shared Calm stylesheet inside render_pdf_print — no separate
        # calm/ template files. Works for every family that shares the print
        # class vocabulary.
        theme_suffix = "" if theme == "color" else f"-{theme}"
        variant_suffix = "-combined" if variant == "combined" else ""
        out = target_dir / f"bowel-prep-{stem}-{loc_suffix}{lang_suffix}-print{theme_suffix}{variant_suffix}.pdf"
        render_pdf_print(template, replacements, out,
                         mobile_path=band.get("mobile_path"), lang=lang, location=location, theme=theme,
                         variant=variant, logo=logo, legal=legal, doctors=doctors)
    else:
        raise ValueError(f"Unknown format: {fmt}")
    return out


# ---------------------------------------------------------------------------
# Internal-staff cheat-sheet renderer (doses.giready.com + staff PDF).
#
# Single template at templates/cheatsheet.html with both @media screen rules
# (for the public mobile page) and @page print rules (for the staff PDF).
# All dosing numbers come from dosing.yaml so the cheat-sheet never drifts
# from the patient handouts that read the same file.
# ---------------------------------------------------------------------------

def _cs_strip_paren(s):
    """Strip a trailing parenthetical: '3 oz (~90 mL)' -> '3 oz'."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()


def _cs_shorten_window(s):
    """'5:00 PM to 9:00 PM' -> '5:00–9:00 PM'. Keeps both AM/PM if they differ."""
    m = re.match(r"^\s*(\d{1,2}:\d{2})\s*(AM|PM)\s+to\s+(\d{1,2}:\d{2})\s*(AM|PM)\s*$", s)
    if not m:
        return s
    s1, p1, s2, p2 = m.groups()
    if p1 == p2:
        return f"{s1}–{s2} {p2}"
    return f"{s1} {p1}–{s2} {p2}"


def _cs_short_band_label(label_en):
    """'Under 5 kg (under 11 lb)' -> '<5 kg' for the lactulose-infant cheat-sheet rows."""
    base = label_en.split(" (")[0]
    return {"Under 5 kg": "&lt;5 kg"}.get(base, base)


def _cs_spoon_short(s):
    """'5 mL (one teaspoon)' -> '5 mL (1 tsp)' — staff-cheat-sheet compact form."""
    return (s.replace("one teaspoon", "1 tsp")
              .replace("two teaspoons", "2 tsp")
              .replace("one tablespoon", "1 tbsp"))


def render_cheatsheet(dosing_data, out_dir):
    """Render doses.giready.com cheat-sheet (index.html + bowel-prep-cheatsheet.pdf).

    One template (templates/cheatsheet.html) → both outputs, so the print PDF
    and the on-screen page can never drift.
    """
    from datetime import date, datetime

    template_path = TEMPLATES / "cheatsheet.html"
    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    bands_by_id = {b["id"]: b for b in dosing_data["bands"]}
    locations = dosing_data["locations"]
    scc, pmch = locations["scc"], locations["pmch"]

    practice = _practice()
    office_phone = practice["practice"]["phone"]

    # "Last updated" = the last dosing.yaml change, not the build date.
    # Using today's date made every cross-day rebuild dirty doses-giready
    # (and claimed an update that never happened). Git commit date first;
    # file mtime as the no-git fallback.
    import subprocess
    try:
        iso = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", str(DOSING_PATH)],
            cwd=SKILL_DIR, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        updated = date.fromisoformat(iso)
    except (ValueError, OSError, subprocess.SubprocessError):
        updated = datetime.fromtimestamp(DOSING_PATH.stat().st_mtime).date()
    today_str = updated.strftime("%B %-d, %Y")

    std_ids = ["15-20", "21-30", "31-40", "41-50", "over-50"]

    # MiraLAX standard table — 5 rows.
    std_rows = []
    for bid in std_ids:
        b = bands_by_id[bid]
        cup = _cs_strip_paren(b["drink_cup_en"])
        std_rows.append(
            f'          <tr>'
            f'<td class="band">{b["label_en"]}</td>'
            f'<td class="num">{b["dulcolax_bedtime_tablets"]} + {b["dulcolax_dayof_tablets"]}</td>'
            f'<td class="num">{b["dulcolax_mg_total"]} mg</td>'
            f'<td class="num">{b["miralax_capfuls"]}</td>'
            f'<td class="num">{b["miralax_grams"]} g</td>'
            f'<td class="num">{b["gatorade_oz"]} oz</td>'
            f'<td class="muted">{cup}</td>'
            f'<td class="num">{b["miralax_time"]}</td>'
            f'</tr>'
        )
    std_rows_html = "\n".join(std_rows)

    # Infant MiraLAX sub-band rows — 3 tiers + saline-enema row (10 mL/kg is
    # a clinical norm for pediatric enemas, so it's left static here).
    infant = bands_by_id["under-15"]
    infant_rows = []
    for t in infant.get("miralax_infant_tiers", []):
        infant_rows.append(
            f'          <tr>'
            f'<td class="band">{t["label_en"]}</td>'
            f'<td class="num">{t["capfuls_label_en"]}</td>'
            f'<td class="num">{t["grams_label_en"]}</td>'
            f'<td class="muted">{t["mix_in_en"]}</td>'
            f'</tr>'
        )
    # Explicit 4 cells (no colspan) — a colspan cell trips WeasyPrint's tagged-PDF
    # output (the tag tree omits /ColSpan, so veraPDF reads a ragged row → PDF/UA
    # clause 7.2-43). MiraLAX caps / Tot. g are N/A for a saline enema (em-dash);
    # the note rides in the wide "Mix in" column like the tier rows above.
    infant_rows.append(
        '          <tr>'
        '<td class="band">Saline enema</td>'
        '<td class="num">—</td>'
        '<td class="num">—</td>'
        '<td class="muted">10 mL/kg — evening at home if directed, otherwise by staff at facility</td>'
        '</tr>'
    )
    infant_rows_html = "\n".join(infant_rows)

    # Contingency rescue rows — 5 rows.
    cont_rows = []
    for bid in std_ids:
        b = bands_by_id[bid]
        cont_rows.append(
            f'          <tr>'
            f'<td class="band">{b["label_en"]}</td>'
            f'<td class="num">{b["contingency_evening_caps"]} cap / {b["contingency_evening_oz"]} oz</td>'
            f'<td class="num">{b["contingency_morning_caps"]} cap / {b["contingency_morning_oz"]} oz</td>'
            f'<td class="num hi">{b["contingency_total_caps"]} caps ({b["contingency_total_grams"]} g)</td>'
            f'<td class="muted">{b["cheatsheet_contingency_backup"]}</td>'
            f'</tr>'
        )
    cont_rows_html = "\n".join(cont_rows)

    # Pre-cleanout rows — 5 rows.
    prec_rows = []
    for bid in std_ids:
        b = bands_by_id[bid]
        prec_rows.append(
            f'          <tr>'
            f'<td class="band">{b["label_en"]}</td>'
            f'<td>{b["cheatsheet_disimpaction"]}</td>'
            f'<td class="num">weekend</td>'
            f'<td>{b["cheatsheet_maintenance"]}</td>'
            f'</tr>'
        )
    prec_rows_html = "\n".join(prec_rows)

    # GLP-1 hold band list — driven by include_glp1_warning across standard bands.
    glp1_bands = [bands_by_id[bid]["label_en"] for bid in std_ids
                  if bands_by_id[bid].get("include_glp1_warning")]
    glp1_hold_str = ", ".join(glp1_bands) if glp1_bands else "none"

    # CLENPIQ rows — 2 doses.
    cl = bands_by_id["clenpiq"]
    cl_cup = _cs_strip_paren(cl["drink_cup_en"])
    cl_window = _cs_shorten_window(cl["dose1_window_en"])
    clenpiq_rows_html = "\n".join([
        f'          <tr>'
        f'<td class="band">Evening</td>'
        f'<td class="num-wrap">1 &times; {cl["clenpiq_bottle_oz"]} oz ({cl["clenpiq_bottle_ml"]} mL)</td>'
        f'<td class="num">{cl_window}</td>'
        f'<td class="num-wrap">{cl["dose1_clears_cups"]} &times; 8 oz cup over {cl["dose1_clears_hours"]} h ({cl["dose1_clears_oz"]} oz)</td>'
        f'<td class="muted">{cl_cup}</td>'
        f'</tr>',
        f'          <tr>'
        f'<td class="band">Morning</td>'
        f'<td class="num-wrap">1 &times; {cl["clenpiq_bottle_oz"]} oz ({cl["clenpiq_bottle_ml"]} mL)</td>'
        f'<td class="num-wrap">{cl["dose2_hours_before_min"]}–{cl["dose2_hours_before_max"]} h before proc.</td>'
        f'<td class="num-wrap">{cl["dose2_clears_cups"]} &times; 8 oz cup ({cl["dose2_clears_oz"]} oz)</td>'
        f'<td class="muted">{cl_cup}</td>'
        f'</tr>',
    ])
    clenpiq_eligibility = cl["summary_label_en"].split(" — ")[0]

    # SUPREP rows — 2 doses.
    sp = bands_by_id["suprep"]
    sp_cup = _cs_strip_paren(sp["drink_cup_en"])
    sp_window = _cs_shorten_window(sp["dose1_window_en"])
    suprep_rows_html = "\n".join([
        f'          <tr>'
        f'<td class="band">Evening</td>'
        f'<td class="num-wrap">1 bottle ({sp["suprep_bottle_oz"]} oz) + water to {sp["suprep_fill_line_oz"]}-oz fill line</td>'
        f'<td class="num">{sp_window}</td>'
        f'<td class="num-wrap">{sp["dose1_chaser_fills"]} &times; {sp["suprep_fill_line_oz"]} oz fills ({sp["dose1_chasers_oz"]} oz) over {sp["dose1_chasers_hours"]} h</td>'
        f'<td class="muted">{sp_cup}</td>'
        f'</tr>',
        f'          <tr>'
        f'<td class="band">Morning</td>'
        f'<td class="num-wrap">1 bottle ({sp["suprep_bottle_oz"]} oz) + water to {sp["suprep_fill_line_oz"]}-oz fill line</td>'
        f'<td class="num-wrap">{sp["dose_separation_hours_min"]}–{sp["dose_separation_hours_max"]} h after Dose 1 &middot; ≥ {sp["dose2_hours_before_min"]} h before proc.</td>'
        f'<td class="num-wrap">{sp["dose2_chaser_fills"]} &times; {sp["suprep_fill_line_oz"]} oz fills ({sp["dose2_chasers_oz"]} oz) over {sp["dose2_chasers_hours"]} h</td>'
        f'<td class="muted">{sp_cup}</td>'
        f'</tr>',
    ])
    suprep_eligibility = sp["summary_label_en"].split(" — ")[0]

    # Lactulose big-prep rows — 15-17, 18-20 (from 15-20-lact tiers), then 21-30 (single tier).
    l1520 = bands_by_id["15-20-lact"]
    l2130 = bands_by_id["21-30-lact"]
    lact_std_rows = []
    for src_band in (l1520, l2130):
        for tier in src_band["lactulose_big_prep_tiers"]:
            lact_std_rows.append(
                f'          <tr>'
                f'<td class="band">{tier["label_en"]}</td>'
                f'<td class="num">{src_band["dulcolax_bedtime_tablets"]} + {src_band["dulcolax_dayof_tablets"]}</td>'
                f'<td class="num">{tier["lactulose_ml"]} mL</td>'
                f'<td class="num">{tier["gatorade_oz"]} oz</td>'
                f'<td class="muted">{src_band["drink_cup_oz"]} oz</td>'
                f'<td class="num">{src_band["lactulose_time"]}</td>'
                f'</tr>'
            )
    lact_std_rows_html = "\n".join(lact_std_rows)

    # Lactulose infant rows — 3 weight tiers from under-15-lact.
    li = bands_by_id["under-15-lact"]
    lact_inf_rows = []
    for tier in li["lactulose_daily_tiers"]:
        lact_inf_rows.append(
            f'          <tr>'
            f'<td class="band">{_cs_short_band_label(tier["label_en"])}</td>'
            f'<td class="num">{_cs_spoon_short(tier["dose_label_en"])}</td>'
            f'<td class="muted">{tier["cheatsheet_mix_in_en"]}</td>'
            f'</tr>'
        )
    lact_inf_rows_html = "\n".join(lact_inf_rows)

    # Lactulose footnote — rescue volumes per band + pre-cleanout pattern.
    lact_footnote = (
        f'<strong>Rescue (15–20 kg):</strong> '
        f'{l1520["rescue_evening_lactulose_ml"]} mL / {l1520["rescue_evening_gatorade_oz"]} oz evening, '
        f'{l1520["rescue_morning_lactulose_ml"]} mL / {l1520["rescue_morning_gatorade_oz"]} oz morning. '
        f'<strong>Rescue (21–30 kg):</strong> '
        f'{l2130["rescue_evening_lactulose_ml"]} mL / {l2130["rescue_evening_gatorade_oz"]} oz evening, '
        f'{l2130["rescue_morning_lactulose_ml"]} mL / {l2130["rescue_morning_gatorade_oz"]} oz morning. '
        f'<strong>Pre-cleanout:</strong> 15 mL &times; 2/day (15–20 kg) or &times; 3/day (21–30 kg) &times; 3 d.'
    )

    scc_mobile_lines = f'{scc["mobile_subdomain"]}.giready.com<br>{scc["mobile_subdomain_combined"]}.giready.com'
    pmch_mobile_lines = f'{pmch["mobile_subdomain"]}.giready.com<br>{pmch["mobile_subdomain_combined"]}.giready.com'

    replacements = {
        "{{LAST_UPDATED}}": today_str,
        "{{OFFICE_PHONE}}": office_phone,
        "{{STANDARD_BAND_ROWS}}": std_rows_html,
        "{{INFANT_SUB_ROWS}}": infant_rows_html,
        "{{CONTINGENCY_ROWS}}": cont_rows_html,
        "{{CONTINGENCY_TRIGGER_HOURS}}": str(bands_by_id["15-20"]["contingency_trigger_hours"]),
        "{{PRECLEANOUT_ROWS}}": prec_rows_html,
        "{{NPO_SCC_HOURS}}": str(scc["clears_npo_hours"]),
        "{{NPO_PMCH_HOURS}}": str(pmch["clears_npo_hours"]),
        "{{LOC_SCC_NAME}}": scc["cheatsheet_name"],
        "{{LOC_SCC_ADDRESS}}": scc["address"],
        "{{LOC_SCC_PHONE}}": scc["phone"],
        "{{LOC_SCC_ARRIVAL_MIN}}": str(scc["arrival_minutes_before"]),
        "{{LOC_SCC_MOBILE_LINES}}": scc_mobile_lines,
        "{{LOC_PMCH_NAME}}": pmch["cheatsheet_name"],
        "{{LOC_PMCH_ADDRESS}}": pmch["address"],
        "{{LOC_PMCH_PHONE}}": pmch["phone"],
        "{{LOC_PMCH_ARRIVAL_MIN}}": str(pmch["arrival_minutes_before"]),
        "{{LOC_PMCH_MOBILE_LINES}}": pmch_mobile_lines,
        "{{GLP1_HOLD_BANDS}}": glp1_hold_str,
        "{{CLENPIQ_ROWS}}": clenpiq_rows_html,
        "{{CLENPIQ_ELIGIBILITY}}": clenpiq_eligibility,
        "{{CLENPIQ_BOTTLES}}": str(cl["clenpiq_total_bottles"]),
        "{{SUPREP_ROWS}}": suprep_rows_html,
        "{{SUPREP_ELIGIBILITY}}": suprep_eligibility,
        "{{SUPREP_AGE_FLOOR}}": str(sp["suprep_age_floor"]),
        "{{LACTULOSE_STANDARD_ROWS}}": lact_std_rows_html,
        "{{LACTULOSE_INFANT_ROWS}}": lact_inf_rows_html,
        "{{LACTULOSE_FOOTNOTE}}": lact_footnote,
        # Cheatsheet is English-only (staff reference).
        **build_practice_placeholders("en"),
    }
    for token, value in replacements.items():
        html = html.replace(token, value)

    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders in cheatsheet: {sorted(set(unreplaced))}")

    html = _inject_shared_print_css(html)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "index.html"
    pdf_path = out_dir / "bowel-prep-cheatsheet.pdf"

    # Screen copy (doses.giready.com) gets the shared a11y base (focus, table
    # scope, keyboard); the PDF copy does not (print ignores it and WeasyPrint
    # doesn't run the JS anyway).
    index_path.write_text(_inject_shared_mobile_a11y(html), encoding="utf-8")

    # Regenerate _headers so the CSP script-src hash covers the inline a11y
    # script we just added (self-maintaining, same mechanism as the site builds).
    try:
        from header_config import write_headers
        write_headers(out_dir)
    except Exception:
        pass

    _ensure_weasyprint_libpath()
    from weasyprint import HTML  # type: ignore
    from pdf_tagging import write_pdf_tagged
    write_pdf_tagged(HTML(string=html, base_url=str(TEMPLATES)), str(pdf_path))

    return [index_path, pdf_path]


def load_dosing():
    with open(DOSING_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


def main():
    ap = argparse.ArgumentParser(description="Render bowel prep handouts from dosing.yaml")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--band", default="all", help="Band id (default: all)")
    ap.add_argument("--lang", default="both", choices=["en", "es", "both"])
    ap.add_argument("--format", default="both",
                    choices=["html", "docx", "both", "pdf-print", "cheatsheet"],
                    help="'cheatsheet' renders the doses.giready.com index.html + "
                         "bowel-prep-cheatsheet.pdf (internal staff reference) and "
                         "ignores --band/--lang/--theme/--variant.")
    ap.add_argument("--location", default="scc", help="Location id (scc or pmch). Default: scc")
    ap.add_argument("--theme", default="color", choices=["color", "print-light", "calm"],
                    help="Color theme for pdf-print: 'color' (default), 'print-light' (toner-friendly), "
                         "or 'calm' (Calm visual language — Phase A pilot, standard colonoscopy only).")
    ap.add_argument("--variant", default="standard", choices=["standard", "combined"],
                    help="Document family for pdf-print: 'standard' (colonoscopy-only, default) "
                         "or 'combined' (EGD + colonoscopy back-to-back). 'combined' renders all "
                         "protocols (standard + both infant variants) using per-protocol "
                         "combined-*-print templates and the location's mobile_subdomain_combined "
                         "for QRs.")
    ap.add_argument("--flat", action="store_true",
                    help="Write all files directly into --out instead of nesting "
                         "under Language/Weight-band subfolders")
    # Fork toggles (print PDFs). Defaults = public-website behavior.
    ap.add_argument("--logo", default="giready", choices=["giready", "pmch"],
                    help="Cover logo: 'giready' (default, public brand) or 'pmch' (internal Drive binder).")
    ap.add_argument("--legal", default="on", choices=["on", "off"],
                    help="Legal footer (disclaimer + privacy/terms): 'on' (default) or 'off' "
                         "(internal Drive binder / scheduler).")
    ap.add_argument("--doctors", default="none", choices=["none", "all"],
                    help="Doctor names: 'none' (default — public PDFs carry no doctor names) or "
                         "'all' (internal Drive binder lists every partner).")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    dosing_data = load_dosing()

    # The cheat-sheet is a single, all-bands document that renders the public
    # doses.giready.com page + an internal staff PDF in one pass. It skips the
    # per-band/per-language loop below.
    if args.format == "cheatsheet":
        written = render_cheatsheet(dosing_data, out_dir)
        for path in written:
            print(f"  wrote {path}")
        print(f"\n{len(written)} file(s) written to {out_dir} (format=cheatsheet)")
        return

    bands = dosing_data["bands"]
    locations = dosing_data.get("locations", {})
    if args.location not in locations:
        sys.exit(f"ERROR: location {args.location!r} not found in dosing.yaml (available: {list(locations.keys())})")
    location = locations[args.location]

    if args.band != "all":
        bands = [b for b in bands if b["id"] == args.band]
        if not bands:
            sys.exit(f"ERROR: band id {args.band!r} not found in dosing.yaml")
    else:
        # SUPREP, lactulose, and CLENPIQ are scheduler-only alternative
        # preps — exclude them from the default --band all pass so they
        # don't end up mixed in with the standard MiraLAX colonoscopy
        # folder. The Makefile's render-pdf-{suprep,lactulose,clenpiq}
        # targets opt in explicitly by passing each band id.
        bands = [b for b in bands
                 if not b["protocol"].startswith(("suprep", "lactulose", "clenpiq"))]

    # Combined variant renders all protocols (standard + both infant variants);
    # the per-protocol template is picked inside render_band.
    if args.variant == "combined":
        if not bands:
            sys.exit("ERROR: --variant combined produced no bands to render.")

    langs = ["en", "es"] if args.lang == "both" else [args.lang]
    # `both` keeps its existing meaning (html + docx) so existing behavior is unchanged;
    # `pdf-print` is its own format that must be requested explicitly.
    formats = ["html", "docx"] if args.format == "both" else [args.format]

    # Combined variant only makes sense for the print PDF — there's no combined HTML/DOCX.
    if args.variant == "combined" and any(f != "pdf-print" for f in formats):
        sys.exit("ERROR: --variant combined is only supported with --format pdf-print.")

    written = []
    for band in bands:
        for lang in langs:
            for fmt in formats:
                out = render_band(band, lang, fmt, out_dir, flat=args.flat,
                                  location=location, location_id=args.location, theme=args.theme,
                                  variant=args.variant, logo=args.logo, legal=args.legal,
                                  doctors=args.doctors)
                # render_band returns None when a band/format combination is
                # intentionally skipped (e.g. lactulose protocols only ship
                # mobile HTML in Phase 1; combined variant skips lactulose).
                if out is None:
                    continue
                written.append(out)
                print(f"  wrote {out}")

    print(f"\n{len(written)} file(s) written to {out_dir} (location={args.location}, variant={args.variant})")


if __name__ == "__main__":
    main()
