"""Tenant resolution + the FastAPI tenant dependency for the scheduler backend.

A "tenant" is the same top-of-stack practice overlay the skills use
(tenants/<id>/tenant.yaml, resolved by shared/tenant_resolver.py). This module
is the backend's single seam onto it:

  - resolve_overlay(tenant_id)  -> the raw tenant.yaml overlay dict
  - roster(tenant_id)           -> {slug: physician record} for that tenant
  - locations(tenant_id)        -> {loc_id: location block} for that tenant
  - config(tenant_id)           -> the GET /config payload
  - get_tenant (FastAPI dep)    -> tenant id from the Cloudflare Access JWT,
                                   with an X-Tenant / env dev fallback.

SECURITY: the tenant id is NEVER read from the request body — it is derived
server-side from the Access identity (or the dev fallback). Retrofitting that
later would be a security rewrite, so it's paid here. See plan §"Layer 3".

The roster/locations fall back to the bowel-prep skill's practice.yaml +
dosing.yaml (today's giready source of truth) when the overlay doesn't carry
them, so the giready tenant resolves to exactly today's data.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import Header

from .adapters._paths import shared_dir, skill_dir

DEFAULT_TENANT = "giready"


def _resolver():
    """Import the vendored/local tenant_resolver via the shared dir (mirrors the
    skills' resolution: vendor/shared first, then the meta-repo checkout)."""
    sd = str(shared_dir())
    if sd not in sys.path:
        sys.path.insert(0, sd)
    import tenant_resolver  # noqa: PLC0415
    return tenant_resolver


def resolve_overlay(tenant_id: str) -> dict:
    """Raw tenant.yaml overlay dict ({} if absent)."""
    try:
        return _resolver().resolve(tenant_id) or {}
    except Exception:
        return {}


def known_tenants() -> list[str]:
    try:
        ids = _resolver().list_tenants()
    except Exception:
        ids = []
    return ids or [DEFAULT_TENANT]


def _bowel_prep_practice() -> dict:
    """The bowel-prep skill's practice.yaml (giready roster source of truth)."""
    p = skill_dir("bowel-prep-generator") / "practice.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _bowel_prep_dosing() -> dict:
    p = skill_dir("bowel-prep-generator") / "data" / "dosing.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=None)
def roster(tenant_id: str = DEFAULT_TENANT) -> dict[str, dict]:
    """{slug: {id, name_short, profile_url}} for the tenant.

    Prefers the tenant overlay's practice.doctors[]; falls back to the
    bowel-prep practice.yaml (giready). Cached per tenant."""
    overlay = resolve_overlay(tenant_id)
    doctors = (overlay.get("practice", {}) or {}).get("doctors")
    if not doctors:
        doctors = _bowel_prep_practice().get("practice", {}).get("doctors", [])
    out: dict[str, dict] = {}
    for d in doctors:
        slug = d.get("id")
        if not slug:
            raise ValueError(f"doctor entry missing `id`: {d!r}")
        out[slug] = {
            "id": slug,
            "name_short": d["name_short"],
            "profile_url": d.get("profile_url", ""),
        }
    return out


@lru_cache(maxsize=None)
def locations(tenant_id: str = DEFAULT_TENANT) -> dict[str, dict]:
    """{loc_id: location block} for the tenant: dosing.yaml locations with the
    tenant overlay's `locations` merged on top (mirrors render.main)."""
    base = dict(_bowel_prep_dosing().get("locations", {}))
    overlay_locs = resolve_overlay(tenant_id).get("locations") or {}
    for lid, block in overlay_locs.items():
        if isinstance(base.get(lid), dict) and isinstance(block, dict):
            merged = dict(base[lid])
            merged.update(block)
            base[lid] = merged
        else:
            base[lid] = block
    return base


# Clinical sets are SHARED across tenants (the demo reuses giready's dose math
# in the prototype). These are the canonical band/prep-type vocabularies.
BOWEL_PREP_BANDS = ["under-15", "under-15-enema", "15-20", "21-30", "31-40", "41-50", "over-50"]
FLEX_SIG_BANDS = ["under-15kg", "20-40kg", "over-40kg"]
PREP_TYPES = ["miralax", "lactulose", "clenpiq", "suprep"]
PROCEDURES = ["bowel_prep", "combined", "egd", "egd_phmii", "flex_sig", "composed"]


def config(tenant_id: str = DEFAULT_TENANT) -> dict:
    """The GET /config payload: everything the frontend control panel needs to
    render itself for this tenant without hardcoded arrays."""
    r = roster(tenant_id)
    overlay = resolve_overlay(tenant_id)
    tcfg = overlay.get("tenant", {}) or {}
    locs = locations(tenant_id)
    return {
        "tenant": tenant_id,
        "display_name": tcfg.get("display_name", tenant_id),
        "doctors": [r[s] for s in r],
        "procedures": PROCEDURES,
        "bands": BOWEL_PREP_BANDS,
        "flex_sig_bands": FLEX_SIG_BANDS,
        "prep_types": PREP_TYPES,
        "locations": [
            {
                "id": lid,
                "name_en": lb.get("name_en", lid),
                "name_es": lb.get("name_es", lb.get("name_en", lid)),
            }
            for lid, lb in locs.items()
        ],
    }


def physician_ids(tenant_id: str = DEFAULT_TENANT) -> set[str]:
    return set(roster(tenant_id).keys())


def location_ids(tenant_id: str = DEFAULT_TENANT) -> set[str]:
    return set(locations(tenant_id).keys())


# --- FastAPI tenant dependency --------------------------------------------

def _tenant_from_access_jwt(assertion: str | None) -> str | None:
    """Best-effort tenant id from a Cloudflare Access JWT. We do NOT verify the
    signature here (CF Access already verified it at the edge before forwarding
    Cf-Access-Jwt-Assertion); we only read a tenant claim from the payload. A
    real multi-tenant deploy maps the Access application / identity → tenant;
    for the prototype we read a `custom.tenant` (or `tenant`) claim if present."""
    if not assertion:
        return None
    try:
        parts = assertion.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        custom = payload.get("custom", {}) or {}
        return custom.get("tenant") or payload.get("tenant")
    except Exception:
        return None


CLINICAL_SIGNER_ROLE = "clinical_signer"
PLATFORM_OPERATOR_ROLE = "platform_operator"


def _roles_from_access_jwt(assertion: str | None) -> set[str]:
    """Roles/groups from the Access JWT. CF Access puts the identity's group
    memberships in the token; we read `custom.groups` / `groups` (a list) and
    `custom.role` / `role` (a scalar). Signature already verified at the edge."""
    if not assertion:
        return set()
    try:
        parts = assertion.split(".")
        if len(parts) != 3:
            return set()
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        custom = payload.get("custom", {}) or {}
        roles: set[str] = set()
        for src in (custom.get("groups"), payload.get("groups")):
            if isinstance(src, list):
                roles.update(str(g) for g in src)
        for src in (custom.get("role"), payload.get("role")):
            if isinstance(src, str):
                roles.add(src)
        return roles
    except Exception:
        return set()


def get_signer_role(
    cf_access_jwt_assertion: str | None = Header(default=None),
    x_signer_role: str | None = Header(default=None),
) -> str:
    """The caller's content-signing role, SERVER-SIDE only:
      1. clinical_signer if the Access JWT groups include it, else
      2. the X-Signer-Role header (dev/preview) ONLY when the env opt-in is set,
      3. else platform_operator (the default — CAN edit layout, CANNOT sign).
    Approval endpoints depend on this so the role gate is an enforced check."""
    roles = _roles_from_access_jwt(cf_access_jwt_assertion)
    if CLINICAL_SIGNER_ROLE in roles:
        return CLINICAL_SIGNER_ROLE
    if x_signer_role and os.environ.get("PORTAL_ALLOW_HEADER_TENANT", "").strip() == "1":
        return x_signer_role.strip()
    return PLATFORM_OPERATOR_ROLE


def _content_status():
    """Import the shared content_status module via the shared dir."""
    sd = str(shared_dir())
    if sd not in sys.path:
        sys.path.insert(0, sd)
    import content_status  # noqa: PLC0415
    return content_status


def get_tenant(
    cf_access_jwt_assertion: str | None = Header(default=None),
    x_tenant: str | None = Header(default=None),
) -> str:
    """Resolve the active tenant id, server-side only:
      1. the Cloudflare Access JWT tenant claim (production), else
      2. the X-Tenant header (dev/preview convenience), else
      3. the PORTAL_DEFAULT_TENANT env (so `make dev` works), else
      4. 'giready'.
    NEVER reads tenant from the request body."""
    tid = _tenant_from_access_jwt(cf_access_jwt_assertion)
    if not tid:
        # X-Tenant is only honored when an env opt-in is set, so production
        # (where the env is unset) can't be spoofed via a plain header.
        if x_tenant and os.environ.get("PORTAL_ALLOW_HEADER_TENANT", "").strip() == "1":
            tid = x_tenant.strip()
    if not tid:
        tid = os.environ.get("PORTAL_DEFAULT_TENANT", DEFAULT_TENANT).strip()
    # Only resolve to a known tenant; unknown ids fall back to the default so a
    # bad claim can never select a non-existent (or someone else's) tenant.
    return tid if tid in known_tenants() else DEFAULT_TENANT
