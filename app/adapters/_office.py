"""Office (canonical) render transform.

The scheduler's *personalized* print templates carry per-patient chrome — a
`.performing-physician` credit line and an `.appt-callout` date/arrival/time box
— that the render adapters fill from the appointment request. The **office**
(canonical) variant reuses those exact templates (so the Calm theme, the
`calm-personalized.css` partner stylesheet, and the ADA/PDF-UA tagging all match
the custom look), then strips that patient chrome to produce a *generic* handout
suitable for the practice Google Drive:

  * no procedure date  -> the whole `.appt-callout` section is removed
  * all doctors, not one -> the `.performing-physician` div is replaced with the
    full group roster credit line
  * no follow-up appointment -> the caller passes an empty `{{FOLLOWUP_BLOCK_HTML}}`

The office render path also skips `apply_pz_substitutions`, so the empty
additive `pz-only` date spans (`data-pz-template=" ({date})"`) contribute nothing
and the surrounding diet/med copy reads as correct dateless wording.

Both edits here are keyed on the *container* markup — `<section
class="appt-callout">…</section>` and `<div class="performing-physician">…</div>`
— which is uniform across every personalized family (bowel_prep std/combined/
infant/enema/lactulose/clenpiq/suprep, egd, composed, egd_phmii, flexsig), even
though the inner layout of the appt-callout differs (std uses `.appt-value`
rows, combined uses a single `.appt-line`). Each edit asserts exactly one match
and raises on drift so a future template change can't silently desync the office
output.
"""
from __future__ import annotations

import re

from .. import physicians

# `<section class="appt-callout"> ... </section>` — the callout never nests a
# <section>, so a non-greedy match to the first closing tag is safe. Trailing
# whitespace/newlines are swallowed so removal leaves no gap.
_APPT_CALLOUT_RE = re.compile(
    r'<section class="appt-callout">.*?</section>\s*',
    re.DOTALL,
)

# `<div class="performing-physician"> ... </div>` — the div has no nested
# <div>, so a non-greedy match to the first closing tag is exact.
_PERFORMING_PHYSICIAN_RE = re.compile(
    r'<div class="performing-physician">.*?</div>',
    re.DOTALL,
)

_ROSTER_LABEL = {
    "en": "Our pediatric gastroenterologists:",
    "es": "Nuestros gastroenterólogos pediátricos:",
}


def _roster_names() -> list[str]:
    """Ordered `name_short` roster from the shared practice.yaml (via physicians)."""
    # physicians._BY_ID preserves practice.yaml insertion order.
    return [p["name_short"] for p in physicians._BY_ID.values()]


def all_doctors_block_html(lang: str) -> str:
    """The all-doctors credit line that replaces the single-physician div.

    Reuses the `.performing-physician` class so it drops into the same header
    slot with the existing (Calm) styling — just multi-name instead of one.
    """
    label = _ROSTER_LABEL.get(lang, _ROSTER_LABEL["en"])
    # Non-breaking space INSIDE each name so it never wraps after "Dr." (which
    # would orphan "Dr." at a line end). Breaks are allowed only at the " · "
    # separators between doctors.
    names = " · ".join(n.replace(" ", "\u00A0") for n in _roster_names())
    return f'    <div class="performing-physician">{label}<br>{names}</div>'


def _sub_exactly_once(pattern: re.Pattern[str], repl: str, text: str, *, what: str) -> str:
    """Substitute `pattern` -> `repl`, asserting exactly one match. Fail loud."""
    new_text, n = pattern.subn(repl, text, count=1)
    if n != 1:
        total = len(pattern.findall(text))
        raise RuntimeError(
            f"office transform: expected exactly one {what} to {('remove' if not repl else 'replace')}, "
            f"found {total}. The personalized template markup has drifted; "
            f"update app/adapters/_office.py."
        )
    return new_text


def to_office(html: str, *, lang: str, doctors_block_html: str | None = None) -> str:
    """Turn a rendered *personalized* handout HTML into the generic office variant.

    Call this AFTER `swap_calm` and BEFORE token substitution (so the removed
    blocks take their `{{APPT_*}}` / `{{PERFORMING_PHYSICIAN}}` tokens with them
    and never trip the unreplaced-placeholder guard).
    """
    if doctors_block_html is None:
        doctors_block_html = all_doctors_block_html(lang)
    # Escape backslashes for re.sub replacement string safety (names have none
    # today, but keep the substitution literal-safe).
    doctors_repl = doctors_block_html.replace("\\", "\\\\")
    html = _sub_exactly_once(
        _PERFORMING_PHYSICIAN_RE, doctors_repl, html, what="performing-physician div"
    )
    html = _sub_exactly_once(_APPT_CALLOUT_RE, "", html, what="appt-callout section")
    return html
