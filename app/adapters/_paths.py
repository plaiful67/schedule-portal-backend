"""Resolve which copy of a vendored skill the adapter should use.

For **local development** the backend reads directly from
`~/.claude/skills/<skill>/` so Sebastian's edits to the production skills
land in the next render without a `make vendor-sync`. For **production**
(Cloud Run, where the home dir doesn't have those skills), the adapter
falls back to the baked-in `vendor/` copy from the Docker image.

The choice is decided once at import time. Override with the env var
`PORTAL_SKILL_SOURCE=vendor` to force the production path locally
(useful for smoke-testing what the deployed container will do).
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
HOME_SKILLS = Path.home() / ".claude" / "skills"
HOME_SHARED = Path.home() / "peds-gi-prep-system" / "shared"
VENDOR_DIR = BACKEND_DIR / "vendor"

_FORCE = os.environ.get("PORTAL_SKILL_SOURCE", "").strip().lower()


def skill_dir(name: str) -> Path:
    """Return the directory to load skill `name` from."""
    if _FORCE == "vendor":
        return VENDOR_DIR / name
    live = HOME_SKILLS / name
    if live.exists():
        return live
    return VENDOR_DIR / name


def shared_dir() -> Path:
    """Return the meta-repo `shared/` dir (cross-skill partials + CSS/JS).
    Mirrors skill_dir's live-vs-vendor choice so shared partials resolve on
    Cloud Run (vendor/shared) and in local dev (~/peds-gi-prep-system/shared)."""
    if _FORCE == "vendor":
        return VENDOR_DIR / "shared"
    if HOME_SHARED.exists():
        return HOME_SHARED
    return VENDOR_DIR / "shared"


def skill_source(name: str) -> str:
    """Returns "live" or "vendor" — used in /healthz for observability."""
    return "live" if skill_dir(name) == HOME_SKILLS / name else "vendor"


def is_live_dev(name: str = "egd-handout-generator") -> bool:
    """True when the adapter is reading a skill from the live ~/.claude/skills
    tree (local dev) rather than the baked-in vendor/ copy (Cloud Run). Used to
    gate the per-request cache resets, which only earn their I/O cost in dev —
    in production the skill source is immutable, so re-reading every render is
    pure waste."""
    return skill_source(name) == "live"


_COMPOSE_MODULE = None


def load_compose_module():
    """Load the EGD skill's compose.py resolver once, under a stable module
    name. Shared by the egd and composed adapters so the importlib dance isn't
    duplicated."""
    global _COMPOSE_MODULE
    if _COMPOSE_MODULE is not None:
        return _COMPOSE_MODULE
    import importlib.util
    import sys

    name = "_composed_resolver"
    if name in sys.modules:
        _COMPOSE_MODULE = sys.modules[name]
        return _COMPOSE_MODULE
    path = skill_dir("egd-handout-generator") / "scripts" / "compose.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load compose module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _COMPOSE_MODULE = module
    return module
