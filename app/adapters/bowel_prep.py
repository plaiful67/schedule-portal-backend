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
from ._calm import swap_calm
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
# Lactulose templates (scheduler-only; mobile sites at preplact*.giready.com).
# Selected when prep_type == "lactulose"; the user's weight_band is mapped to
# the lactulose-specific band id (e.g. "15-20" -> "15-20-lact") below.
LACTULOSE_STANDARD_TEMPLATE_BY_VARIANT_LANG = {
    ("standard", "en"): TEMPLATES_DIR / "lactulose-standard-print-personalized.en.html",
    ("standard", "es"): TEMPLATES_DIR / "lactulose-standard-print-personalized.es.html",
    ("combined", "en"): TEMPLATES_DIR / "combined-lactulose-standard-print-personalized.en.html",
    ("combined", "es"): TEMPLATES_DIR / "combined-lactulose-standard-print-personalized.es.html",
}
LACTULOSE_INFANT_TEMPLATE_BY_VARIANT_LANG = {
    ("standard", "en"): TEMPLATES_DIR / "lactulose-infant-print-personalized.en.html",
    ("standard", "es"): TEMPLATES_DIR / "lactulose-infant-print-personalized.es.html",
    ("combined", "en"): TEMPLATES_DIR / "combined-lactulose-infant-print-personalized.en.html",
    ("combined", "es"): TEMPLATES_DIR / "combined-lactulose-infant-print-personalized.es.html",
}
# weight_band (user-facing) -> lactulose band id (in dosing.yaml).
LACTULOSE_BAND_MAP = {
    "under-15": "under-15-lact",
    "15-20":    "15-20-lact",
    "21-30":    "21-30-lact",
}
# Hidden subdomains: prep_type=lactulose routes to these instead of prep* / egdcolon*.
LACTULOSE_SUBDOMAIN = {
    ("standard", "scc"):  "preplact",
    ("standard", "pmch"): "preplact86",
    ("combined", "scc"):  "egdcolonlact",
    ("combined", "pmch"): "egdcolonlact86",
}

# CLENPIQ templates (scheduler-only; mobile sites at prepclenpiq*.giready.com).
# Selected when prep_type == "clenpiq"; the user's weight_band is mapped to
# the single unified "clenpiq" band id (dosing is identical across all
# eligible weights, so we don't keep per-weight band entries).
CLENPIQ_STANDARD_TEMPLATE_BY_VARIANT_LANG = {
    ("standard", "en"): TEMPLATES_DIR / "clenpiq-standard-print-personalized.en.html",
    ("standard", "es"): TEMPLATES_DIR / "clenpiq-standard-print-personalized.es.html",
    ("combined", "en"): TEMPLATES_DIR / "combined-clenpiq-standard-print-personalized.en.html",
    ("combined", "es"): TEMPLATES_DIR / "combined-clenpiq-standard-print-personalized.es.html",
}
# weight_band (user-facing) -> clenpiq band id. All three eligible bands
# collapse to the same dosing.yaml entry — dosing is identical across them.
CLENPIQ_BAND_MAP = {
    "31-40":   "clenpiq",
    "41-50":   "clenpiq",
    "over-50": "clenpiq",
}
# Hidden subdomains: prep_type=clenpiq routes to these instead of prep* / egdcolon*.
CLENPIQ_SUBDOMAIN = {
    ("standard", "scc"):  "prepclenpiq",
    ("standard", "pmch"): "prepclenpiq86",
    ("combined", "scc"):  "egdcolonclenpiq",
    ("combined", "pmch"): "egdcolonclenpiq86",
}

# SUPREP templates (scheduler-only; mobile sites at prepsuprep*.giready.com).
# Selected when prep_type == "suprep"; the user's weight_band is mapped to
# the single unified "suprep" band id (only "over-50" is eligible — SUPREP
# is FDA-approved age 12+).
SUPREP_STANDARD_TEMPLATE_BY_VARIANT_LANG = {
    ("standard", "en"): TEMPLATES_DIR / "suprep-standard-print-personalized.en.html",
    ("standard", "es"): TEMPLATES_DIR / "suprep-standard-print-personalized.es.html",
    ("combined", "en"): TEMPLATES_DIR / "combined-suprep-standard-print-personalized.en.html",
    ("combined", "es"): TEMPLATES_DIR / "combined-suprep-standard-print-personalized.es.html",
}
# weight_band (user-facing) -> suprep band id. Only over-50 is eligible.
SUPREP_BAND_MAP = {
    "over-50": "suprep",
}
# Hidden subdomains: prep_type=suprep routes to these instead of prep* / egdcolon*.
SUPREP_SUBDOMAIN = {
    ("standard", "scc"):  "prepsuprep",
    ("standard", "pmch"): "prepsuprep86",
    ("combined", "scc"):  "egdcolonsuprep",
    ("combined", "pmch"): "egdcolonsuprep86",
}

