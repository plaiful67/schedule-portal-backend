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


def compose_blurbs(add_ons, knob_picks, lang, registry=None):
    reg = registry or load_registry()
    selected = set(add_ons)
    blocks = []
    for addon_id in reg["add_ons"]:
        if addon_id not in selected:
            continue
        text = _addon_blurb(addon_id, reg["add_ons"][addon_id], lang, reg)
        if text:
            blocks.append(f'<p class="addon-blurb">{text}</p>')
    for knob in resolve_knobs(add_ons, knob_picks, lang, reg):
        if knob["fragment"]:
            blocks.append(f'<p class="addon-knob">{knob["fragment"]}</p>')
    return "\n".join(blocks)


@dataclass
class Composition:
    title: str
    blurbs_html: str
    knob_values: dict = field(default_factory=dict)
    addon_title: str = ""


def compose(base, add_ons, knob_picks, lang, registry=None):
    reg = registry or load_registry()
    title = compose_title(base, add_ons, lang, reg)
    blurbs = compose_blurbs(add_ons, knob_picks, lang, reg)
    knob_values = {k["name"]: k["value"] for k in resolve_knobs(add_ons, knob_picks, lang, reg)}
    addon_title = compose_addon_title(add_ons, lang, reg)
    return Composition(title=title, blurbs_html=blurbs, knob_values=knob_values, addon_title=addon_title)
