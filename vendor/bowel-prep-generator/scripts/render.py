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
import datetime
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml --break-system-packages\n")
    sys.exit(1)

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
DOSING_PATH = SKILL_DIR / "data" / "dosing.yaml"
PRACTICE_PATH = SKILL_DIR / "practice.yaml"


# §2 revision-date stamp — sourced from the most recent git commit touching
# templates/, data/dosing.yaml, or practice.yaml. Falls back to today if git
# is unavailable (e.g., the skill is being run from a tarball checkout).
SPANISH_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _revision_date():
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%cI", "--",
             "templates", "data/dosing.yaml", "practice.yaml"],
            cwd=SKILL_DIR, text=True, stderr=subprocess.DEVNULL).strip()
        if out:
            return datetime.date.fromisoformat(out[:10])
    except Exception:
        pass
    return datetime.date.today()


def _revision_date_str(lang):
    d = _revision_date()
    if lang == "es":
        return f"{d.day} de {SPANISH_MONTHS[d.month - 1]} de {d.year}"
    return d.strftime("%B %-d, %Y")


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


REMOVE_PARAGRAPH_MARKER = "__OMIT_PARAGRAPH__"


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
            f'  <p style="margin: 0 0 8px;">{intro_two}</p>\n'
            f'  <ul class="precleanout-options" style="margin: 0 0 8px; padding-left: 20px;">\n'
            f'{_opt_li(a_lbl, a_text, a_off)}\n'
            f'{_opt_li(b_lbl, b_text, b_off)}\n'
            f'  </ul>\n'
            f'  <p style="margin: 0;">{outro}</p>'
        )
        return (
            f'<div class="callout">\n'
            f'  <div class="callout-title">{title}</div>\n'
            f'{body}\n'
            f'</div>'
        )

    # Single-option: separate lead, dose, maintenance, and outro into 4 paragraphs
    # so the MiraLAX dose stands on its own line at-a-glance. Dose-token <strong>
    # tags come from the YAML (precleanout_a_text_{lang}); the maintenance string
    # already wraps the daily dose in <strong>.
    a_date_span = ""
    if a_off is not None:
        a_date_span = (f'<span data-pz-day="{a_off}" '
                       f'data-pz-template="{date_tmpl}"></span>')
    if lang == "en":
        lead = ("&#9888;&#65039; <strong>If any history or suspicion of "
                "constipation, please do a pre-cleanout:</strong>")
    else:
        lead = ("&#9888;&#65039; <strong>Si hay antecedente o sospecha de "
                "estreñimiento, haga una pre-limpieza:</strong>")
    maintenance_p = (f'  <p style="margin: 0 0 6px;">{maintenance}</p>\n'
                     if maintenance else "")
    body = (
        f'  <p style="margin: 0 0 6px;">{lead}</p>\n'
        f'  <p style="margin: 0 0 6px;">{a_text}{a_date_span}</p>\n'
        f'{maintenance_p}'
        f'  <p style="margin: 0;">{outro}</p>'
    )
    return (
        f'<div class="callout">\n'
        f'{body}\n'
        f'</div>'
    )


