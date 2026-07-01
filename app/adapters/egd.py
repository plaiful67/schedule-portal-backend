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
from typing import Any, Literal

import yaml

from .. import personalization, physicians
from ._calm import swap_calm
from ._office import all_doctors_block_html, to_office
from ._paths import is_live_dev, load_compose_module, shared_dir, skill_dir

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
# Cross-skill shared partials (footer/legal, feedback bar, NPO table) resolve
# from the vendored shared/ on Cloud Run (or ~/peds-gi-prep-system/shared live).
skill._SHARED_PARTIALS_DIR = shared_dir() / "partials"
skill.PROCEDURE_PATH = SKILL_ROOT / "data" / "procedure.yaml"
skill.PRACTICE_PATH = SKILL_ROOT / "practice.yaml"
skill._PRACTICE_CACHE = None
skill._SHARED_PARTIALS_CACHE = {}

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
    """Reset practice.yaml / shared-partials / composition-registry caches at
    request time so live edits to the skill YAML land in the next render without
    a uvicorn restart. No-op in production (immutable vendored source) so the
    caches stay warm and we don't re-read+parse on every request.
    """
    if not is_live_dev("egd-handout-generator"):
        return
    skill._PRACTICE_CACHE = None
    skill._SHARED_PARTIALS_CACHE = {}
    load_compose_module().reset_registry_cache()


