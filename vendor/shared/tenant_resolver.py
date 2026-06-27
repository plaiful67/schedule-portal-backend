"""Single seam for resolving a per-tenant config overlay.

A "tenant" is the top layer of the practice-config deep-merge stack:

    resolved = deep_merge(practice-core.yaml, skill practice.yaml, tenant.yaml)

This module owns ONLY "where tenant records live + how to load one." Today that
is a YAML file per tenant at ``<root>/tenants/<id>/tenant.yaml``. The whole point
of funnelling every reader through ``resolve()`` is that graduating the store to
D1/Postgres later is a one-function change here — nothing downstream moves,
because callers already consume a plain dict and do their own deep-merge.

Resolution mirrors the shared/ resolver in each skill's render.py: prefer a
vendored copy (the backend's Cloud Run image carries vendor/shared + vendor/
tenants), then the local meta-repo checkout. Stdlib + PyYAML only; every venv
that imports this already pins PyYAML.
"""

from pathlib import Path

DEFAULT_TENANT = "giready"


def tenants_dir():
    """Return the tenants/ directory, or None if absent.

    This file lives in ``<root>/shared/``; tenants/ is its sibling
    (``<root>/tenants/``). Works for both the meta-repo checkout and the
    vendored copy (vendor/shared + vendor/tenants share a parent)."""
    here = Path(__file__).resolve().parent              # .../shared
    for cand in (here.parent / "tenants",               # sibling of shared/
                 Path.home() / "peds-gi-prep-system" / "tenants"):
        if cand.is_dir():
            return cand
    return None


def tenant_path(tenant_id):
    """Path to a tenant's tenant.yaml (may not exist)."""
    d = tenants_dir()
    return (d / tenant_id / "tenant.yaml") if d else None


def resolve(tenant_id=DEFAULT_TENANT):
    """Return the raw per-tenant overlay dict for ``tenant_id``.

    Returns ``{}`` when the tenant store or record is absent, so callers stay
    inert (byte-identical to pre-tenant behaviour) until a record exists. The
    caller deep-merges this OVER its already-merged practice config."""
    p = tenant_path(tenant_id)
    if not p or not p.exists():
        return {}
    import yaml
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def list_tenants():
    """Return sorted tenant ids that have a tenant.yaml (for the builder)."""
    d = tenants_dir()
    if not d:
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_dir() and (p / "tenant.yaml").exists())
