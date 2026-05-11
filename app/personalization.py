"""Helpers that build the personalized HTML fragments substituted into the
print templates: human-readable date, formatted times, the meds-reference
callout, and procedure-time-driven substitutions for pz-only spans.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import date, datetime, timedelta
from typing import Iterable

from .medications import by_id

EN_MONTHS = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]
ES_MONTHS = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
EN_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ES_WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
EN_WEEKDAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
ES_WEEKDAYS_SHORT = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
EN_MONTHS_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
ES_MONTHS_SHORT = ["", "ene", "feb", "mar", "abr", "may", "jun",
                   "jul", "ago", "sep", "oct", "nov", "dic"]


def format_appt_date(d: date, lang: str) -> str:
    if lang == "es":
        return f"{ES_WEEKDAYS[d.weekday()]}, {d.day} de {ES_MONTHS[d.month]} de {d.year}"
    return f"{EN_WEEKDAYS[d.weekday()]}, {EN_MONTHS[d.month]} {d.day}, {d.year}"


def format_appt_date_short(d: date, lang: str) -> str:
    """Short variant for inline use ('Wed, Mar 18'). Used by pz-day substitutions."""
    if lang == "es":
        return f"{ES_WEEKDAYS_SHORT[d.weekday()]}, {d.day} {ES_MONTHS_SHORT[d.month]}"
    return f"{EN_WEEKDAYS_SHORT[d.weekday()]}, {EN_MONTHS_SHORT[d.month]} {d.day}"


def format_time_12h(hhmm: str) -> str:
    """'07:30' → '7:30 AM'."""
    dt = datetime.strptime(hhmm, "%H:%M")
    return dt.strftime("%-I:%M %p")


def _brand_aliases(med: dict) -> list[str]:
    """Pick up to 2 brand-name aliases from a med's search_aliases list.

    Convention in medications.yaml: search_aliases[0] is the lowercase generic
    that matches primary_name. Brand names follow, capitalized. Take the first
    couple that aren't just the generic re-cased.
    """
    primary_lower = med["primary_name"].lower()
    out: list[str] = []
    for a in med.get("search_aliases", [])[1:]:
        if a.lower() == primary_lower:
            continue
        out.append(a)
        if len(out) >= 2:
            break
    return out


def build_meds_reference_callout(med_ids: Iterable[str], lang: str, qr_data_uri: str) -> str:
    """Render the personalized medication callout.

    Contains the scheduler-selected meds-to-stop list (sorted longest-hold
    first, with brand-name parens for recognition) plus a compact footer
    pointing at meds.giready.com so families can self-verify anything that
    wasn't on the list. Single box — no second callout.
    """
    selected = [by_id()[m] for m in med_ids if m in by_id()]
    selected.sort(key=lambda m: m.get("hold_days", 0), reverse=True)

    if lang == "es":
        title = "SUSPENDA estos medicamentos antes del procedimiento"
        footer_text = ("Para otros medicamentos, verifique en "
                       "<strong>meds.giready.com</strong>")
        alt = "Código QR de meds.giready.com"
        empty_title = "Medicamentos"
        empty_body = ("No se seleccionaron medicamentos para suspender. "
                      "Verifique en <strong>meds.giready.com</strong> "
                      "o escanee este código.")
    else:
        title = "STOP these medications before the procedure"
        footer_text = ("For other medications, verify at "
                       "<strong>meds.giready.com</strong>")
        alt = "meds.giready.com QR"
        empty_title = "Medications"
        empty_body = ("No medications selected to stop. Verify yours at "
                      "<strong>meds.giready.com</strong> or scan this code.")

    if not selected:
        return (
            '<section class="meds-reference">'
            '<div class="meds-reference-row">'
            f'<div><h3>{empty_title}</h3><p>{empty_body}</p></div>'
            f'<img class="meds-qr" src="{qr_data_uri}" alt="{alt}">'
            '</div>'
            '</section>'
        )

    rows: list[str] = []
    for m in selected:
        name = html_lib.escape(m["primary_name"])
        brands = _brand_aliases(m)
        brand_str = f" ({', '.join(html_lib.escape(b) for b in brands)})" if brands else ""
        hold = html_lib.escape(m["hold_text"])
        note = m.get("note")
        note_html = f'<span class="med-note">{html_lib.escape(note)}</span>' if note else ""
        rows.append(
            f'<li><strong>{name}</strong>{brand_str} &mdash; {hold}{note_html}</li>'
        )

    return (
        '<section class="meds-reference">'
        f'<h3>{title}</h3>'
        f'<ul>{"".join(rows)}</ul>'
        '<div class="meds-footer">'
        f'<span>{footer_text}</span>'
        f'<img class="meds-qr" src="{qr_data_uri}" alt="{alt}">'
        '</div>'
        '</section>'
    )


def build_followup_block(d: date | None, hhmm: str | None, lang: str) -> str:
    """Render the follow-up appointment as a narrow standalone callout that
    sits between the location box and the medications block. When `d` and
    `hhmm` are both set, prints the concrete date/time. Otherwise prints a
    "call the office" fallback (lighter typography via the .fallback class).
    """
    label = "Seguimiento" if lang == "es" else "Follow-up"
    if d and hhmm:
        date_str = format_appt_date_short(d, lang)
        time_str = format_time_12h(hhmm)
        value = f"{date_str} a las {time_str}" if lang == "es" else f"{date_str} at {time_str}"
        return (
            '<section class="followup-callout">'
            f'<span class="followup-label">{label}</span>'
            f'<span class="followup-value">{value}</span>'
            '</section>'
        )
    if lang == "es":
        msg = "Llame a la oficina para programar el seguimiento."
    else:
        msg = "Call the office to schedule a follow-up appointment."
    return (
        '<section class="followup-callout fallback">'
        f'<span class="followup-label">{label}</span>'
        f'<span class="followup-value">{msg}</span>'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Procedure-time-driven substitutions (mirrors the mobile-page pz-only JS)
# ---------------------------------------------------------------------------

# Match ANY element whose attribute list contains one of the pz markers.
# Mirrors the mobile JS's `querySelectorAll('[data-pz-day], [data-pz-time-mins],
# [data-pz-cutoff-hours]')` — we don't filter by tag name or class.
_PZ_TAG_RE = re.compile(
    r'<(?P<tag>[a-zA-Z][a-zA-Z0-9]*)\s+'
    r'(?P<attrs>[^>]*?\bdata-pz-(?:day|time-mins|cutoff-hours)\b[^>]*)>'
    r'(?P<inner>.*?)'
    r'</(?P=tag)\s*>',
    re.DOTALL,
)

_ICON_RE = re.compile(r'^\s*(<span class="icon">[^<]*</span>)', re.DOTALL)


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'\b{name}="([^"]*)"', attrs)
    return m.group(1) if m else None


def apply_pz_substitutions(html: str, appt_dt: datetime, lang: str) -> str:
    """Walk every element with a data-pz-* marker and replace its inner HTML
    with the back-calculated clock time / date string.

    Mirrors the mobile-page client-side JS at
    `~/.claude/skills/bowel-prep-generator/templates/partials/_personalize.{en,es}.html`
    so the print PDF gets the same personalization the mobile page does.

    Marker shapes recognized (any tag, any class):
      - data-pz-time-mins="N" + data-pz-template="…{time}…"
            → substitute {time} = format(appt_dt + N minutes)
      - data-pz-day="N" WITH data-pz-template="…{date}…"
            → substitute {date} = format(appt_date + N days)
      - data-pz-day="N" WITHOUT data-pz-template
            → rewrite inner to: icon + prefix + date + suffix (preserves leading
              <span class="icon">…</span>; reads data-pz-prefix / data-pz-suffix)
      - data-pz-cutoff-hours="H"
            → emit the localized "Stop all clear liquids by {time} ({date})." sentence
      - data-pz-weekday="N" + data-pz-template="…{weekday}…"
            → substitute {weekday} = weekday name
    """
    def replace(m: re.Match) -> str:
        tag = m.group("tag")
        attrs = m.group("attrs")
        inner = m.group("inner")
        tmpl = _attr(attrs, "data-pz-template")

        time_mins = _attr(attrs, "data-pz-time-mins")
        day_off = _attr(attrs, "data-pz-day")
        weekday_off = _attr(attrs, "data-pz-weekday")
        cutoff_hours = _attr(attrs, "data-pz-cutoff-hours")

        # data-pz-cutoff-hours: emit the hardcoded localized cutoff sentence,
        # mirroring the mobile JS's line at _personalize.en.html:227.
        if cutoff_hours is not None:
            cutoff_dt = appt_dt - timedelta(hours=float(cutoff_hours))
            time_str = cutoff_dt.strftime("%-I:%M %p")
            date_str = format_appt_date_short(cutoff_dt.date(), lang)
            if lang == "es":
                new_inner = (f"<strong>Deje de tomar líquidos claros antes de "
                             f"las {time_str} ({date_str}).</strong>")
            else:
                new_inner = (f"<strong>Stop all clear liquids by {time_str} "
                             f"({date_str}).</strong>")
            return f"<{tag} {attrs}>{new_inner}</{tag}>"

        # data-pz-day WITHOUT template: preserve leading icon, prepend prefix
        # + date, append suffix. Mirrors mobile JS at _personalize.en.html:217-222.
        if day_off is not None and tmpl is None:
            d = appt_dt.date() + timedelta(days=int(day_off))
            prefix = _attr(attrs, "data-pz-prefix") or ""
            suffix = _attr(attrs, "data-pz-suffix") or ""
            icon_match = _ICON_RE.match(inner)
            icon = (icon_match.group(1) + " ") if icon_match else ""
            new_inner = f"{icon}{prefix}{format_appt_date_short(d, lang)}{suffix}"
            return f"<{tag} {attrs}>{new_inner}</{tag}>"

        # Template-driven substitution (time + date + weekday placeholders).
        result = tmpl if tmpl is not None else inner
        if time_mins is not None:
            dt = appt_dt + timedelta(minutes=int(time_mins))
            result = result.replace("{time}", dt.strftime("%-I:%M %p"))
        if day_off is not None:
            d = appt_dt.date() + timedelta(days=int(day_off))
            result = result.replace("{date}", format_appt_date_short(d, lang))
        if weekday_off is not None:
            d = appt_dt.date() + timedelta(days=int(weekday_off))
            wk = (ES_WEEKDAYS if lang == "es" else EN_WEEKDAYS)[d.weekday()]
            result = result.replace("{weekday}", wk)

        # If the markup originally had a data-pz-template attr, swap the WHOLE
        # tag's inner HTML with the substituted result (keeps the tag wrapper).
        # If no template, the inner content was used as the template fallback —
        # again swap inner with result.
        return f"<{tag} {attrs}>{result}</{tag}>"

    return _PZ_TAG_RE.sub(replace, html)
