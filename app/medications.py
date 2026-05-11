"""Loader + helpers for data/medications.yaml.

Single source of truth for the pre-procedure hold list. The frontend
autocomplete and the backend's STOP_MEDS_BLOCK renderer both read this file.

The schema is English-only by design — see data/medications.yaml header.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "medications.yaml"


@cache
def load() -> dict[str, Any]:
    with open(DATA_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@cache
def by_id() -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in load()["meds"]}


@cache
def categories() -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in load()["categories"]}


def lookup(med_id: str) -> dict[str, Any] | None:
    return by_id().get(med_id)


def for_language(lang: str) -> dict[str, Any]:
    """Return the table for the frontend. The `lang` arg is kept for
    forward-compat but currently always returns English (see header)."""
    _ = lang
    return load()
