"""flex_sig adapter — Increment 1: 'short colonoscopy'. Renders the colonoscopy
bowel-prep handout relabeled 'Flexible Sigmoidoscopy' (miralax/lactulose). Enema
prep + EGD+flexsig are later increments."""
from __future__ import annotations

from datetime import datetime

from . import bowel_prep
from ._paths import load_compose_module

_compose = load_compose_module()


def _flexsig_title(lang: str) -> str:
    reg = _compose.load_registry()
    b = reg["bases"]["flexsig"]
    return b.get(f"title_fragment_{lang}", b.get("title_fragment_en", "Flexible Sigmoidoscopy"))


def render_pdf(
    *,
    weight_band: str,
    prep_type: str,
    location_id: str,
    lang: str,
    physician_id: str,
    appt_date_human: str,
    appt_time_display: str,
    arrival_time_display: str,
    followup_block_html: str,
    appt_dt: datetime,
    include_directions: bool = True,
) -> bytes:
    title = _flexsig_title(lang)
    return bowel_prep.render_pdf(
        band_id=weight_band,
        location_id=location_id,
        lang=lang,
        physician_id=physician_id,
        appt_date_human=appt_date_human,
        appt_time_display=appt_time_display,
        arrival_time_display=arrival_time_display,
        followup_block_html=followup_block_html,
        appt_dt=appt_dt,
        variant="standard",
        prep_type=prep_type,
        include_directions=include_directions,
        composed_title=title,
        composed_procedure_label=title,
    )