# Partner-variant standard protocol — mirrors the lactulose / clenpiq pattern
# but keyed on physician_id instead of prep_type. Phase 1 ships with an empty
# registry: production behavior is unchanged until a partner is onboarded.
# Composition rule: partner variants apply only when prep_type=="miralax" and
# the band has a partner-specific entry in dosing.yaml. Lactulose, clenpiq,
# and infant protocols are not partner-overridable in Phase 1.
#
# To onboard a partner {slug}:
#   1. Add bands to dosing.yaml: {canonical-id}-{slug} (e.g. "15-20-dunn"),
#      protocol: standard, public: false. Clone the canonical band and edit
#      only the differing fields (miralax_capfuls, miralax_grams, gatorade_oz,
#      miralax_time, drink_cup_*, etc.).
#   2. Copy templates into app/templates/bowel_prep/:
#      {slug}-print-personalized.{en,es}.html
#      combined-{slug}-print-personalized.{en,es}.html
#      (Initial copies can be identical to canonical — they pick up the new
#      dose numbers via build_strings, which reads off the partner-specific band.)
#   3. Stand up four hidden Cloudflare Pages subdomains: prep{slug}{,86} and
#      egdcolon{slug}{,86}. None are linked from giready.com.
#   4. Add the partner's entry to all four dicts below.
# See docs/PLAYBOOK.md "Adding a partner-specific MiraLAX variant" for the full recipe.
PARTNER_OVERRIDE_PHYSICIANS: set[str] = set()
# physician_id -> {(variant, lang): Path}
PARTNER_TEMPLATE_BY_VARIANT_LANG: dict[str, dict[tuple[str, str], Path]] = {}
# physician_id -> {canonical_band_id: partner_band_id}
PARTNER_BAND_MAP: dict[str, dict[str, str]] = {}
# physician_id -> {(variant, location_id): subdomain_label}
PARTNER_SUBDOMAIN: dict[str, dict[tuple[str, str], str]] = {}


def is_partner_variant_active(physician_id: str, band_id: str | None, prep_type: str) -> bool:
    """Return True iff the partner-variant routing path applies for this
    request. Used by app/main.py to surface the flag in the analytics event.
    Mirrors the in-render check so the answer is authoritative."""
    if prep_type != "miralax" or band_id is None:
        return False
    if physician_id not in PARTNER_OVERRIDE_PHYSICIANS:
        return False
    return band_id in PARTNER_BAND_MAP.get(physician_id, {})


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

