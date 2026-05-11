"""Helpers that build the personalized HTML fragments substituted into the
print templates: human-readable date, formatted times, and the STOP_MEDS_BLOCK.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from .medications import by_id, categories as load_categories

EN_MONTHS = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]
ES_MONTHS = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
EN_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ES_WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def format_appt_date(d: date, lang: str) -> str:
    if lang == "es":
        return f"{ES_WEEKDAYS[d.weekday()]}, {d.day} de {ES_MONTHS[d.month]} de {d.year}"
    return f"{EN_WEEKDAYS[d.weekday()]}, {EN_MONTHS[d.month]} {d.day}, {d.year}"


def format_time_12h(hhmm: str) -> str:
    """'07:30' → '7:30 AM'."""
    dt = datetime.strptime(hhmm, "%H:%M")
    return dt.strftime("%-I:%M %p")


def build_stop_meds_block(med_ids: Iterable[str], lang: str) -> str:
    """Render the {{STOP_MEDS_BLOCK}} HTML — a styled callout listing each
    selected med with its hold instruction, sorted by hold_days descending.

    Med names and hold instructions are English regardless of `lang`; only
    the section title localizes (see medications.yaml header for rationale).
    """
    selected = [by_id()[m] for m in med_ids if m in by_id()]
    if not selected:
        return ""

    selected.sort(key=lambda m: m["hold_days"], reverse=True)
    cats = load_categories()

    title = "STOP these medications before the procedure" if lang == "en" \
        else "SUSPENDA estos medicamentos antes del procedimiento"

    rows: list[str] = []
    for m in selected:
        name = m["primary_name"]
        hold = m["hold_text"]
        cat = cats.get(m["category"], {}).get("label", "")
        cat_label = f" <span style='color:#888'>· {cat}</span>" if cat else ""
        note = m.get("note")
        note_html = f"<span class='med-note'>{note}</span>" if note else ""
        rows.append(
            f"<li><span class='med-name'>{name}</span>{cat_label} — "
            f"<span class='med-hold'>{hold}</span>{note_html}</li>"
        )

    return (
        '<section class="stop-meds">'
        f'<h3>{title}</h3>'
        f'<ul>{"".join(rows)}</ul>'
        '</section>'
    )
