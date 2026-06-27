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

import contextvars
from typing import TypedDict


# Active tenant for the current request. Set per-request by main.render via the
# tenant dependency (a contextvar so concurrent requests stay isolated under
# the async server). Defaults to "giready" so the giready render path — and any
# adapter calling lookup()/footer_line() without a tenant arg — is unchanged.
_ACTIVE_TENANT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "active_tenant", default="giready")


def set_active_tenant(tenant_id: str) -> None:
    _ACTIVE_TENANT.set(tenant_id or "giready")


def _active_tenant() -> str:
    return _ACTIVE_TENANT.get()


class Physician(TypedDict):
    id: str
    name_short: str
    profile_url: str


def _load(tenant_id: str = "giready") -> dict[str, Physician]:
    """Roster for a tenant. Delegates to app.tenant.roster (the single tenant
    seam) — which prefers the tenant overlay's practice.doctors[] and falls back
    to the bowel-prep practice.yaml (giready). Cached per tenant in app.tenant."""
    from .tenant import roster
    return roster(tenant_id)  # type: ignore[return-value]


# giready default roster kept for existing adapter callers (lookup/footer_line
# without a tenant arg). Per-tenant lookups pass tenant_id explicitly.
_BY_ID: dict[str, Physician] = _load()


def lookup(physician_id: str, tenant_id: str | None = None) -> Physician:
    """Return the physician record for a slug in a tenant (default: the active
    request tenant, else giready). Raises if unknown."""
    tid = tenant_id or _active_tenant()
    by_id = _load(tid)
    p = by_id.get(physician_id)
    if p is None:
        raise KeyError(f"Unknown physician_id={physician_id!r}; known: {sorted(by_id)}")
    return p


def footer_line(physician_id: str, lang: str, phone: str = "(317) 338-9450",
                tenant_id: str | None = None) -> str:
    """Build the per-physician {{PRACTICE_FOOTER}} replacement.

    Format: `Dr. X  ·  Pediatric Gastroenterology  ·  (317) 338-9450`
    (Spanish: `Gastroenterología Pediátrica`.) The middle-dot separator
    matches the existing group-footer formatting in practice.yaml. Resolves
    the roster against the active request tenant unless tenant_id is given.
    """
    name = lookup(physician_id, tenant_id)["name_short"]
    specialty = "Gastroenterología Pediátrica" if lang == "es" else "Pediatric Gastroenterology"
    return f"{name}  ·  {specialty}  ·  {phone}"
