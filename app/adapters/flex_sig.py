"""flex_sig adapter — Increment 1: 'short colonoscopy'. Renders the colonoscopy
bowel-prep handout relabeled 'Flexible Sigmoidoscopy' (miralax/lactulose). Enema
prep + EGD+flexsig are later increments."""
from __future__ import annotations

from datetime import datetime

from . import bowel_prep
from ._paths import load_compose_module

_compose = load_compose_module()

# Approved flex-sig "About the Procedure" paragraphs (verbatim; EN reviewed,
# ES is a faithful translation — see ES-REVIEW comment below).
_FLEXSIG_ABOUT_EN = (
    '<p>A <strong>flexible sigmoidoscopy</strong> is a short procedure done under anesthesia.'
    ' A pediatric gastroenterologist passes a thin, flexible camera through the bottom'
    ' to look at the <strong>last part of the colon and the rectum</strong>.'
    ' Small biopsies are usually taken.'
    ' The procedure itself takes about <strong>15&#8211;30 minutes</strong>,'
    ' but plan to be at the facility for several hours total.'
    ' The lower colon must be clean for the camera to see well &#8212;'
    " that's what the medicines below are for.</p>"
)
# ES-REVIEW: faithful translation of approved EN
_FLEXSIG_ABOUT_ES = (
    '<p>Una <strong>sigmoidoscopia flexible</strong> es un procedimiento corto que se'
    ' realiza bajo anestesia. Un gastroenter&#243;logo pedi&#225;trico pasa una c&#225;mara'
    ' delgada y flexible por el recto para observar la'
    ' <strong>&#250;ltima parte del colon y el recto</strong>.'
    ' Por lo general se toman peque&#241;as biopsias.'
    ' El procedimiento dura entre <strong>15 y 30 minutos</strong>,'
    ' pero planee estar en el centro varias horas en total.'
    ' La parte baja del colon debe estar limpia para que la c&#225;mara vea bien;'
    ' para eso son los medicamentos que se indican a continuaci&#243;n.</p>'
)

_FLEXSIG_ABOUT = {"en": _FLEXSIG_ABOUT_EN, "es": _FLEXSIG_ABOUT_ES}
_FLEXSIG_WORD = {"en": "procedure", "es": "procedimiento"}


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
        procedure_heading=title,
        procedure_about_html=_FLEXSIG_ABOUT.get(lang, _FLEXSIG_ABOUT_EN),
        procedure_word=_FLEXSIG_WORD.get(lang, "procedure"),
    )
