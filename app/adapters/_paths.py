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


def skill_source(name: str) -> str:
    """Returns "live" or "vendor" — used in /healthz for observability."""
    return "live" if skill_dir(name) == HOME_SKILLS / name else "vendor"
