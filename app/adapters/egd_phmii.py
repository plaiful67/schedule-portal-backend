"""EGD + pH impedance monitoring (pH-MII) adapter — produces a personalized
PDF by reusing the vendored egd-handout-generator skill's substitution
functions with procedure_id="egdph", and overlaying patient-specific
appointment/arrival/follow-up data.

PMCH only (motility nurses staff only St. Vincent 86th St). The schema layer
already constrains location_id to "pmch"; the adapter assumes that constraint
and does not re-validate.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .. import personalization, physicians
from ._calm import swap_calm
from ._paths import skill_dir

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SKILL_ROOT = skill_dir("egd-handout-generator")
SKILL_RENDER = SKILL_ROOT / "scripts" / "render.py"
TEMPLATES_DIR = BACKEND_DIR / "app" / "templates" / "egd_phmii"
TEMPLATE_BY_LANG = {
    "en": TEMPLATES_DIR / "print-personalized.en.html",
    "es": TEMPLATES_DIR / "print-personalized.es.html",
}

PROCEDURE_ID = "egdph"


def _load_skill_module():
    """Share the EGD skill module with the egd adapter — both load the same
    render.py and depend on the same skill module-level state.
    """
    name = "_egd_handout_render"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SKILL_RENDER)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load EGD render module from {SKILL_RENDER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


skill = _load_skill_module()

# Re-point skill paths (idempotent if egd adapter already did this).
skill.SKILL_DIR = SKILL_ROOT
skill.TEMPLATES = SKILL_ROOT / "templates"
skill.PROCEDURE_PATH = SKILL_ROOT / "data" / "procedure.yaml"
skill.PRACTICE_PATH = SKILL_ROOT / "practice.yaml"


def _reset_caches_for_live_dev():
    skill._PRACTICE_CACHE = None
    skill._PROCEDURE_CACHE = None


def _load_procedure_data() -> dict[str, Any]:
    with open(skill.PROCEDURE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _location_block(location_id: str) -> dict[str, Any]:
    data = _load_procedure_data()
    loc = data["locations"].get(location_id)
    if not loc:
        raise ValueError(f"Unknown location_id={location_id!r}")
    return loc


def _procedure_block() -> dict[str, Any]:
    data = _load_procedure_data()
    proc = data["procedures"].get(PROCEDURE_ID)
    if not proc:
        raise ValueError(f"Procedure {PROCEDURE_ID!r} missing from procedure.yaml")
    return proc


def render_pdf(
    *,
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
    """Produce a personalized EGD + pH-MII PDF as bytes."""
    from weasyprint import HTML

    _reset_caches_for_live_dev()
    location = _location_block(location_id)
    procedure = _procedure_block()

    replacements = {
        **skill.build_practice_placeholders(lang),
        **skill.build_location_placeholders(location, lang),
        # appt_dt drives the per-row "by {date}" line under each medication stop.
        # Without it the builder emits the same rows the public handout uses.
        **skill.build_egdph_placeholders(procedure, lang, location=location, procedure_id=PROCEDURE_ID, appt_dt=appt_dt),
    }

    physician = physicians.lookup(physician_id)
    replacements["{{PRACTICE_FOOTER}}"] = physicians.footer_line(physician_id, lang)
    replacements["{{PERFORMING_PHYSICIAN}}"] = physician["name_short"]

    # Mobile URL points at the egdph subdomain (procedure-level mobile_subdomain
    # override in procedure.yaml — wins over the location's default "egd86").
    # FEEDBACK_URL appends ?feedback=1&source=print so the survey modal auto-opens.
    sub = procedure.get("mobile_subdomain") or location.get("mobile_subdomain", "egdph")
    lang_seg = "es/" if lang == "es" else ""
    hash_params = f"#d={appt_dt.date().isoformat()}&t={appt_dt.strftime('%H%M')}"
    mobile_url = f"https://{sub}.giready.com/{lang_seg}{hash_params}"
    feedback_url = f"https://{sub}.giready.com/{lang_seg}?feedback=1&source=print{hash_params}"
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""
    youtube_url = skill.procedure_qr_target(procedure, "youtube_url", lang)
    portal_url = skill._qr_target("portal_url")
    gikids_url = skill.procedure_qr_target(procedure, "gikids_url")
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
        "{{APPT_DATE_HUMAN}}":      appt_date_human,
        "{{APPT_TIME}}":            appt_time_display,
        "{{ARRIVAL_TIME}}":         arrival_time_display,
        "{{FOLLOWUP_BLOCK_HTML}}":  followup_block_html,
    }

    template_path = TEMPLATE_BY_LANG.get(lang)
    if template_path is None:
        raise ValueError(f"No egd_phmii template for lang={lang!r}")
    html = template_path.read_text(encoding="utf-8")
    # Calm theme: swap the forked template's navy <style> for the shared Calm
    # stylesheet (+ personalization + EGD-table rules) before substitution.
    html = swap_calm(html, include_egd=True)
    all_replacements = {**replacements, **qr_replacements, **personalization_replacements}
    for token, value in all_replacements.items():
        html = html.replace(token, str(value))

    qr_uris = {
        "qr-mobile":   skill._png_to_data_uri(skill._generate_qr(feedback_url)),
        "qr-feedback": skill._png_to_data_uri(skill._generate_qr(feedback_url)),
        "qr-maps":     skill._png_to_data_uri(skill._generate_qr(maps_url)) if maps_url else "",
        "qr-youtube":  skill._png_to_data_uri(skill._generate_qr(youtube_url)) if youtube_url else "",
        "qr-portal":   skill._png_to_data_uri(skill._generate_qr(portal_url)) if portal_url else "",
        "qr-gikids":   skill._png_to_data_uri(skill._generate_qr(gikids_url)) if gikids_url else "",
    }
    html = skill._inject_qr_into_imgs(html, qr_uris)

    html = personalization.apply_pz_substitutions(html, appt_dt, lang)

    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders: {sorted(set(unreplaced))}")

    html = skill._inject_shared_print_css(html)

    base_url = (SKILL_ROOT / "templates").as_uri() + "/"
    if include_directions:
        from ..directions_inline import inject_into_handout
        html = inject_into_handout(html, location_id, lang)
    from ..pdf_tagging import write_pdf_tagged
    return write_pdf_tagged(HTML(string=html, base_url=base_url))
