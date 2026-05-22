"""EGD-only adapter — produces a personalized EGD PDF by reusing the
vendored egd-handout-generator skill's substitution functions but with a
custom QR pointing at the schedule.giready.com deep-link receiver.
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
from ._paths import skill_dir

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SKILL_ROOT = skill_dir("egd-handout-generator")
SKILL_RENDER = SKILL_ROOT / "scripts" / "render.py"
TEMPLATES_DIR = BACKEND_DIR / "app" / "templates" / "egd"
TEMPLATE_BY_LANG = {
    "en": TEMPLATES_DIR / "print-personalized.en.html",
    "es": TEMPLATES_DIR / "print-personalized.es.html",
}


def _load_skill_module():
    """Load the EGD skill's render.py under a unique module name so it
    doesn't collide with the bowel-prep skill's `render` already cached
    in sys.modules.
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

# Re-point the skill's module-level paths so they resolve to the chosen
# skill source (live ~/.claude/skills or vendor/).
skill.SKILL_DIR = SKILL_ROOT
skill.TEMPLATES = SKILL_ROOT / "templates"
skill.PROCEDURE_PATH = SKILL_ROOT / "data" / "procedure.yaml"
skill.PRACTICE_PATH = SKILL_ROOT / "practice.yaml"
skill._PRACTICE_CACHE = None

# Static-handout sites at *.giready.com use the GI Ready logo (the skill's
# practice.yaml ships logo_filename: "giready-logo.png"). Scheduler-generated
# personalized PDFs keep the PMCH logo per Sebastian's 2026-05-22 brand split.
# The override has to live in code, not vendor/*.yaml, because `make vendor-sync`
# re-copies the static skill on every deploy and would overwrite any YAML edit.
# This patch also covers egd_phmii.py since it shares the same skill module.
if not getattr(skill._practice, "_pmch_override_applied", False):
    _original_practice = skill._practice
    def _practice_with_pmch_override():
        data = _original_practice()
        data["practice"]["logo_filename"] = "logo-pmch.png"
        return data
    _practice_with_pmch_override._pmch_override_applied = True  # type: ignore[attr-defined]
    skill._practice = _practice_with_pmch_override


def _reset_caches_for_live_dev():
    """Reset practice.yaml cache at request time so live edits to the skill
    YAML land in the next render without a uvicorn restart.
    """
    skill._PRACTICE_CACHE = None


def _load_procedure_data() -> dict[str, Any]:
    with open(skill.PROCEDURE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _location_block(location_id: str) -> dict[str, Any]:
    data = _load_procedure_data()
    loc = data["locations"].get(location_id)
    if not loc:
        raise ValueError(f"Unknown location_id={location_id!r}")
    return loc


def _procedure_block(procedure_id: str = "egd") -> dict[str, Any]:
    data = _load_procedure_data()
    proc = data["procedures"].get(procedure_id)
    if not proc:
        raise ValueError(f"Unknown procedure id={procedure_id!r}")
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
) -> bytes:
    """Produce a personalized EGD-only PDF as bytes."""
    from weasyprint import HTML

    _reset_caches_for_live_dev()
    location = _location_block(location_id)
    procedure = _procedure_block("egd")

    replacements = {
        **skill.build_practice_placeholders(lang),
        **skill.build_location_placeholders(location, lang),
        **skill.build_egd_placeholders(procedure, lang, location=location),
    }

    # Performing-physician personalization: same model as bowel_prep adapter.
    # Doctors list is sourced from the bowel-prep skill's practice.yaml (the
    # EGD skill's practice.yaml doesn't carry a doctors block today).
    physician = physicians.lookup(physician_id)
    replacements["{{PRACTICE_FOOTER}}"] = physicians.footer_line(physician_id, lang)
    replacements["{{PERFORMING_PHYSICIAN}}"] = physician["name_short"]

    # MOBILE_URL = the existing EGD mobile site URL + `#d=&t=` hash so the
    # destination page personalizes itself via its built-in _personalize JS.
    # FEEDBACK_URL = same URL with ?feedback=1&source=print spliced in
    # before the hash so survey.js auto-opens with the print-vs-phone q3
    # variant. Query string must come BEFORE the URL fragment.
    proc_data = _load_procedure_data()
    sub = location.get("mobile_subdomain", "") or proc_data.get("mobile_site", {}).get("subdomain", "egd")
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
        # Cover-QR href: matches the cover QR PNG so click and scan both
        # land on the survey-enabled mobile page.
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
        raise ValueError(f"No EGD template for lang={lang!r}")
    html = template_path.read_text(encoding="utf-8")
    all_replacements = {**replacements, **qr_replacements, **personalization_replacements}
    for token, value in all_replacements.items():
        html = html.replace(token, str(value))

    # Swap the QR <img id> srcs to data URIs. qr-mobile (cover) and
    # qr-feedback (mid-doc) both encode the survey-enabled URL so either
    # scan path opens the modal tagged source=print.
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
    html = personalization.apply_pz_substitutions(html, appt_dt, lang)

    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders: {sorted(set(unreplaced))}")

    # Splice shared print-base.css in front of the template's own <style>
    # block so design-token + feedback-cell changes propagate without
    # editing every template. Template-local CSS still overrides.
    html = skill._inject_shared_print_css(html)

    base_url = (SKILL_ROOT / "templates").as_uri() + "/"
    return HTML(string=html, base_url=base_url).write_pdf()