# Static-handout sites at *.giready.com use the GI Ready logo (the skill's
# practice.yaml ships logo_filename: "giready-logo.png"). Scheduler-generated
# personalized PDFs keep the PMCH logo per Sebastian's 2026-05-22 brand split.
# The override has to live in code, not vendor/*.yaml, because `make vendor-sync`
# re-copies the static skill on every deploy and would overwrite any YAML edit.
if not getattr(skill._practice, "_pmch_override_applied", False):
    _original_practice = skill._practice
    def _practice_with_pmch_override():
        data = _original_practice()
        data["practice"]["logo_filename"] = "logo-pmch.png"
        return data
    _practice_with_pmch_override._pmch_override_applied = True  # type: ignore[attr-defined]
    skill._practice = _practice_with_pmch_override


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
    prep_type: Literal["miralax", "lactulose", "clenpiq", "suprep"] = "miralax",
    include_directions: bool = True,
) -> bytes:
    """Produce a personalized bowel-prep (or combined EGD+colonoscopy) PDF.

    `prep_type="lactulose"` (scheduler-only backup) maps the user's weight
    band to a lactulose-specific band id in dosing.yaml, picks the
    lactulose template family, and emits a mobile URL pointing at the
    hidden preplact{,86} / egdcolonlact{,86} subdomains.

    `prep_type="clenpiq"` (scheduler-only alternative for kids 31 kg and up)
    collapses all three eligible user-facing weight bands to the single
    unified "clenpiq" dosing.yaml entry, picks the clenpiq template family,
    and emits a mobile URL pointing at the hidden prepclenpiq{,86} /
    egdcolonclenpiq{,86} subdomains.

    `prep_type="suprep"` (scheduler-only sulfate-based alternative for
    patients ≥50 kg, Rx, FDA age 12+) maps the over-50 user band to the
    unified "suprep" dosing.yaml entry, picks the suprep template family,
    and emits a mobile URL pointing at the hidden prepsuprep{,86} /
    egdcolonsuprep{,86} subdomains.
    """
    from weasyprint import HTML  # imported here so failures are 500s, not import-time crashes

    _reset_caches_for_live_dev()

    # For lactulose, swap the user's weight_band id for the lactulose-specific
    # band id BEFORE looking up the band (the dosing.yaml lactulose bands have
    # `-lact` suffix and the lactulose-* protocols).
    if prep_type == "lactulose":
        if band_id not in LACTULOSE_BAND_MAP:
            raise ValueError(
                f"prep_type=lactulose not supported for band_id={band_id!r} "
                f"(allowed: {sorted(LACTULOSE_BAND_MAP)})"
            )
        band_id = LACTULOSE_BAND_MAP[band_id]
    elif prep_type == "clenpiq":
        if band_id not in CLENPIQ_BAND_MAP:
            raise ValueError(
                f"prep_type=clenpiq not supported for band_id={band_id!r} "
                f"(allowed: {sorted(CLENPIQ_BAND_MAP)})"
            )
        band_id = CLENPIQ_BAND_MAP[band_id]
    elif prep_type == "suprep":
        if band_id not in SUPREP_BAND_MAP:
            raise ValueError(
                f"prep_type=suprep not supported for band_id={band_id!r} "
                f"(allowed: {sorted(SUPREP_BAND_MAP)})"
            )
        band_id = SUPREP_BAND_MAP[band_id]

    # Partner variant: applied only on top of the standard MiraLAX path
    # (lactulose / clenpiq are already routed by this point). The composition
    # is intentionally narrow in Phase 1 — see the registry comment at the
    # top of this module.
    partner_active = is_partner_variant_active(physician_id, band_id, prep_type)
    if partner_active:
        band_id = PARTNER_BAND_MAP[physician_id][band_id]

    band = _band_for_id(band_id)
    location = _location_block(location_id)

    protocol = band.get("protocol")
    if prep_type == "lactulose":
        if protocol == "lactulose-infant":
            template_path = LACTULOSE_INFANT_TEMPLATE_BY_VARIANT_LANG.get((variant, lang))
        elif protocol == "lactulose-standard":
            template_path = LACTULOSE_STANDARD_TEMPLATE_BY_VARIANT_LANG.get((variant, lang))
        else:
            raise ValueError(
                f"prep_type=lactulose expects a lactulose-* protocol, got {protocol!r}"
            )
        if template_path is None:
            raise ValueError(f"No lactulose template for variant={variant!r} lang={lang!r}")
    elif prep_type == "clenpiq":
        if protocol != "clenpiq-standard":
            raise ValueError(
                f"prep_type=clenpiq expects the clenpiq-standard protocol, got {protocol!r}"
            )
        template_path = CLENPIQ_STANDARD_TEMPLATE_BY_VARIANT_LANG.get((variant, lang))
        if template_path is None:
            raise ValueError(f"No clenpiq template for variant={variant!r} lang={lang!r}")
    elif prep_type == "suprep":
        if protocol != "suprep-standard":
            raise ValueError(
                f"prep_type=suprep expects the suprep-standard protocol, got {protocol!r}"
            )
        template_path = SUPREP_STANDARD_TEMPLATE_BY_VARIANT_LANG.get((variant, lang))
        if template_path is None:
            raise ValueError(f"No suprep template for variant={variant!r} lang={lang!r}")
    else:
        template_path = TEMPLATE_BY_VARIANT_LANG.get((variant, lang))
        if template_path is None:
            raise ValueError(f"No template for variant={variant!r} lang={lang!r}")
        if protocol == "infant":
            template_path = INFANT_TEMPLATE_BY_VARIANT_LANG.get((variant, lang))
        elif protocol == "infant-enema":
            template_path = INFANT_ENEMA_TEMPLATE_BY_VARIANT_LANG.get((variant, lang))
        if protocol in ("infant", "infant-enema") and template_path is None:
            raise ValueError(f"No infant template for variant={variant!r} lang={lang!r}")
        # Partner variant overrides the canonical standard template only.
        # Infant protocols are not partner-overridable in Phase 1.
        if partner_active and protocol == "standard":
            template_path = PARTNER_TEMPLATE_BY_VARIANT_LANG[physician_id].get((variant, lang))
            if template_path is None:
                raise ValueError(
                    f"No partner template for physician_id={physician_id!r} "
                    f"variant={variant!r} lang={lang!r}"
                )

    # Build the same replacements dict the skill's batch render uses.
    # `location` is forwarded so build_contingency_block resolves the per-site
    # NPO window (2 h SCC vs 3 h PMCH) instead of falling through to the 2-hour
    # default. LOCATION_* placeholders still come from build_location_placeholders.
    # Pick the dose-string builder by protocol family. Lactulose protocols
    # have their own builder that produces lactulose-specific placeholders
    # (HTML_LACTULOSE_*, HTML_DULCOLAX_TOTAL_LONG, etc.) used by the
    # lactulose print templates.
    if protocol in ("lactulose-infant", "lactulose-standard"):
        replacements = skill.build_lactulose_strings(band, lang, location)
    elif protocol == "clenpiq-standard":
        replacements = skill.build_clenpiq_strings(band, lang, location)
    elif protocol == "suprep-standard":
        replacements = skill.build_suprep_strings(band, lang, location)
    elif protocol in ("infant", "infant-enema"):
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
    # target. We point both at the per-procedure mobile site with
    # `#d=YYYY-MM-DD&t=HHMM` hash params — those sites already personalize
    # themselves from the hash via the _personalize.{en,es}.html JS partial.
    # For hidden-variant prep types, target the hidden subdomain instead.
    if prep_type == "lactulose":
        subdomain = LACTULOSE_SUBDOMAIN[(variant, location_id)]
    elif prep_type == "clenpiq":
        subdomain = CLENPIQ_SUBDOMAIN[(variant, location_id)]
    elif prep_type == "suprep":
        subdomain = SUPREP_SUBDOMAIN[(variant, location_id)]
    elif partner_active:
        subdomain = PARTNER_SUBDOMAIN[physician_id][(variant, location_id)]
    else:
        subdomain_key = "mobile_subdomain_combined" if variant == "combined" else "mobile_subdomain"
        subdomain = location.get(subdomain_key) or location.get("mobile_subdomain", "prep")
    mobile_path = band.get("mobile_path", "")
    lang_seg = "es/" if lang == "es" else ""
    hash_params = f"#d={appt_dt.date().isoformat()}&t={appt_dt.strftime('%H%M')}"
    mobile_url = f"https://{subdomain}.giready.com/{lang_seg}{mobile_path}/{hash_params}"
    # FEEDBACK_URL splices ?feedback=1&source=print BEFORE the hash so
    # survey.js auto-opens on arrival and tags the D1 row as PDF-origin.
    # Cover and mid-doc QRs both encode this URL; the cover-QR href also
    # uses it so click and scan land in the same place.
    feedback_url = f"https://{subdomain}.giready.com/{lang_seg}{mobile_path}/?feedback=1&source=print{hash_params}"
    mobile_qr_data_uri  = skill._png_to_data_uri(skill._generate_maps_qr(feedback_url))
    feedback_qr_data_uri = mobile_qr_data_uri  # identical encoding; reuse the bytes

    qr_replacements = {
        "{{MOBILE_QR_DATA_URI}}":   mobile_qr_data_uri,
        "{{FEEDBACK_QR_DATA_URI}}": feedback_qr_data_uri,
        "{{MAPS_QR_DATA_URI}}":     skill._png_to_data_uri(skill._generate_maps_qr(
            location.get(f"maps_url_{lang}", location.get("maps_url_en", ""))
        )),
        "{{MOBILE_URL}}":          feedback_url,
        "{{FEEDBACK_URL}}":        feedback_url,
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
    # Calm theme: swap the forked template's navy <style> for the shared Calm
    # stylesheet (+ personalization rules) before any token substitution.
    html = swap_calm(html)
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

    # Rewrite all six QR <img id="qr-*"> srcs (mobile + feedback + maps + 3 resource cards).
    html = skill._inject_qr_into_imgs(html, {
        "qr-mobile":   mobile_qr_data_uri,
        "qr-feedback": feedback_qr_data_uri,
        "qr-maps":     qr_replacements["{{MAPS_QR_DATA_URI}}"],
        "qr-youtube":  skill._png_to_data_uri(skill._generate_maps_qr(qr_replacements["{{YOUTUBE_URL}}"])),
        "qr-portal":   skill._png_to_data_uri(skill._generate_maps_qr(qr_replacements["{{PORTAL_URL}}"])),
        "qr-gikids":   skill._png_to_data_uri(skill._generate_maps_qr(qr_replacements["{{GIKIDS_URL}}"])),
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

    # Splice shared print-base.css in front of the template's own <style>
    # block so design-token + feedback-cell changes propagate without
    # editing every template. Template-local CSS still overrides.
    html = skill._inject_shared_print_css(html)

    # Resolve relative URLs (logo PNG, etc.) against the skill's templates/ dir.
    base_url = (SKILL_ROOT / "templates").as_uri() + "/"
    if include_directions:
        from ..directions_inline import inject_into_handout
        html = inject_into_handout(html, location_id, lang)
    from ..pdf_tagging import write_pdf_tagged
    pdf_bytes = write_pdf_tagged(HTML(string=html, base_url=base_url))
    return pdf_bytes
