"""Performing-physician lookup.

Single source of truth lives in the bowel-prep skill's practice.yaml under
`practice.doctors[]` — each entry has `id`, `name_short`, `profile_url`. The
EGD and flex-sig skills' practice.yaml files don't carry a doctors list
today; this module always reads the bowel-prep one regardless of which
adapter calls in, so the group's roster stays in one place.

Loaded once at import time. Restart the backend after adding/removing a
physician (the slug Literal in app/schemas.py also has to be updated).
"""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import yaml

from .adapters._paths import skill_dir


class Physician(TypedDict):
    id: str
    name_short: str
    profile_url: str


def _load() -> dict[str, Physician]:
    practice_path: Path = skill_dir("bowel-prep-generator") / "practice.yaml"
    with open(practice_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    doctors = data.get("practice", {}).get("doctors", [])
    out: dict[str, Physician] = {}
    for d in doctors:
        slug = d.get("id")
        if not slug:
            raise ValueError(
                f"practice.doctors entry missing `id` field: {d!r}. "
                "Every doctor must have an id slug that mirrors PhysicianId in schemas.py."
            )
        out[slug] = {
            "id": slug,
            "name_short": d["name_short"],
            "profile_url": d.get("profile_url", ""),
        }
    return out


_BY_ID: dict[str, Physician] = _load()


def lookup(physician_id: str) -> Physician:
    """Return the physician record for a slug. Raises if unknown."""
    p = _BY_ID.get(physician_id)
    if p is None:
        raise KeyError(f"Unknown physician_id={physician_id!r}; known: {sorted(_BY_ID)}")
    return p


def footer_line(physician_id: str, lang: str, phone: str = "(317) 338-9450") -> str:
    """Build the per-physician {{PRACTICE_FOOTER}} replacement.

    Format: `Dr. X  ·  Pediatric Gastroenterology  ·  (317) 338-9450`
    (Spanish: `Gastroenterología Pediátrica`.) The middle-dot separator
    matches the existing group-footer formatting in practice.yaml.
    """
    name = lookup(physician_id)["name_short"]
    specialty = "Gastroenterología Pediátrica" if lang == "es" else "Pediatric Gastroenterology"
    return f"{name}  ·  {specialty}  ·  {phone}"
