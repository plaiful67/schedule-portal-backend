"""Combined EGD + colonoscopy adapter.

The combined handout reuses the bowel-prep skill — same weight bands,
same dosing logic — with a different print template and the egdcolon{,86}
mobile subdomain. We just delegate to bowel_prep.render_pdf with
variant="combined".
"""
from __future__ import annotations

from . import bowel_prep


def render_pdf(
    *,
    band_id: str,
    location_id: str,
    lang: str,
    appt_date_human: str,
    appt_time_display: str,
    arrival_time_display: str,
    stop_meds_block_html: str,
    deep_link_qr_data_uri: str,
) -> bytes:
    return bowel_prep.render_pdf(
        band_id=band_id,
        location_id=location_id,
        lang=lang,
        appt_date_human=appt_date_human,
        appt_time_display=appt_time_display,
        arrival_time_display=arrival_time_display,
        stop_meds_block_html=stop_meds_block_html,
        deep_link_qr_data_uri=deep_link_qr_data_uri,
        variant="combined",
    )
