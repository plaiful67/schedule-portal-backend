"""Bowel-prep adapter — produces a personalized PDF by reusing the vendored
skill's substitution functions but with a custom QR pointing at the
schedule.giready.com deep-link receiver.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any, Literal

import yaml

from ._paths import skill_dir

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SKILL_ROOT = skill_dir("bowel-prep-generator")
SKILL_RENDER = SKILL_ROOT / "scripts" / "render.py"
TEMPLATES_DIR = BACKEND_DIR / "app" / "templates" / "bowel_prep"
TEMPLATE_BY_VARIANT = {
    "standard": TEMPLATES_DIR / "print-personalized.en.html",
    "combined": TEMPLATES_DIR / "combined-print-personalized.en.html",
}


def _load_skill_module():
    """Load the bowel-prep skill's render.py under a unique module name so
    it doesn't collide with other vendored skills' `render` modules.
    """
    name = "_bowel_prep_render"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SKILL_RENDER)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load bowel-prep render module from {SKILL_RENDER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


skill = _load_skill_module()

# Re-point the skill's module-level paths so practice.yaml + partials
# resolve to the chosen skill source (live ~/.claude/skills or vendor/).
skill.SKILL_DIR = SKILL_ROOT
skill.TEMPLATES = SKILL_ROOT / "templates"
skill.PARTIALS_DIR = skill.TEMPLATES / "partials"
skill.DOSING_PATH = SKILL_ROOT / "data" / "dosing.yaml"
skill.PRACTICE_PATH = SKILL_ROOT / "practice.yaml"
skill._PRACTICE_CACHE = None
skill._PARTIALS_CACHE = {}


def _reset_caches_for_live_dev():
    """Skill modules cache practice.yaml + partials at module load. When we
    point at the live ~/.claude/skills/ directory, that cache means edits
    don't appear until uvicorn restarts. Resetting at request time costs
    one tiny YAML read per render and lets live edits land immediately.
    """
    skill._PRACTICE_CACHE = None
    skill._PARTIALS_CACHE = {}


def _load_dosing() -> dict[str, Any]:
    with open(skill.DOSING_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _band_for_id(band_id: str) -> dict[str, Any]:
    dosing = _load_dosing()
    for b in dosing["bands"]:
        if b["id"] == band_id and b.get("protocol") == "standard":
            return b
    raise ValueError(f"No standard-protocol band found for id={band_id!r}")


def _location_block(location_id: str) -> dict[str, Any]:
    dosing = _load_dosing()
    loc = dosing["locations"].get(location_id)
    if not loc:
        raise ValueError(f"Unknown location_id={location_id!r}")
    return loc


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
    variant: Literal["standard", "combined"] = "standard",
) -> bytes:
    """Produce a personalized bowel-prep (or combined EGD+colonoscopy) PDF."""
    from weasyprint import HTML  # imported here so failures are 500s, not import-time crashes

    _reset_caches_for_live_dev()
    template_path = TEMPLATE_BY_VARIANT.get(variant)
    if template_path is None:
        raise ValueError(f"Unknown variant={variant!r}")

    band = _band_for_id(band_id)
    location = _location_block(location_id)

    # Build the same replacements dict the skill's batch render uses.
    replacements = skill.build_strings(band, lang, location=location)
    replacements.update(skill.build_location_placeholders(location, lang))
    replacements.update(skill.build_practice_placeholders(lang))

    # Compute the same URL placeholders render_pdf_print would have set.
    # Combined renders use the EGD+colonoscopy mobile subdomain (egdcolon{,86}).
    subdomain_key = "mobile_subdomain_combined" if variant == "combined" else "mobile_subdomain"
    subdomain = location.get(subdomain_key) or location.get("mobile_subdomain", "prep")
    mobile_path = band.get("mobile_path", "")
    # The MOBILE_URL is what the QR caption links to; we want it to point at
    # our deep-link viewer too, so scanning and tapping land in the same place.
    # The actual URL is opaque to the caller — we keep it short on the page.
    qr_replacements = {
        "{{MOBILE_QR_DATA_URI}}": deep_link_qr_data_uri,
        "{{MAPS_QR_DATA_URI}}":   skill._png_to_data_uri(skill._generate_maps_qr(
            location.get(f"maps_url_{lang}", location.get("maps_url_en", ""))
        )),
        "{{MOBILE_URL}}":          f"https://{subdomain}.giready.com/{mobile_path}/" + ("es/" if lang == "es" else ""),
        "{{MAPS_URL}}":            location.get(f"maps_url_{lang}", location.get("maps_url_en", "")),
        "{{YOUTUBE_URL}}":         skill._qr_target("youtube_url_es" if lang == "es" else "youtube_url_en"),
        "{{PORTAL_URL}}":          skill._qr_target("portal_url"),
        "{{GIKIDS_URL}}":          skill._qr_target("gikids_url"),
        "{{LOCATION_PHONE_TEL}}":  re.sub(r"\D", "", location.get("phone", "")),
    }

    # Personalized callout placeholders.
    personalization_replacements = {
        "{{APPT_DATE_HUMAN}}": appt_date_human,
        "{{APPT_TIME}}":       appt_time_display,
        "{{ARRIVAL_TIME}}":    arrival_time_display,
        "{{STOP_MEDS_BLOCK}}": stop_meds_block_html,
    }

    # Load the personalized template + the skill's partials.
    html = template_path.read_text(encoding="utf-8")
    partials = skill._load_partials(lang)
    all_replacements = {**partials, **replacements, **qr_replacements, **personalization_replacements}
    for token, value in all_replacements.items():
        html = html.replace(token, str(value))

    # The skill's QR <img id="qr-mobile"> tag still has a placeholder src;
    # rewrite it to the deep-link QR (matching render_pdf_print's id-based fixup).
    html = skill._inject_qr_into_imgs(html, {
        "qr-mobile": deep_link_qr_data_uri,
        "qr-maps":   qr_replacements["{{MAPS_QR_DATA_URI}}"],
    })

    # Strip any time-box wrapper containing the omit marker (mirror skill behavior).
    if skill.REMOVE_PARAGRAPH_MARKER in html:
        omit_pat = re.compile(
            r'<div class="time-box">(?:(?!</div>).)*?'
            + re.escape(skill.REMOVE_PARAGRAPH_MARKER)
            + r'(?:(?!</div>).)*?</div>\s*', re.DOTALL
        )
        html = omit_pat.sub("", html)

    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", html)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders: {sorted(set(unreplaced))}")

    # Resolve relative URLs (logo PNG, etc.) against the skill's templates/ dir.
    base_url = (SKILL_ROOT / "templates").as_uri() + "/"
    pdf_bytes = HTML(string=html, base_url=base_url).write_pdf()
    return pdf_bytes