def _load_procedure_data() -> dict[str, Any]:
    with open(skill.PROCEDURE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _location_block(location_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data if data is not None else _load_procedure_data()
    loc = data["locations"].get(location_id)
    if not loc:
        raise ValueError(f"Unknown location_id={location_id!r}")
    return loc


def _procedure_block(procedure_id: str = "egd", data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data if data is not None else _load_procedure_data()
    proc = data["procedures"].get(procedure_id)
    if not proc:
        raise ValueError(f"Unknown procedure id={procedure_id!r}")
    return proc


def render_pdf(
    *,
    location_id: str,
    lang: str,
    physician_id: str = "",
    appt_date_human: str = "",
    appt_time_display: str = "",
    arrival_time_display: str = "",
    followup_block_html: str = "",
    appt_dt: datetime | None = None,
    audience: Literal["patient", "office"] = "patient",
    include_directions: bool = True,
    add_ons: list[str] | None = None,
    knob_picks: dict[str, str] | None = None,
    template_by_lang: dict[str, Path] | None = None,
) -> bytes:
    """Produce a personalized EGD PDF as bytes.

    Plain EGD when ``add_ons`` is None/empty. When ``add_ons`` is given this is
    the composed EGD-base path: the title gains the add-on suffix and the add-on
    blurbs fill the template's {{ADDON_*}} slots. ``template_by_lang`` lets the
    composed adapter point at app/templates/composed/ instead of the plain EGD
    template — so the composition lives in ONE render body, not a fork.
    """
    from weasyprint import HTML

    office = audience == "office"
    if not office:
        if not physician_id:
            raise ValueError("render_pdf(audience='patient') requires physician_id")
        if appt_dt is None:
            raise ValueError("render_pdf(audience='patient') requires appt_dt")
    if office and add_ons:
        raise ValueError("audience='office' does not support composed add_ons")

    _reset_caches_for_live_dev()
    data = _load_procedure_data()  # one parse per request; shared by all blocks
    location = _location_block(location_id, data)
    procedure = _procedure_block("egd", data)

    replacements = {
        **skill.build_practice_placeholders(lang),
        **skill.build_location_placeholders(location, lang),
        **skill.build_egd_placeholders(procedure, lang, location=location),
    }

    # Performing-physician personalization: same model as bowel_prep adapter.
    # Doctors list is sourced from the bowel-prep skill's practice.yaml (the
    # EGD skill's practice.yaml doesn't carry a doctors block today).
    # Office (canonical) renders keep the group footer and drop the physician
    # callout entirely (to_office below), so this per-doctor override is
    # patient-only.
    if not office:
        physician = physicians.lookup(physician_id)
        replacements["{{PRACTICE_FOOTER}}"] = physicians.footer_line(physician_id, lang)
        replacements["{{PERFORMING_PHYSICIAN}}"] = physician["name_short"]

    # Composition overlay (EGD base + add-on procedures). Only active when the
    # caller passes add_ons; plain EGD renders exactly as before.
    comp = None
    if add_ons:
        compose_module = load_compose_module()
        comp = compose_module.compose("egd", add_ons, knob_picks or {}, lang)
        replacements["{{HTML_TITLE}}"] = comp.title  # full title for PDF metadata
        # PROCEDURE_LABEL = base-only (no add-on suffix) so a running/band label
        # can't overflow; the add-on shows via {{ADDON_TITLE_SUFFIX}}.
        replacements["{{PROCEDURE_LABEL}}"] = compose_module.compose_title("egd", [], lang)
        replacements["{{ADDON_TITLE_SUFFIX}}"] = (" + " + comp.addon_title) if comp.addon_title else ""
        replacements["{{ADDON_BLURBS}}"] = comp.blurbs_html
        replacements["{{ADDON_PROCEDURE_ITEMS}}"] = comp.procedure_items_html
        replacements["{{ADDON_TEAM_BLURBS}}"] = comp.team_blurbs_html

    # MOBILE_URL = the existing EGD mobile site URL + `#d=&t=` hash so the
    # destination page personalizes itself via its built-in _personalize JS.
    # FEEDBACK_URL = same URL with ?feedback=1&source=print spliced in
    # before the hash so survey.js auto-opens with the print-vs-phone q3
    # variant. Query string must come BEFORE the URL fragment.
    sub = location.get("mobile_subdomain", "") or data.get("mobile_site", {}).get("subdomain", "egd")
    lang_seg = "es/" if lang == "es" else ""
    # Office (canonical) handouts carry no appointment: bare generic mobile URL.
    hash_params = "" if office else f"#d={appt_dt.date().isoformat()}&t={appt_dt.strftime('%H%M')}"
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

    template_path = (template_by_lang or TEMPLATE_BY_LANG).get(lang)
    if template_path is None:
        raise ValueError(f"No EGD template for lang={lang!r}")
    html = template_path.read_text(encoding="utf-8")
    # Composed-overlay guard: fail loudly if any non-empty add-on content has no
    # matching slot in this template (per-slot, never silently drop). Shares the
    # bowel_prep guard so the EGD-base path can't regress the way bowel_prep was
    # hardened against.
    if comp is not None:
        from .bowel_prep import ComposedTemplateUnsupported, addon_slots_cover
        _content = bool(comp.blurbs_html or comp.procedure_items_html or comp.team_blurbs_html)
        if _content and not addon_slots_cover(
            html, comp.blurbs_html, comp.procedure_items_html, comp.team_blurbs_html
        ):
            raise ComposedTemplateUnsupported(
                f"composed add-ons requested but template {template_path.name!r} "
                f"has no matching ADDON_BLURBS / ADDON_PROCEDURE_ITEMS / ADDON_TEAM_BLURBS slot")
    # Calm theme: swap the forked template's navy <style> for the shared Calm
    # stylesheet (+ personalization + EGD-table rules) before substitution.
    html = swap_calm(html, include_egd=True)
    # Office (canonical) variant: strip per-patient chrome + swap in all-doctors
    # roster BEFORE substitution (removes the {{APPT_*}} / {{PERFORMING_PHYSICIAN}}
    # tokens with their blocks).
    if office:
        html = to_office(html, lang=lang, doctors_block_html=all_doctors_block_html(lang))
    # Expand shared partials first (feedback bar / NPO table); inner tokens like
    # {{FEEDBACK_URL}} resolve in the main pass below.
    for token, value in skill._load_shared_partials(lang).items():
        html = html.replace(token, str(value))
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
    # Office renders have no appointment: skip so pz-only date spans stay empty.
    if not office:
        html = personalization.apply_pz_substitutions(html, appt_dt, lang)

    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders: {sorted(set(unreplaced))}")

    # Splice shared print-base.css in front of the template's own <style>
    # block so design-token + feedback-cell changes propagate without
    # editing every template. Template-local CSS still overrides.
    html = skill._inject_shared_print_css(html)

    base_url = (SKILL_ROOT / "templates").as_uri() + "/"
    if include_directions:
        from ..directions_inline import inject_into_handout
        html = inject_into_handout(html, location_id, lang)
    from ..pdf_tagging import write_pdf_tagged
    return write_pdf_tagged(HTML(string=html, base_url=base_url))
