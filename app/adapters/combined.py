"""Combined EGD + colonoscopy adapter.

The combined handout reuses the bowel-prep skill — same weight bands,
same dosing logic — with a different print template and the egdcolon{,86}
mobile subdomain. We just delegate to bowel_prep.render_pdf with
variant="combined".
"""
from __future__ import annotations

from datetime import datetime

from . import bowel_prep


def render_pdf(
    *,
    band_id: str,
    location_id: str,
    lang: str,
    physician_id: str,
    appt_date_human: str,
    appt_time_display: str,
    arrival_time_display: str,
    followup_block_html: str,
    appt_dt: datetime,
) -> bytes:
    return bowel_prep.render_pdf(
        band_id=band_id,
        location_id=location_id,
        lang=lang,
        physician_id=physician_id,
        appt_date_human=appt_date_human,
        appt_time_display=appt_time_display,
        arrival_time_display=arrival_time_display,
        followup_block_html=followup_block_html,
        appt_dt=appt_dt,
        variant="combined",
    )