def build_contingency_block(band, lang, location):
    """Backup-plan block shown when the BIG PREP isn't producing clear/yellow stools.

    Per-band dosing fields drive the rescue capfuls/oz; the location's
    `clears_npo_hours` drives the morning cutoff (2 h SCC, 3 h PMCH).

    A `pz-only` span is appended to the morning step so that, when the family
    has personalized the procedure date+time on the mobile page, the rescue
    cutoff is shown as a concrete clock time. The static text ("at least N
    hours before the procedure time") is what the print PDF shows.
    """
    if band.get("protocol") != "standard":
        return ""
    npo_hours = location.get("clears_npo_hours", 2) if location else 2
    npo_minutes = npo_hours * 60
    trigger = band.get("contingency_trigger_hours", 4)
    ev_caps = band["contingency_evening_caps"]
    ev_oz = band["contingency_evening_oz"]
    mn_caps = band["contingency_morning_caps"]
    mn_oz = band["contingency_morning_oz"]
    total_caps = band["contingency_total_caps"]
    total_g = band["contingency_total_grams"]
    backup = band[f"contingency_backup_{lang}"]
    practice = _practice()["practice"]
    office_phone = practice.get("phone", "")
    office_tel = practice.get("phone_tel", re.sub(r"\D", "", office_phone))
    phone_link = f'<a href="tel:{office_tel}">{office_phone}</a>' if office_phone else ""

    if lang == "en":
        cap_word_ev = "capfuls" if ev_caps != 1 else "capful"
        cap_word_mn = "capfuls" if mn_caps != 1 else "capful"
        morning_pz = (f'<span class="pz-only" data-pz-time-mins="-{npo_minutes}" '
                      f'data-pz-template=" &mdash; by {{time}}"></span>')
        return (
            '<div class="contingency-body">\n'
            f'  <p class="contingency-lead"><strong>If the prep isn\'t working &mdash; backup plan:</strong> '
            f'if your child\'s stools are still <strong>not clear or pale yellow {trigger} hours after '
            f'starting the BIG PREP</strong>, follow this rescue plan.</p>\n'
            '  <ul>\n'
            f'    <li><strong>Evening (continue MiraLAX):</strong> Give <strong>{ev_caps} more {cap_word_ev} '
            f'of MiraLAX in {ev_oz} oz of Gatorade</strong>, ending by bedtime.</li>\n'
            f'    <li><strong>Morning of procedure:</strong> If stools are still not clear or pale yellow, '
            f'give <strong>{mn_caps} more {cap_word_mn} in {mn_oz} oz of Gatorade</strong>, finishing '
            f'<strong>at least {npo_hours} hours before</strong> the procedure time{morning_pz}.</li>\n'
            f'    <li><strong>Do not exceed a total of {total_caps} capfuls ({total_g} g) of MiraLAX</strong> '
            f'for the day.</li>\n'
            f'    <li>If stools are still not clear after the rescue plan: <strong>call our office at '
            f'{phone_link}</strong>. {backup}</li>\n'
            '  </ul>\n'
            '</div>'
        )
    # Spanish
    cap_word_ev = "tapas" if ev_caps != 1 else "tapa"
    cap_word_mn = "tapas" if mn_caps != 1 else "tapa"
    morning_pz = (f'<span class="pz-only" data-pz-time-mins="-{npo_minutes}" '
                  f'data-pz-template=" &mdash; antes de las {{time}}"></span>')
    return (
        '<div class="contingency-body">\n'
        f'  <p class="contingency-lead"><strong>Si la preparación no está funcionando &mdash; plan de respaldo:</strong> '
        f'si las heces de su niño aún <strong>no son claras o amarillas pálidas {trigger} horas después '
        f'de iniciar la BIG PREP</strong>, siga este plan de rescate.</p>\n'
        '  <ul>\n'
        f'    <li><strong>Por la noche (continúe el MiraLAX):</strong> Dé <strong>{ev_caps} {cap_word_ev} más '
        f'de MiraLAX en {ev_oz} oz de Gatorade</strong>, terminando antes de acostarse.</li>\n'
        f'    <li><strong>La mañana del procedimiento:</strong> Si las heces aún no son claras o amarillas pálidas, '
        f'dé <strong>{mn_caps} {cap_word_mn} más en {mn_oz} oz de Gatorade</strong>, terminando '
        f'<strong>al menos {npo_hours} horas antes</strong> del procedimiento{morning_pz}.</li>\n'
        f'    <li><strong>No exceda un total de {total_caps} tapas ({total_g} g) de MiraLAX</strong> '
        f'en el día.</li>\n'
        f'    <li>Si las heces aún no son claras después del plan de rescate: <strong>llame a nuestro consultorio al '
        f'{phone_link}</strong>. {backup}</li>\n'
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


def build_strings(band, lang, location=None):
    """Return a dict of placeholder → rendered string for a standard-protocol band.

    `location` is needed for the contingency block's NPO-window interpolation; if
    omitted (e.g., legacy callers), it falls back to the SCC default of 2 hours.
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

    # §6 shopping totals — buy enough MiraLAX/Gatorade to cover BIG PREP plus
    # the largest single-day extra (rescue plan or weekend intensive pre-cleanout,
    # whichever dominates per band). See plan §6 for the locked numbers.
    rescue_caps = (band.get("contingency_evening_caps", 0)
                   + band.get("contingency_morning_caps", 0))
    intensive_caps = band.get("precleanout_intensive_caps", 0)
    shop_caps = capfuls + max(rescue_caps, intensive_caps)
    extra_caps = shop_caps - capfuls
    shop_grams = int(round((grams + extra_caps * grams / capfuls) / 10) * 10)
    shop_oz = band.get("shopping_gatorade_oz", oz)
    shop_ml = oz_to_ml(shop_oz)

    def tablet_word_en(n): return "tablet" if n == 1 else "tablets"
    def tablet_word_es(n): return "tableta" if n == 1 else "tabletas"

    # Per-band time + cup overrides (for the 15-20 special schedule)
    dayof_time = band.get("dulcolax_dayof_time", "2:00 PM")
    miralax_time = band.get("miralax_time", "3:00 PM")
    drink_cup = band.get(f"drink_cup_{lang}", "1 cup (8 oz)" if lang == "en" else "1 taza (8 oz)")

    if lang == "en":
        tablet_word = tablet_word_en(tabs)
        html_dulcolax_short = f"{tabs} {tablet_word} ({mg} mg)"
        html_miralax_short = f"{capfuls} capfuls (~{grams} g{note})"
        html_gatorade_vol = f"{oz} oz (~{ml} mL)"
        html_miralax_shopping = f"{shop_caps} capfuls (~{shop_grams} g)"
        html_gatorade_shopping = f"{shop_oz} oz (~{shop_ml} mL)"

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
            f"{capfuls} capfuls (~{grams} g{note}) of MiraLAX mixed into "
            f"{oz} oz (~{ml} mL) of clear Gatorade (no red or purple)"
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
        html_gatorade_vol = f"{oz} oz (~{ml} mL)"
        html_miralax_shopping = f"{shop_caps} tapas (~{shop_grams} g)"
        html_gatorade_shopping = f"{shop_oz} oz (~{shop_ml} mL)"

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
            f"{capfuls} tapas (~{grams} g{note}) de MiraLAX mezcladas en "
            f"{oz} oz (~{ml} mL) de Gatorade transparente (sin rojo ni morado)"
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
                f'                <div class="what">Give Dulcolax tablets — <strong>{bedtime_dose_text}</strong> — with a sip of water.</div>\n'
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
                f'                <div class="what">Dé las tabletas de Dulcolax — <strong>{bedtime_dose_text}</strong> — con un sorbo de agua.</div>\n'
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
    miralax_dose_phrase_en = f"{capfuls} capfuls (~{grams} g{note}) of MiraLAX in {oz} oz (~{ml} mL) of Gatorade"
    miralax_dose_phrase_es = f"{capfuls} tapas (~{grams} g{note}) de MiraLAX en {oz} oz (~{ml} mL) de Gatorade"
    miralax_dose_phrase = miralax_dose_phrase_en if lang == "en" else miralax_dose_phrase_es

    if dayof_time == miralax_time and dayof_tabs > 0:
        if lang == "en":
            html_prep_medicine_block = (
                '<div class="time-box">\n'
                f'  <div class="when">{miralax_time}</div>\n'
                '  <div class="what">\n'
                f'    Give Dulcolax tablets &mdash; <strong>{html_dulcolax_dayof_short}</strong> &mdash; with a sip of water,<br>\n'
                f'    then start the MiraLAX solution &mdash; <strong>{miralax_dose_phrase}</strong> &mdash; from the fridge.<br>\n'
                f'    Have your child drink <strong>{drink_cup} every 30 minutes</strong> until finished.\n'
                '  </div>\n'
                '</div>'
            )
        else:
            html_prep_medicine_block = (
                '<div class="time-box">\n'
                f'  <div class="when">{miralax_time}</div>\n'
                '  <div class="what">\n'
                f'    Dé las tabletas de Dulcolax &mdash; <strong>{html_dulcolax_dayof_short}</strong> &mdash; con un sorbo de agua,<br>\n'
                f'    luego comience la solución de MiraLAX &mdash; <strong>{miralax_dose_phrase}</strong> &mdash; del refrigerador.<br>\n'
                f'    Haga que su niño beba <strong>{drink_cup} cada 30 minutos</strong> hasta terminar.\n'
                '  </div>\n'
                '</div>'
            )
    else:
        # Times differ (15-20 kg) — render two separate time-boxes.
        if lang == "en":
            html_prep_medicine_block = (
                '<div class="time-box">\n'
                f'  <div class="when">{dayof_time}</div>\n'
                f'  <div class="what">Give Dulcolax tablets &mdash; <strong>{html_dulcolax_dayof_short}</strong> &mdash; with a sip of water.</div>\n'
                '</div>\n'
                '<div class="time-box">\n'
                f'  <div class="when">{miralax_time}</div>\n'
                '  <div class="what">\n'
                f'    Start the MiraLAX solution &mdash; <strong>{miralax_dose_phrase}</strong> &mdash; from the fridge.<br>\n'
                f'    Have your child drink <strong>{drink_cup} every 30 minutes</strong> until finished.\n'
                '  </div>\n'
                '</div>'
            )
        else:
            html_prep_medicine_block = (
                '<div class="time-box">\n'
                f'  <div class="when">{dayof_time}</div>\n'
                f'  <div class="what">Dé las tabletas de Dulcolax &mdash; <strong>{html_dulcolax_dayof_short}</strong> &mdash; con un sorbo de agua.</div>\n'
                '</div>\n'
                '<div class="time-box">\n'
                f'  <div class="when">{miralax_time}</div>\n'
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
        "{{HTML_MIRALAX_SHORT}}": html_miralax_short,
        "{{HTML_GATORADE_VOL}}": html_gatorade_vol,
        "{{HTML_MIRALAX_SHOPPING}}": html_miralax_shopping,
        "{{HTML_GATORADE_SHOPPING}}": html_gatorade_shopping,
        "{{HTML_PRECLEANOUT}}": html_precleanout,
        "{{HTML_PRECLEANOUT_BLOCK}}": build_precleanout_block(band, lang),
        "{{HTML_CONTINGENCY_BLOCK}}": build_contingency_block(band, lang, location),
        "{{HTML_MEDICATIONS_DRUGS}}": _medications_drugs(band, lang),
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
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


MOBILE_QR_FILENAME = "word/media/mobile-qr.png"


def _generate_mobile_qr(mobile_path, lang="en", subdomain="prep"):
    """Generate a band-specific mobile-link QR PNG (~150x150 px) for swap-in at render time.
    Spanish renders point at the /es/ subpath; subdomain depends on location ('prep' for SCC, 'prep86' for PMCH)."""
    try:
        import qrcode
        from PIL import Image
        import io as _io
    except ImportError:
        return None
    url = f"https://{subdomain}.giready.com/{mobile_path}/"
    if lang == "es":
        url = url + "es/"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=1)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((150, 150), Image.NEAREST)
    buf = _io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# All values formerly hardcoded here (SCC_MAPS_URL, YOUTUBE_URL_*, PORTAL_URL,
# and the practice info baked into print templates) now live in practice.yaml
# and are read once at startup via _practice().
_PRACTICE_CACHE = None


def _practice():
    """Load practice.yaml once and cache it. Returns the parsed dict."""
    global _PRACTICE_CACHE
    if _PRACTICE_CACHE is None:
        if not PRACTICE_PATH.exists():
            raise RuntimeError(f"practice.yaml not found at {PRACTICE_PATH}. "
                               "This file holds per-practice branding/contact/QR config.")
        with open(PRACTICE_PATH, encoding="utf-8") as f:
            _PRACTICE_CACHE = yaml.safe_load(f)
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


def build_practice_placeholders(lang):
    """Return {{PRACTICE_*}} placeholders sourced from practice.yaml for the given language."""
    p = _practice()["practice"]
    stack = p.get(f"cover_stack_{lang}") or p.get("cover_stack_en") or ["", "", ""]
    # Normalize to exactly 3 lines
    stack = (stack + ["", "", ""])[:3]
    phone = p.get("phone", "")
    phone_tel = p.get("phone_tel", re.sub(r"\D", "", phone))
    rev_date = _revision_date_str(lang)
    # PDF footer carries the revision date appended to the running practice
    # footer so families can tell which version of the handout they have.
    base_footer = p.get(f"footer_{lang}") or p.get("footer_en") or ""
    rev_label = "Updated" if lang == "en" else "Actualizado"
    practice_footer = f"{base_footer}  ·  {rev_label} {rev_date}" if base_footer else rev_date
    return {
        "{{PRACTICE_STACK_LINE_1}}": stack[0],
        "{{PRACTICE_STACK_LINE_2}}": stack[1],
        "{{PRACTICE_STACK_LINE_3}}": stack[2],
        "{{PRACTICE_FOOTER}}":       practice_footer,
        "{{PRACTICE_LOGO_FILE}}":    p.get("logo_filename", ""),
        "{{PRACTICE_LOGO_ALT}}":     p.get("logo_alt", ""),
        "{{PRACTICE_PHONE}}":        phone,
        "{{PRACTICE_PHONE_TEL}}":    phone_tel,
        "{{REVISION_DATE}}":         rev_date,
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
                      variant="standard"):
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
    maps_qr_bytes = _generate_maps_qr(maps_url) if maps_url else None
    youtube_qr_bytes = _generate_maps_qr(youtube_url)
    portal_qr_bytes = _generate_maps_qr(portal_url)
    gikids_qr_bytes = _generate_maps_qr(gikids_url)
    qr_uris = {
        "qr-mobile":  _png_to_data_uri(mobile_qr_bytes),
        "qr-maps":    _png_to_data_uri(maps_qr_bytes),
        "qr-youtube": _png_to_data_uri(youtube_qr_bytes),
        "qr-portal":  _png_to_data_uri(portal_qr_bytes),
        "qr-gikids":  _png_to_data_uri(gikids_qr_bytes),
    }

    # Token-based substitution (stub templates + URL placeholders for clickable links).
    qr_replacements = {
        "{{MOBILE_QR_DATA_URI}}": qr_uris["qr-mobile"],
        "{{MAPS_QR_DATA_URI}}":   qr_uris["qr-maps"],
        "{{MOBILE_URL}}":         mobile_url,
        "{{MAPS_URL}}":            maps_url,
        "{{YOUTUBE_URL}}":         youtube_url,
        "{{PORTAL_URL}}":          portal_url,
        "{{GIKIDS_URL}}":          gikids_url,
        "{{LOCATION_PHONE_TEL}}":  location_phone_tel,
    }
    practice_replacements = build_practice_placeholders(lang)
    # Partials must be merged FIRST so any per-band/QR/practice placeholders that
    # live inside the partial markup are still substituted by the regular pass.
    partials_replacements = _load_partials(lang)
    all_replacements = {**partials_replacements, **replacements, **qr_replacements, **practice_replacements}
    for token, value in all_replacements.items():
        html = html.replace(token, value)

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

    # Resolve relative URLs (e.g. local stub images) against the template directory.
    HTML(string=html, base_url=str(Path(template_path).parent)).write_pdf(str(out_path))


# ---------------------------------------------------------------------------
# Staff cheat-sheet (1-page landscape PDF + on-screen HTML page).
#
# The cheat sheet is a different shape from the patient handouts: a single
# at-a-glance reference of every dosing/timing/policy number across all
# weight bands and both locations. It's deployed to cheatsheet.giready.com
# as an unlisted (X-Robots-Tag: noindex) staff-only page, and is also a
# printable PDF for desk reference.
#
# Source of truth: data/dosing.yaml + practice.yaml. The cheat-sheet builder
# never duplicates dosing numbers — it reads the same fields the patient
# handouts read, so a number change here propagates to both.
# ---------------------------------------------------------------------------

def _strip_for_cell(html_text):
    """Normalize entities for tight table cells. Keeps <strong>/<em> inline."""
    return (html_text or "").replace("&mdash;", "—").replace("&nbsp;", " ")


def build_cheatsheet_replacements(bands_all, locations):
    """Build {{CHEATSHEET_*}} placeholder strings for the staff cheat-sheet template.

    Only the 5 standard-protocol bands populate the dosing/contingency/pre-cleanout
    tables; infant variants get a one-line footnote on the dosing section.
    """
    bands = [b for b in bands_all if b.get("protocol") == "standard"]

    dosing_rows = []
    for b in bands:
        label = b["label_en"]
        bedtime = b.get("dulcolax_bedtime_tablets", 0)
        dayof = b.get("dulcolax_dayof_tablets", 0)
        # Strip the metric "(~NN mL)" portion of drink_cup for the tight cell.
        cup = re.sub(r"\s*\(~[^)]*\)\s*", "", b.get("drink_cup_en", ""))
        dosing_rows.append(
            f'          <tr>'
            f'<td class="band">{label}</td>'
            f'<td class="num">{bedtime} + {dayof}</td>'
            f'<td class="num">{b.get("dulcolax_mg_total", 0)} mg</td>'
            f'<td class="num">{b.get("miralax_capfuls", 0)}</td>'
            f'<td class="num">{b.get("miralax_grams", 0)} g</td>'
            f'<td class="num">{b.get("gatorade_oz", 0)} oz</td>'
            f'<td class="muted">{cup}</td>'
            f'<td class="num">{b.get("miralax_time", "")}</td>'
            f'</tr>'
        )

    # Contingency backup: per-band short stub (full sentence is in patient PDFs).
    def _backup_stub(backup_en):
        s = backup_en or ""
        if "10 mL/kg" in s:
            return "NS enema (10 mL/kg) or reschedule"
        if "saline enema" in s:
            return "NS enema or reschedule"
        return "Enema or reschedule"

    contingency_rows = []
    for b in bands:
        contingency_rows.append(
            f'          <tr>'
            f'<td class="band">{b["label_en"]}</td>'
            f'<td class="num">{b.get("contingency_evening_caps", 0)} cap / '
            f'{b.get("contingency_evening_oz", 0)} oz</td>'
            f'<td class="num">{b.get("contingency_morning_caps", 0)} cap / '
            f'{b.get("contingency_morning_oz", 0)} oz</td>'
            f'<td class="num hi">{b.get("contingency_total_caps", 0)} caps '
            f'({b.get("contingency_total_grams", 0)} g)</td>'
            f'<td class="muted">{_backup_stub(b.get("contingency_backup_en"))}</td>'
            f'</tr>'
        )

    # Per-band short stubs for pre-cleanout (full sentences are on the patient PDFs).
    # Keyed off the band id since the YAML strings are long localized HTML.
    precleanout_stubs = {
        "15-20":   "1 cap / 4 oz clear × <strong>1×/day × 5 d</strong>",
        "21-30":   "1 cap / 4 oz clear × <strong>2×/day × 5 d</strong>",
        "31-40":   "1 cap / 4 oz clear × <strong>4×/day × 3 d</strong> (weekend)",
        "41-50":   "<strong>8 caps in 32 oz Gatorade</strong> over ~4 h (weekend)",
        "over-50": "<strong>10 caps in 40 oz Gatorade</strong> over ~4 h (weekend)",
    }

    precleanout_rows = []
    for b in bands:
        offset = b.get("precleanout_a_offset_days")
        offset_str = "weekend" if offset is None else f"{offset} d"
        maintenance = _strip_for_cell(b.get("precleanout_maintenance_en", ""))
        maintenance = (maintenance
                       .replace("Then continue prescribed daily laxatives, or at least ", "")
                       .replace(", until the procedure.", "")
                       .replace(" until the procedure.", ""))
        text_a = precleanout_stubs.get(b["id"], _strip_for_cell(b.get("precleanout_a_text_en", "")))
        precleanout_rows.append(
            f'          <tr>'
            f'<td class="band">{b["label_en"]}</td>'
            f'<td>{text_a}</td>'
            f'<td class="num">{offset_str}</td>'
            f'<td>{maintenance}</td>'
            f'</tr>'
        )

    # Locations side-by-side
    scc = locations.get("scc", {})
    pmch = locations.get("pmch", {})
    def _loc_row(label, scc_val, pmch_val, cls=""):
        return (f'    <tr><th>{label}</th>'
                f'<td{(" class=" + cls) if cls else ""}>{scc_val}</td>'
                f'<td{(" class=" + cls) if cls else ""}>{pmch_val}</td></tr>')
    locations_block = (
        '<table class="cs-locations">\n'
        '  <thead><tr><th></th>'
        f'<th>Surgery Center of Carmel (SCC)</th>'
        f'<th>St. Vincent — PMCH Peds Preop</th></tr></thead>\n'
        '  <tbody>\n'
        + _loc_row("Address", scc.get("address", ""), pmch.get("address", "")) + "\n"
        + _loc_row("Facility phone", scc.get("phone", ""), pmch.get("phone", ""), '"num"') + "\n"
        + _loc_row("NPO clears", f'{scc.get("clears_npo_hours", 2)} h before procedure',
                                f'{pmch.get("clears_npo_hours", 3)} h before procedure', '"num"') + "\n"
        + _loc_row("Arrival", f'{scc.get("arrival_minutes_before", 60)} min before',
                              f'{pmch.get("arrival_minutes_before", 90)} min before', '"num"') + "\n"
        + _loc_row("Mobile sites",
                   f'{scc.get("mobile_subdomain", "prep")}.giready.com<br>'
                   f'{scc.get("mobile_subdomain_combined", "egdcolon")}.giready.com',
                   f'{pmch.get("mobile_subdomain", "prep86")}.giready.com<br>'
                   f'{pmch.get("mobile_subdomain_combined", "egdcolon86")}.giready.com',
                   '"muted"') + "\n"
        + '  </tbody>\n'
        + '</table>'
    )

    # Post-procedure red flags — kept in sync manually with
    # templates/colonoscopy-mobile.en.html lines ~561-569 (the "After the
    # procedure & when to call" section). If the patient handout's red flags
    # change, mirror them here.
    red_flags_block = (
        '<ul class="cs-list">\n'
        '  <li><strong>Fever &gt;101°F (38.3°C)</strong></li>\n'
        '  <li>Stool that is <strong>black</strong> or has more than a small streak of <strong>blood</strong></li>\n'
        '  <li><strong>Severe or worsening abdominal pain</strong>, or hard / swollen belly</li>\n'
        '  <li><strong>Persistent vomiting</strong>, or refusal to drink</li>\n'
        '  <li class="emergency">Difficulty breathing or chest pain — emergency, call <strong>911</strong></li>\n'
        '</ul>\n'
        '<p class="footnote">Small blood with the first stool can be normal (especially if biopsies taken). Adult must stay 24 h after anesthesia. Most kids back to normal next day.</p>'
    )

    # Medications to hold. The GLP-1 trigger is `include_glp1_warning: true` on
    # the 41-50 and over-50 bands — see _medications_drugs() for the source.
    glp1_bands = [b["label_en"] for b in bands if b.get("include_glp1_warning")]
    glp1_scope = ", ".join(glp1_bands) if glp1_bands else "(none configured)"
    meds_block = (
        '<ul class="cs-list">\n'
        '  <li><strong>Iron supplements</strong> — all bands</li>\n'
        '  <li><strong>Anti-diarrhea medicine</strong> — all bands</li>\n'
        f'  <li><strong>GLP-1 agonists</strong> (Ozempic, Wegovy, Mounjaro) — <span class="hi">{glp1_scope} only</span></li>\n'
        '</ul>\n'
        '<p class="footnote"><strong>Unusual or unlisted meds:</strong> ask the family to prepare a list and call the office.</p>'
    )

    # Infant subtable rows — the ≤15 kg pathway. Two YAML entries feed this:
    # `under-15` (MiraLAX oral, weight-banded) and `under-15-enema` (saline
    # enema). The main dosing table is standard-protocol-only; infants get
    # their own compact 4-row block right below it.
    infant_band = next((b for b in bands_all if b.get("id") == "under-15"), None)
    enema_band = next((b for b in bands_all if b.get("id") == "under-15-enema"), None)
    infant_rows = []
    if infant_band:
        for sb in infant_band.get("cheat_sheet_subbands", []):
            infant_rows.append(
                f'          <tr>'
                f'<td class="band">{sb.get("label_en", "")}</td>'
                f'<td class="num">{sb.get("miralax_capfuls_en", "")}</td>'
                f'<td class="num">{sb.get("miralax_grams", 0)} g</td>'
                f'<td class="muted">{sb.get("mix_oz_en", "")}</td>'
                f'</tr>'
            )
    if enema_band and enema_band.get("cheat_sheet_summary_en"):
        infant_rows.append(
            f'          <tr>'
            f'<td class="band">Saline enema</td>'
            f'<td class="muted" colspan="3">{enema_band["cheat_sheet_summary_en"]}</td>'
            f'</tr>'
        )

    return {
        "{{CHEATSHEET_DOSING_ROWS}}": "\n".join(dosing_rows),
        "{{CHEATSHEET_INFANT_ROWS}}": "\n".join(infant_rows),
        "{{CHEATSHEET_INFANT_SCHEDULE}}": (infant_band or {}).get("cheat_sheet_schedule_en", ""),
        "{{CHEATSHEET_INFANT_MIX_VEHICLE}}": (infant_band or {}).get("cheat_sheet_mix_vehicle_en", ""),
        "{{CHEATSHEET_CONTINGENCY_ROWS}}": "\n".join(contingency_rows),
        "{{CHEATSHEET_PRECLEANOUT_ROWS}}": "\n".join(precleanout_rows),
        "{{CHEATSHEET_LOCATIONS_BLOCK}}": locations_block,
        "{{CHEATSHEET_RED_FLAGS_BLOCK}}": red_flags_block,
        "{{CHEATSHEET_MEDS_BLOCK}}": meds_block,
    }


def render_cheatsheet(out_dir):
    """Render the staff cheat sheet — both HTML (for cheatsheet.giready.com) and
    a 1-page landscape PDF (for printing). Outputs to:
      - out_dir/index.html           (same content, served as the website root)
      - out_dir/bowel-prep-cheatsheet.pdf
    """
    template_path = TEMPLATES / "cheatsheet-print.en.html"
    if not template_path.exists():
        raise RuntimeError(f"cheat-sheet template not found at {template_path}")
    data = load_dosing()
    bands = data["bands"]
    locations = data.get("locations", {})

    replacements = build_cheatsheet_replacements(bands, locations)
    # Add practice placeholders (PRACTICE_PHONE, REVISION_DATE).
    replacements.update(build_practice_placeholders("en"))

    with open(template_path, encoding="utf-8") as f:
        html = f.read()
    for token, value in replacements.items():
        html = html.replace(token, value)

    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders in cheat-sheet: {sorted(set(unreplaced))}")

    out_dir.mkdir(parents=True, exist_ok=True)
    # HTML: written as index.html so a Cloudflare Pages deploy serves the page
    # at cheatsheet.giready.com/ directly. Also write a copy under the named
    # filename so a local --out folder still has a stable artifact.
    html_index = out_dir / "index.html"
    html_index.write_text(html, encoding="utf-8")
    print(f"  wrote {html_index}")

    # PDF via WeasyPrint (uses @page rules in the template for landscape sizing).
    _ensure_weasyprint_libpath()
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "WeasyPrint failed to import. On macOS this usually means Pango/Cairo are "
            "missing — install with `brew install pango`. Original error: " + repr(e)
        )
    pdf_out = out_dir / "bowel-prep-cheatsheet.pdf"
    HTML(string=html, base_url=str(template_path.parent)).write_pdf(str(pdf_out))
    print(f"  wrote {pdf_out}")

    return [html_index, pdf_out]


def render_band(band, lang, fmt, out_dir, flat=False, location=None, location_id="scc", theme="color", variant="standard"):
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
    if protocol == "standard":
        replacements = build_strings(band, lang, location=location)
    elif protocol in ("infant", "infant-enema"):
        replacements = build_infant_strings(band, lang)
    else:
        raise ValueError(f"Unknown protocol: {protocol}")

    # Add location placeholders
    replacements = {**replacements, **build_location_placeholders(location, lang)}

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
        # template.
        if variant == "combined":
            if protocol == "standard":
                template = TEMPLATES / f"combined-print.{lang}.html"
            elif protocol == "infant":
                template = TEMPLATES / f"combined-infant-print.{lang}.html"
            elif protocol == "infant-enema":
                template = TEMPLATES / f"combined-infant-enema-print.{lang}.html"
            else:
                raise ValueError(f"Unknown protocol for combined variant: {protocol!r}")
        else:
            template = TEMPLATES / f"{protocol}-print.{lang}.html"
        theme_suffix = "" if theme == "color" else f"-{theme}"
        variant_suffix = "-combined" if variant == "combined" else ""
        out = target_dir / f"bowel-prep-{stem}-{loc_suffix}{lang_suffix}-print{theme_suffix}{variant_suffix}.pdf"
        render_pdf_print(template, replacements, out,
                         mobile_path=band.get("mobile_path"), lang=lang, location=location, theme=theme,
                         variant=variant)
    else:
        raise ValueError(f"Unknown format: {fmt}")
    return out


def load_dosing():
    with open(DOSING_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


def main():
    ap = argparse.ArgumentParser(description="Render bowel prep handouts from dosing.yaml")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--band", default="all", help="Band id (default: all)")
    ap.add_argument("--lang", default="both", choices=["en", "es", "both"])
    ap.add_argument("--format", default="both", choices=["html", "docx", "both", "pdf-print"])
    ap.add_argument("--location", default="scc", help="Location id (scc or pmch). Default: scc")
    ap.add_argument("--theme", default="color", choices=["color", "print-light"],
                    help="Color theme for pdf-print: 'color' (default) or 'print-light' (toner-friendly).")
    ap.add_argument("--variant", default="standard", choices=["standard", "combined", "cheat-sheet"],
                    help="Document family for pdf-print: 'standard' (colonoscopy-only, default), "
                         "'combined' (EGD + colonoscopy back-to-back), or 'cheat-sheet' (1-page "
                         "landscape staff reference; writes both HTML and PDF, ignores --band, "
                         "--lang, --location, --format).")
    ap.add_argument("--flat", action="store_true",
                    help="Write all files directly into --out instead of nesting "
                         "under Language/Weight-band subfolders")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Cheat-sheet variant short-circuits the per-band pipeline — it's a single
    # location-agnostic document. Warn (but don't error) if band-specific flags
    # were also passed; they're silently ignored.
    if args.variant == "cheat-sheet":
        if args.band != "all" or args.lang != "both" or args.location != "scc" or args.format != "both":
            print("NOTE: --variant cheat-sheet ignores --band, --lang, --location, --format "
                  "(it's a single staff-reference document).", file=sys.stderr)
        written = render_cheatsheet(out_dir)
        print(f"\n{len(written)} file(s) written to {out_dir} (variant=cheat-sheet)")
        return

    dosing_data = load_dosing()
    bands = dosing_data["bands"]
    locations = dosing_data.get("locations", {})
    if args.location not in locations:
        sys.exit(f"ERROR: location {args.location!r} not found in dosing.yaml (available: {list(locations.keys())})")
    location = locations[args.location]

    if args.band != "all":
        bands = [b for b in bands if b["id"] == args.band]
        if not bands:
            sys.exit(f"ERROR: band id {args.band!r} not found in dosing.yaml")

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
                                  variant=args.variant)
                written.append(out)
                print(f"  wrote {out}")

    print(f"\n{len(written)} file(s) written to {out_dir} (location={args.location}, variant={args.variant})")


if __name__ == "__main__":
    main()
