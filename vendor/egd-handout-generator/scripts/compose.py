"""Composition resolver — pure (PyYAML + stdlib only). Turns a selection of
base + add-ons + knob picks into a Composition (title, blurb HTML, knob values).
No rendering, no WeasyPrint, no network. Vendored unchanged into the backend in Phase 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "procedures.yaml"

_REGISTRY_CACHE: dict | None = None


def load_registry(path: Path | None = None) -> dict:
    """Load and cache the composition registry."""
    global _REGISTRY_CACHE
    if path is not None:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    if _REGISTRY_CACHE is None:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            _REGISTRY_CACHE = yaml.safe_load(f)
    return _REGISTRY_CACHE


def reset_registry_cache():
    """Clear the cached registry so a live edit to procedures.yaml lands on the
    next compose without a process restart — mirrors the adapters'
    _reset_caches_for_live_dev for practice.yaml / shared partials."""
    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None


def _frag(entry: dict, lang: str) -> str:
    return entry.get(f"title_fragment_{lang}", entry.get("title_fragment_en", ""))


def compose_title(base: str, add_ons: list[str], lang: str, registry: dict | None = None) -> str:
    reg = registry or load_registry()
    parts = [_frag(reg["bases"][base], lang)]
    ordered = [a for a in reg["add_ons"] if a in set(add_ons)]
    for a in ordered:
        parts.append(_frag(reg["add_ons"][a], lang))
    # Validate every requested add-on exists (catches typos the order-filter would silently drop).
    for a in add_ons:
        if a not in reg["add_ons"]:
            raise KeyError(a)
    return " + ".join(p for p in parts if p)


def compose_addon_title(add_ons, lang, registry=None):
    """Just the add-on title fragments, ' + '-joined (no base) — lets a handout
    render the base procedure name normally and the add-on(s) in smaller print."""
    reg = registry or load_registry()
    ordered = [a for a in reg["add_ons"] if a in set(add_ons)]
    for a in add_ons:
        if a not in reg["add_ons"]:
            raise KeyError(a)
    return " + ".join(_frag(reg["add_ons"][a], lang) for a in ordered if _frag(reg["add_ons"][a], lang))


def resolve_knobs(add_ons, knob_picks, lang, registry=None):
    reg = registry or load_registry()
    selected = set(add_ons)
    out = []
    for name, kdef in reg["knobs"].items():
        owner = kdef.get("owned_by_addon")
        if owner is None or owner not in selected:
            continue
        value = knob_picks.get(name, kdef["default"])
        if value not in kdef["options"]:
            raise ValueError(f"knob {name!r}: invalid pick {value!r}")
        fragment = kdef["options"][value].get(f"fragment_{lang}",
                                               kdef["options"][value].get("fragment_en", ""))
        out.append({"name": name, "value": value, "fragment": fragment})
    return out


def _addon_blurb(addon_id: str, entry: dict, lang: str, registry: dict) -> str:
    """A *_generic add-on uses its specialty's generic blurb; others use their own."""
    if addon_id.endswith("_generic"):
        spec = registry["specialties"][entry["specialty"]]
        return spec.get(f"generic_blurb_{lang}", spec.get("generic_blurb_en", ""))
    return entry.get(f"blurb_{lang}", entry.get("blurb_en", ""))


def _is_gi_procedure_addon(entry: dict, lang: str) -> bool:
    """True iff this add-on has a procedure_list_desc for the given lang
    (signals it should render as a list item, not a paragraph blurb)."""
    return bool(entry.get(f"procedure_list_desc_{lang}",
                           entry.get("procedure_list_desc_en", "")))


def compose_procedure_items(add_ons, lang, registry=None):
    """Bare <li> items for GI-procedure add-ons (those with procedure_list_desc).
    Returns joined string with no <ul> wrapper; empty string if none qualify."""
    reg = registry or load_registry()
    selected = set(add_ons)
    items = []
    for addon_id in reg["add_ons"]:
        if addon_id not in selected:
            continue
        entry = reg["add_ons"][addon_id]
        if not _is_gi_procedure_addon(entry, lang):
            continue
        frag = _frag(entry, lang)
        desc = entry.get(f"procedure_list_desc_{lang}",
                         entry.get("procedure_list_desc_en", ""))
        items.append(f"<li><strong>{frag}</strong> &mdash; {desc}</li>")
    return "\n".join(items)


def _addon_blocks(add_ons, knob_picks, lang, reg, *, exclude_gi_procedures=False):
    """Shared builder for add-on blurb + knob <p> blocks. With
    exclude_gi_procedures=True, add-ons that render as procedure list items
    (compose_procedure_items) are skipped — that's the 'team' blurb slot on
    templates which show GI procedures as a bulleted list instead. Single source
    so the full-blurbs and team-blurbs paths can never drift in markup."""
    selected = set(add_ons)
    blocks = []
    for addon_id in reg["add_ons"]:
        if addon_id not in selected:
            continue
        entry = reg["add_ons"][addon_id]
        if exclude_gi_procedures and _is_gi_procedure_addon(entry, lang):
            continue
        text = _addon_blurb(addon_id, entry, lang, reg)
        if text:
            blocks.append(f'<p class="addon-blurb">{text}</p>')
    for knob in resolve_knobs(add_ons, knob_picks, lang, reg):
        if knob["fragment"]:
            blocks.append(f'<p class="addon-knob">{knob["fragment"]}</p>')
    return "\n".join(blocks)


def compose_blurbs(add_ons, knob_picks, lang, registry=None):
    reg = registry or load_registry()
    return _addon_blocks(add_ons, knob_picks, lang, reg)


@dataclass
class Composition:
    title: str
    blurbs_html: str
    knob_values: dict = field(default_factory=dict)
    addon_title: str = ""
    procedure_items_html: str = ""
    team_blurbs_html: str = ""


def compose(base, add_ons, knob_picks, lang, registry=None):
    reg = registry or load_registry()
    title = compose_title(base, add_ons, lang, reg)
    blurbs = compose_blurbs(add_ons, knob_picks, lang, reg)
    knob_values = {k["name"]: k["value"] for k in resolve_knobs(add_ons, knob_picks, lang, reg)}
    addon_title = compose_addon_title(add_ons, lang, reg)
    procedure_items = compose_procedure_items(add_ons, lang, reg)
    # team_blurbs: same as blurbs but excludes GI-procedure add-ons (those render
    # as list items via compose_procedure_items). Shared builder — no duplication.
    team_blurbs = _addon_blocks(add_ons, knob_picks, lang, reg, exclude_gi_procedures=True)
    return Composition(
        title=title, blurbs_html=blurbs, knob_values=knob_values,
        addon_title=addon_title, procedure_items_html=procedure_items,
        team_blurbs_html=team_blurbs,
    )
