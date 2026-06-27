"""Composed adapter — assembles a personalized "base procedure + add-ons" PDF.

    base="egd"          → delegates to the egd adapter with composition-overlay
                          kwargs (no bowel prep).
    base="colonoscopy"  → colonoscopy-only bowel-prep base + add-on overlay.
    base="combined"     → EGD+colonoscopy combined prep + add-on overlay.

The actual rendering lives in the egd / bowel_prep adapters; this module only
resolves the composition (title + blurbs via the vendored resolver), validates
the add-on / knob inputs, and dispatches. No forked render body — so an EGD
pipeline change (QR target, feedback bar, pdf-tagging) can't silently diverge
between the plain-EGD and composed-EGD paths.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ._paths import is_live_dev, load_compose_module

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = BACKEND_DIR / "app" / "templates" / "composed"
TEMPLATE_BY_LANG = {
    "en": TEMPLATES_DIR / "print-personalized.en.html",
    "es": TEMPLATES_DIR / "print-personalized.es.html",
}

compose_module = load_compose_module()


class CompositionInputError(ValueError):
    """An add-on id or knob pick the registry doesn't recognize. Raised at the
    composition boundary so app.main can map it to a 422 (not a 500) — the
    frontend gates this, but a direct API caller deserves a useful error."""


def _compose(base: str, add_ons: list[str], knob_picks: dict[str, str], lang: str):
    """Resolve the composition, translating registry-lookup failures (unknown
    add-on → KeyError, invalid knob pick → ValueError) into
    CompositionInputError so they surface as a 422 rather than a 500."""
    if is_live_dev():
        compose_module.reset_registry_cache()
    try:
        return compose_module.compose(base, add_ons, knob_picks, lang)
    except (KeyError, ValueError) as e:
        raise CompositionInputError(str(e)) from e


def render_pdf(
    *,
    add_ons: list[str],
    knob_picks: dict[str, str],
    location_id: str,
    lang: str,
    physician_id: str,
    appt_date_human: str,
    appt_time_display: str,
    arrival_time_display: str,
    followup_block_html: str,
    appt_dt: datetime,
    include_directions: bool = True,
    base: str = "egd",
    weight_band: str | None = None,
    prep_type: str = "miralax",
) -> bytes:
    """Produce a personalized composed PDF as bytes.

    base="egd": EGD + add-ons only.
    base="colonoscopy"/"combined": bowel-prep base + add-ons overlay.
    """
    # Validate add-on / knob inputs up front (→ 422 on a bad id/pick) regardless
    # of which base path renders.
    comp = _compose(base, add_ons, knob_picks, lang)

    if base in ("colonoscopy", "combined"):
        if weight_band is None:
            raise ValueError(f"weight_band required for base={base!r}")
        from . import bowel_prep
        variant = "combined" if base == "combined" else "standard"
        # PROCEDURE_LABEL = base procedure only (no add-on suffix) — consistent
        # with the egd composed path, so a running/band label can't overflow.
        base_label = compose_module.compose_title(base, [], lang)
        return bowel_prep.render_pdf(
            band_id=weight_band, location_id=location_id, lang=lang,
            physician_id=physician_id, appt_date_human=appt_date_human,
            appt_time_display=appt_time_display, arrival_time_display=arrival_time_display,
            followup_block_html=followup_block_html, appt_dt=appt_dt,
            variant=variant, prep_type=prep_type, include_directions=include_directions,
            addon_blurbs_html=comp.blurbs_html, composed_title=comp.title,
            composed_procedure_label=base_label,
            addon_title_suffix=(" + " + comp.addon_title) if comp.addon_title else "",
            addon_procedure_items_html=comp.procedure_items_html,
            addon_team_blurbs_html=comp.team_blurbs_html)

    # base == "egd": delegate to the egd adapter with composition overlay,
    # pointing it at the composed personalized template.
    from . import egd
    return egd.render_pdf(
        location_id=location_id, lang=lang, physician_id=physician_id,
        appt_date_human=appt_date_human, appt_time_display=appt_time_display,
        arrival_time_display=arrival_time_display, followup_block_html=followup_block_html,
        appt_dt=appt_dt, include_directions=include_directions,
        add_ons=add_ons, knob_picks=knob_picks, template_by_lang=TEMPLATE_BY_LANG)
