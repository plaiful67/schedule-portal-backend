"""Bowel-prep adapter — produces a personalized PDF by reusing the vendored
skill's substitution functions but with a custom QR pointing at the
schedule.giready.com deep-link receiver.
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
from ._paths import skill_dir

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SKILL_ROOT = skill_dir("bowel-prep-generator")
SKILL_RENDER = SKILL_ROOT / "scripts" / "render.py"
TEMPLATES_DIR = BACKEND_DIR / "app" / "templates" / "bowel_prep"
TEMPLATE_BY_VARIANT_LANG = {
    ("standard", "en"): TEMPLATES_DIR / "print-personalized.en.html",
    ("standard", "es"): TEMPLATES_DIR / "print-personalized.es.html",
    ("combined", "en"): TEMPLATES_DIR / "combined-print-personalized.en.html",
    ("combined", "es"): TEMPLATES_DIR / "combined-print-personalized.es.html",
}
# Infant-protocol bands (under-15 kg) use separate template sets derived
# from the vendored infant-print sources. The MiraLAX (oral) variant is
# selected when band["protocol"] == "infant"; the saline-enema (in-office)
# variant when band["protocol"] == "infant-enema".
INFANT_TEMPLATE_BY_VARIANT_LANG = {
    ("standard", "en"): TEMPLATES_DIR / "infant-print-personalized.en.html",
    ("standard", "es"): TEMPLATES_DIR / "infant-print-personalized.es.html",
    ("combined", "en"): TEMPLATES_DIR / "combined-infant-print-personalized.en.html",
    ("combined", "es"): TEMPLATES_DIR / "combined-infant-print-personalized.es.html",
}
INFANT_ENEMA_TEMPLATE_BY_VARIANT_LANG = {
    ("standard", "en"): TEMPLATES_DIR / "infant-enema-print-personalized.en.html",
    ("standard", "es"): TEMPLATES_DIR / "infant-enema-print-personalized.es.html",
    ("combined", "en"): TEMPLATES_DIR / "combined-infant-enema-print-personalized.en.html",
    ("combined", "es"): TEMPLATES_DIR / "combined-infant-enema-print-personalized.es.html",
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
        if b["id"] == band_id:
            return b
    raise ValueError(f"No band found for id={band_id!r}")


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
    physician_id: str,
    appt_date_human: str,
    appt_time_display: str,
    arrival_time_display: str,
    followup_block_html: str,
    appt_dt: datetime,
    variant: Literal["standard", "combined"] = "standard",
) -> bytes:
    """Produce a personalized bowel-prep (or combined EGD+colonoscopy) PDF."""
    from weasyprint import HTML  # imported here so failures are 500s, not import-time crashes

    _reset_caches_for_live_dev()
    template_path = TEMPLATE_BY_VARIANT_LANG.get((variant, lang))
    if template_path is None:
        raise ValueError(f"No template for variant={variant!r} lang={lang!r}")

    band = _band_for_id(band_id)
    location = _location_block(location_id)

    protocol = band.get("protocol")
    if protocol == "infant":
        template_path = INFANT_TEMPLATE_BY_VARIANT_LANG.get((variant, lang))
    elif protocol == "infant-enema":
        template_path = INFANT_ENEMA_TEMPLATE_BY_VARIANT_LANG.get((variant, lang))
    if protocol in ("infant", "infant-enema") and template_path is None:
        raise ValueError(f"No infant template for variant={variant!r} lang={lang!r}")

    # Build the same replacements dict the skill's batch render uses.
    # `location` is forwarded so build_contingency_block resolves the per-site
    # NPO window (2 h SCC vs 3 h PMCH) instead of falling through to the 2-hour
    # default. LOCATION_* placeholders still come from build_location_placeholders.
    # Infant bands (both MiraLAX and saline-enema variants) have no oral-prep
    # dosing fields, so the skill provides a separate minimal builder for those
    # protocols.
    if band.get("protocol") in ("infant", "infant-enema"):
        replacements = skill.build_infant_strings(band, lang)
    else:
        replacements = skill.build_strings(band, lang, location)
    replacements.update(skill.build_location_placeholders(location, lang))
    replacements.update(skill.build_practice_placeholders(lang))

    # Performing-physician personalization: override the group-footer line
    # with a single-doctor line, and supply the {{PERFORMING_PHYSICIAN}} token
    # used by the top callout in the print template.
    physician = physicians.lookup(physician_id)
    replacements["{{PRACTICE_FOOTER}}"] = physicians.footer_line(physician_id, lang)
    replacements["{{PERFORMING_PHYSICIAN}}"] = physician["name_short"]

    # MOBILE_URL is the cover-row QR's clickable href AND the QR image's encoded
    # target. We point both at the existing per-procedure mobile site with
    # `#d=YYYY-MM-DD&t=HHMM` hash params — those sites already personalize
    # themselves from the hash via the _personalize.{en,es}.html JS partial.
    subdomain_key = "mobile_subdomain_combined" if variant == "combined" else "mobile_subdomain"
    subdomain = location.get(subdomain_key) or location.get("mobile_subdomain", "prep")
    mobile_path = band.get("mobile_path", "")
    lang_seg = "es/" if lang == "es" else ""
    hash_params = f"#d={appt_dt.date().isoformat()}&t={appt_dt.strftime('%H%M')}"
    mobile_url = f"https://{subdomain}.giready.com/{lang_seg}{mobile_path}/{hash_params}"
    mobile_qr_data_uri = skill._png_to_data_uri(skill._generate_maps_qr(mobile_url))

    qr_replacements = {
        "{{MOBILE_QR_DATA_URI}}": mobile_qr_data_uri,
        "{{MAPS_QR_DATA_URI}}":   skill._png_to_data_uri(skill._generate_maps_qr(
            location.get(f"maps_url_{lang}", location.get("maps_url_en", ""))
        )),
        "{{MOBILE_URL}}":          mobile_url,
        "{{MAPS_URL}}":            location.get(f"maps_url_{lang}", location.get("maps_url_en", "")),
        "{{YOUTUBE_URL}}":         skill._qr_target("youtube_url_es" if lang == "es" else "youtube_url_en"),
        "{{PORTAL_URL}}":          skill._qr_target("portal_url"),
        "{{GIKIDS_URL}}":          skill._qr_target("gikids_url"),
        "{{LOCATION_PHONE_TEL}}":  re.sub(r"\D", "", location.get("phone", "")),
    }

    # Personalized callout placeholders.
    personalization_replacements = {
        "{{APPT_DATE_HUMAN}}":      appt_date_human,
        "{{APPT_TIME}}":            appt_time_display,
        "{{ARRIVAL_TIME}}":         arrival_time_display,
        "{{FOLLOWUP_BLOCK_HTML}}":  followup_block_html,
    }
    # Forward-compat: the canonical bowel-prep skill has removed the
    # contingency / shopping-quantity helpers, but the scheduler's
    # personalized templates still reference those tokens. When the adapter
    # runs against the live ~/.claude/skills copy, fall back to empty strings
    # so the unreplaced-token guard passes. The vendored snapshot still
    # provides real values, and `replacements` is merged BEFORE these stubs,
    # so the real values win when present (dict-merge override order in
    # `all_replacements` below).
    forward_compat_stubs = {
        "{{HTML_CONTINGENCY_BLOCK}}": "",
        "{{HTML_GATORADE_SHOPPING}}": "",
        "{{HTML_MIRALAX_SHOPPING}}":  "",
    }

    # Load the personalized template + the skill's partials. Post-phase-2 the
    # _medications_note partial carries the standardized yellow callout
    # (drug list + meds.giready.com QR + verify line), so we let it render
    # rather than suppress it — replaces the older server-side STOP_MEDS_BLOCK
    # injection that used to live where {{PARTIAL_MEDICATIONS_NOTE}} sits now.
    html = template_path.read_text(encoding="utf-8")
    partials = skill._load_partials(lang)
    # Pass 1: expand partials FIRST so any tokens they introduce
    # (e.g. {{HTML_MIRALAX_SHOPPING}} inside the partial shopping table)
    # are visible to the main substitution pass below.
    for token, value in partials.items():
        html = html.replace(token, str(value))
    # Pass 2: real values from build_strings + per-request personalization.
    substitutions = {**replacements, **qr_replacements, **personalization_replacements}
    for token, value in substitutions.items():
        html = html.replace(token, str(value))
    # Pass 3: forward-compat empty stubs for tokens the canonical skill no
    # longer provides (contingency / shopping-quantities). Only fills tokens
    # that survived passes 1 + 2, so real values from the vendored snapshot
    # still win when present.
    for token, value in forward_compat_stubs.items():
        html = html.replace(token, str(value))

    # Rewrite all five QR <img id="qr-*"> srcs (mobile + maps + 3 resource cards).
    html = skill._inject_qr_into_imgs(html, {
        "qr-mobile":  mobile_qr_data_uri,
        "qr-maps":    qr_replacements["{{MAPS_QR_DATA_URI}}"],
        "qr-youtube": skill._png_to_data_uri(skill._generate_maps_qr(qr_replacements["{{YOUTUBE_URL}}"])),
        "qr-portal":  skill._png_to_data_uri(skill._generate_maps_qr(qr_replacements["{{PORTAL_URL}}"])),
        "qr-gikids": skill._png_to_data_uri(skill._generate_maps_qr(qr_replacements["{{GIKIDS_URL}}"])),
    })

    # Server-side equivalent of the mobile-page pz-only JS: walk every
    # <span class="pz-only" data-pz-time-mins=...> and substitute the
    # back-calculated clock time so the print PDF gets concrete times too.
    html = personalization.apply_pz_substitutions(html, appt_dt, lang)

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
