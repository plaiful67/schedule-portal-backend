"""flex_sig adapter.

Two render paths:

  * ``prep_type in {"miralax", "lactulose"}`` — flex sig as a "short
    colonoscopy": relabels the colonoscopy bowel-prep handout (those prep
    templates are tokenized for the relabel). Unchanged from Increment 1.

  * ``prep_type == "enema"`` — the in-office / at-home saline-enema prep. This
    path loads the FLEX-SIG skill (vendor/flex-sig-handout-generator), selects
    one of the 3 enema weight bands from procedure.yaml, and renders the
    personalized flex-sig ENEMA template (app/templates/flexsig/). The enema
    clinical text comes verbatim from the skill's build_band_placeholders —
    this adapter never rewrites enema dosing.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import bowel_prep
from .. import personalization, physicians
from ._calm import swap_calm
from ._paths import is_live_dev, load_compose_module, shared_dir, skill_dir

_compose = load_compose_module()

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SKILL_ROOT = skill_dir("flex-sig-handout-generator")
SKILL_RENDER = SKILL_ROOT / "scripts" / "render.py"
TEMPLATES_DIR = BACKEND_DIR / "app" / "templates" / "flexsig"
ENEMA_TEMPLATE_BY_LANG = {
    "en": TEMPLATES_DIR / "print-personalized.en.html",
    "es": TEMPLATES_DIR / "print-personalized.es.html",
}
# Hidden/live mobile flex-sig sites, keyed by location. Same scheme the skill's
# procedure.yaml `mobile_subdomain` uses; resolved per-request from the location
# block so the cover/feedback QRs deep-link to the right subdomain.

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

# EGD + flexible sigmoidoscopy COMBINED relabels. These swap the colonoscopy
# framing in the combined EGD+colonoscopy bowel-prep handout for the approved
# flex-sig framing (same bowel prep, same weight bands). Verbatim approved
# wording — the lower-scope <li> and the heading/sequencing word.
_EGD_FLEXSIG_HEADING = {
    "en": "EGD and Flexible Sigmoidoscopy",
    "es": "EGD y Sigmoidoscopia Flexible",
}
_EGD_FLEXSIG_LOWER_DESC = {
    "en": ('<li><strong>Flexible sigmoidoscopy</strong> &mdash; the same kind of camera is passed '
           'through the bottom to look at the last part of the colon and the rectum. '
           'Small biopsies are usually taken.</li>'),
    "es": ('<li><strong>Sigmoidoscopia flexible</strong> &mdash; el mismo tipo de cámara se pasa por el '
           'recto para examinar la última parte del colon y el recto. '
           'Por lo general se toman pequeñas biopsias.</li>'),
}
_EGD_FLEXSIG_LOWER_WORD = {
    "en": "flexible sigmoidoscopy",
    "es": "sigmoidoscopia flexible",
}

# Valid enema weight bands (mirror schemas.FlexSigBand / procedure.yaml ids).
ENEMA_BANDS: set[str] = {"under-15kg", "20-40kg", "over-40kg"}


def _flexsig_title(lang: str) -> str:
    reg = _compose.load_registry()
    b = reg["bases"]["flexsig"]
    return b.get(f"title_fragment_{lang}", b.get("title_fragment_en", "Flexible Sigmoidoscopy"))


# ---------------------------------------------------------------------------
# Flex-sig skill module — loaded once, paths re-pointed at the chosen source
# (live ~/.claude/skills or vendor/), mirroring bowel_prep / egd adapters.
# ---------------------------------------------------------------------------
def _load_skill_module():
    """Load the flex-sig skill's render.py under a unique module name so it
    doesn't collide with the bowel-prep / EGD skills' `render` modules already
    cached in sys.modules."""
    name = "_flex_sig_render"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SKILL_RENDER)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load flex-sig render module from {SKILL_RENDER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


skill = _load_skill_module()

# Re-point the skill's module-level paths so practice.yaml + procedure.yaml +
# shared partials resolve to the chosen skill source.
skill.SKILL_DIR = SKILL_ROOT
skill.TEMPLATES = SKILL_ROOT / "templates"
skill.PROCEDURE_PATH = SKILL_ROOT / "data" / "procedure.yaml"
skill.PRACTICE_PATH = SKILL_ROOT / "practice.yaml"
# Cross-skill shared partials (feedback bar, NPO table) resolve from vendor/shared
# on Cloud Run (or ~/peds-gi-prep-system/shared in live dev).
skill._SHARED_PARTIALS_DIR = shared_dir() / "partials"
skill._PRACTICE_CACHE = None
skill._PROCEDURE_CACHE = None
skill._SHARED_PARTIALS_CACHE = {}

# Static-handout sites at *.giready.com use the GI Ready logo; scheduler-
# generated personalized PDFs keep the PMCH logo per the 2026-05-22 brand split.
# Override in code (not vendor/*.yaml) because `make vendor-sync` re-copies the
# static skill on every deploy and would overwrite a YAML edit.
if not getattr(skill._practice, "_pmch_override_applied", False):
    _original_practice = skill._practice

    def _practice_with_pmch_override():
        data = _original_practice()
        data["practice"]["logo_filename"] = "logo-pmch.png"
        return data

    _practice_with_pmch_override._pmch_override_applied = True  # type: ignore[attr-defined]
    skill._practice = _practice_with_pmch_override


def _reset_caches_for_live_dev():
    """Reset the skill's practice/procedure/shared-partials caches at request
    time so live edits land without a uvicorn restart. No-op in production
    (immutable vendored source) so the caches stay warm."""
    if not is_live_dev("flex-sig-handout-generator"):
        return
    skill._PRACTICE_CACHE = None
    skill._PROCEDURE_CACHE = None
    skill._SHARED_PARTIALS_CACHE = {}


def _procedure_and_location(location_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    data = skill._procedure_data()
    procedure = data["procedures"]["flex-sig"]
    location = data["locations"].get(location_id)
    if not location:
        raise ValueError(f"Unknown location_id={location_id!r}")
    return procedure, location


def _band_for_id(procedure: dict[str, Any], band_id: str) -> dict[str, Any]:
    for b in procedure.get("bands", []):
        if b["id"] == band_id:
            return b
    raise ValueError(f"No flex-sig band found for id={band_id!r}")


def _render_enema(
    *,
    weight_band: str,
    location_id: str,
    lang: str,
    physician_id: str,
    appt_date_human: str,
    appt_time_display: str,
    arrival_time_display: str,
    followup_block_html: str,
    appt_dt: datetime,
    include_directions: bool,
) -> bytes:
    """Render the personalized flex-sig ENEMA handout. Mirrors the bowel_prep /
    egd render bodies: Calm swap, shared-partial expansion, QR injection, pz
    clock substitutions, unreplaced-token guard, tagged-PDF write."""
    from weasyprint import HTML  # imported here so failures are 500s, not import-time crashes

    _reset_caches_for_live_dev()

    if weight_band not in ENEMA_BANDS:
        raise ValueError(
            f"prep_type=enema not supported for weight_band={weight_band!r} "
            f"(allowed: {sorted(ENEMA_BANDS)})"
        )

    procedure, location = _procedure_and_location(location_id)
    band = _band_for_id(procedure, weight_band)

    template_path = ENEMA_TEMPLATE_BY_LANG.get(lang)
    if template_path is None:
        raise ValueError(f"No flex-sig enema template for lang={lang!r}")

    # Clinical strings come verbatim from the skill's builders (enema text,
    # shopping list, infant warning, NPO, drink-cup, band label).
    replacements = {
        **skill.build_practice_placeholders(lang),
        **skill.build_location_placeholders(location, lang),
        **skill.build_band_placeholders(procedure, band, lang, location=location),
    }

    # Performing-physician personalization: same model as bowel_prep / egd.
    physician = physicians.lookup(physician_id)
    replacements["{{PRACTICE_FOOTER}}"] = physicians.footer_line(physician_id, lang)
    replacements["{{PERFORMING_PHYSICIAN}}"] = physician["name_short"]

    # Procedure-level relabel tokens (heading / about / "the {procedure}").
    title = _flexsig_title(lang)
    replacements["{{PROCEDURE_HEADING}}"] = title
    replacements["{{PROCEDURE_ABOUT}}"] = _FLEXSIG_ABOUT.get(lang, _FLEXSIG_ABOUT_EN)
    replacements["{{PROCEDURE_WORD}}"] = _FLEXSIG_WORD.get(lang, "procedure")

    # MOBILE / FEEDBACK URLs deep-link to the flex-sig mobile site with the
    # appointment baked into the #d=&t= hash so the page personalizes itself,
    # plus ?feedback=1&source=print so survey.js auto-opens and tags the row.
    sub = location.get("mobile_subdomain", "flexsig") or "flexsig"
    lang_seg = "es/" if lang == "es" else ""
    hash_params = f"#d={appt_dt.date().isoformat()}&t={appt_dt.strftime('%H%M')}"
    mobile_url = f"https://{sub}.giready.com/{lang_seg}{hash_params}"
    feedback_url = f"https://{sub}.giready.com/{lang_seg}?feedback=1&source=print{hash_params}"
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""
    youtube_url = skill._qr_target("youtube_url_es" if lang == "es" else "youtube_url_en")
    portal_url = skill._qr_target("portal_url")
    gikids_url = skill._qr_target("gikids_url")
    location_phone_tel = re.sub(r"\D", "", location.get("phone", ""))

    qr_replacements = {
        "{{MOBILE_URL}}":          feedback_url,
        "{{FEEDBACK_URL}}":        feedback_url,
        "{{MAPS_URL}}":            maps_url,
        "{{YOUTUBE_URL}}":         youtube_url,
        "{{PORTAL_URL}}":          portal_url,
        "{{GIKIDS_URL}}":          gikids_url,
        "{{LOCATION_PHONE_TEL}}":  location_phone_tel,
    }

    personalization_replacements = {
        "{{APPT_DATE_HUMAN}}":     appt_date_human,
        "{{APPT_TIME}}":           appt_time_display,
        "{{ARRIVAL_TIME}}":        arrival_time_display,
        "{{FOLLOWUP_BLOCK_HTML}}": followup_block_html,
    }

    html = template_path.read_text(encoding="utf-8")

    # Per-band conditional blocks (SIMPLE_DIET / FULL_DIET / INFANT_CALLOUT /
    # INCLUDE_GLP1) BEFORE token substitution so we don't leave orphan tokens
    # and the unreplaced-token guard stays honest.
    # NOTE: the skill's apply_conditional_blocks regex captures flag names with
    # [A-Z_]+ — NO digits — so the conditional marker must be "INCLUDE_GLP", not
    # "INCLUDE_GLP1" (a "1" would break the match and leak the block). The band
    # field in procedure.yaml is still `include_glp1_warning`.
    flags = {
        "SIMPLE_DIET":    bool(band.get("simple_diet")),
        "FULL_DIET":      not bool(band.get("simple_diet")),
        "INFANT_CALLOUT": bool(band.get("infant_callout")),
        "INCLUDE_GLP":    bool(band.get("include_glp1_warning")),
    }
    html = skill.apply_conditional_blocks(html, flags)

    # Calm theme: swap the navy <style> for the shared Calm stylesheet (+ EGD
    # NPO-table rules, which flex-sig shares) before any token substitution.
    html = swap_calm(html, include_egd=True)

    # Expand shared partials first (feedback bar / NPO table); inner tokens like
    # {{FEEDBACK_URL}} / {{NPO_*}} resolve in the main pass below.
    for token, value in skill._load_shared_partials(lang).items():
        html = html.replace(token, str(value))

    all_replacements = {**replacements, **qr_replacements, **personalization_replacements}
    for token, value in all_replacements.items():
        html = html.replace(token, str(value))

    # Swap the QR <img id> srcs to data URIs. qr-mobile (cover) + qr-feedback
    # (mid-doc) both encode the survey-enabled URL so either scan path opens the
    # modal tagged source=print.
    qr_uris = {
        "qr-mobile":   skill._png_to_data_uri(skill._generate_qr(feedback_url)),
        "qr-feedback": skill._png_to_data_uri(skill._generate_qr(feedback_url)),
        "qr-maps":     skill._png_to_data_uri(skill._generate_qr(maps_url)) if maps_url else "",
        "qr-youtube":  skill._png_to_data_uri(skill._generate_qr(youtube_url)) if youtube_url else "",
        "qr-portal":   skill._png_to_data_uri(skill._generate_qr(portal_url)) if portal_url else "",
        "qr-gikids":   skill._png_to_data_uri(skill._generate_qr(gikids_url)) if gikids_url else "",
    }
    html = skill._inject_qr_into_imgs(html, qr_uris)

    # Procedure-time-driven clock-time substitutions (mirrors mobile pz-only JS).
    # The flex-sig enema template carries no pz-only spans today, but run it for
    # parity so any future timing spans personalize uniformly.
    html = personalization.apply_pz_substitutions(html, appt_dt, lang)

    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders: {sorted(set(unreplaced))}")

    # Splice shared print-base.css in front of the template's own <style> block
    # so design-token + feedback-cell changes propagate without editing the
    # template. Template-local CSS still overrides.
    html = skill._inject_shared_print_css(html)

    base_url = (SKILL_ROOT / "templates").as_uri() + "/"
    if include_directions:
        from ..directions_inline import inject_into_handout
        html = inject_into_handout(html, location_id, lang)
    from ..pdf_tagging import write_pdf_tagged
    return write_pdf_tagged(HTML(string=html, base_url=base_url))


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
    include_egd: bool = False,
) -> bytes:
    # EGD + flexible sigmoidoscopy combined: reuse the EGD+colonoscopy COMBINED
    # bowel-prep handout (same miralax/lactulose prep, same 6 weight bands),
    # relabeled so the lower scope reads as a flex sig. enema+EGD is out of scope
    # this increment (the schema rejects it), so this path is miralax/lactulose only.
    if include_egd:
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
            variant="combined",
            prep_type=prep_type,
            include_directions=include_directions,
            procedure_heading=_EGD_FLEXSIG_HEADING.get(lang, _EGD_FLEXSIG_HEADING["en"]),
            combined_lower_desc_html=_EGD_FLEXSIG_LOWER_DESC.get(lang, _EGD_FLEXSIG_LOWER_DESC["en"]),
            combined_lower_word=_EGD_FLEXSIG_LOWER_WORD.get(lang, _EGD_FLEXSIG_LOWER_WORD["en"]),
        )

    if prep_type == "enema":
        return _render_enema(
            weight_band=weight_band,
            location_id=location_id,
            lang=lang,
            physician_id=physician_id,
            appt_date_human=appt_date_human,
            appt_time_display=appt_time_display,
            arrival_time_display=arrival_time_display,
            followup_block_html=followup_block_html,
            appt_dt=appt_dt,
            include_directions=include_directions,
        )

    # miralax / lactulose: relabel the colonoscopy bowel-prep handout.
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
