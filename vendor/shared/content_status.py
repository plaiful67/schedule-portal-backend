"""Content-ownership / liability seam (plan Layer 4).

The platform is a CMS, not a clinical authority: each practice (tenant) OWNS and
clinically SIGNS OFF on its own content. This module enforces that structurally:

  - Per tenant, each content unit (a site family — colonoscopy, combined, …)
    carries a status: ``{state: draft|approved, approved_by, approved_sha,
    approved_at}``, stored in ``tenants/<id>/content_status.yaml`` (git-tracked —
    the git history IS the audit trail of who signed off on which version).

  - **Publish refuses anything not ``approved``.** ``is_publishable(tenant, unit)``
    returns False for a draft unit; the build skips it (draft renders only to a
    watermarked, noindex preview, never the tenant's real apex).

  - **Approval binds to the git sha of the content the practice authors** — its
    ``tenant.yaml`` (the bytes that carry its prose/branding/contact info). If
    ``tenant.yaml`` changes after approval, the recorded ``approved_sha`` no
    longer matches the current sha → the unit AUTO-REVERTS to draft and cannot
    publish until re-signed. That is the structural "they signed off on the EXACT
    bytes that ship" guarantee.

  - **Role separation:** only a member holding the ``clinical_signer`` role
    (sourced from the Access JWT group, with a dev/env fallback) may move a unit
    to ``approved``. A platform operator can edit layout/structure but CANNOT
    approve clinical content. ``approve()`` enforces this — it's a check, not a
    convention.

Stdlib + PyYAML only (imported into the skill venvs). The git-sha read shells out
to ``git`` (already required across the system); when git is unavailable it falls
back to a content hash so the seam still functions (documented).
"""
from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TENANT = "giready"
CLINICAL_SIGNER_ROLE = "clinical_signer"
PLATFORM_OPERATOR_ROLE = "platform_operator"


def _tenants_dir() -> Path | None:
    """tenants/ dir (sibling of this shared/ dir, or the home meta-repo)."""
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "tenants",
                 Path.home() / "peds-gi-prep-system" / "tenants"):
        if cand.is_dir():
            return cand
    return None


def tenant_dir(tenant_id: str) -> Path | None:
    d = _tenants_dir()
    return (d / tenant_id) if d else None


def _tenant_yaml_path(tenant_id: str) -> Path | None:
    td = tenant_dir(tenant_id)
    return (td / "tenant.yaml") if td else None


def _status_path(tenant_id: str) -> Path | None:
    td = tenant_dir(tenant_id)
    return (td / "content_status.yaml") if td else None


def content_sha(tenant_id: str) -> str:
    """Git blob sha of the tenant's content file (tenant.yaml) — the bytes the
    practice authors + signs off on. Approval binds to THIS. Falls back to a
    sha256 of the file bytes if git is unavailable (so the seam still works)."""
    p = _tenant_yaml_path(tenant_id)
    if not p or not p.exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "hash-object", str(p)],
            cwd=str(p.parent), capture_output=True, text=True, check=True,
        )
        sha = out.stdout.strip()
        if sha:
            return sha
    except Exception:
        pass
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def load_status(tenant_id: str) -> dict:
    """The tenant's content_status.yaml as a dict ({} if absent)."""
    p = _status_path(tenant_id)
    if not p or not p.exists():
        return {}
    import yaml
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_status(tenant_id: str, data: dict) -> Path:
    p = _status_path(tenant_id)
    if not p:
        raise RuntimeError(f"no tenants dir for {tenant_id!r}")
    p.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    return p


def unit_status(tenant_id: str, unit: str) -> dict:
    """Resolved status for one content unit, with the sha-mismatch auto-revert
    applied: if the recorded approved_sha != the current content sha, the
    EFFECTIVE state is 'draft' (the content changed after sign-off)."""
    units = (load_status(tenant_id).get("content_status", {}) or {}).get("units", {}) or {}
    rec = dict(units.get(unit, {}) or {})
    state = rec.get("state", "draft")
    if state == "approved":
        if rec.get("approved_sha") != content_sha(tenant_id):
            # Content changed since sign-off → auto-revert (not persisted; the
            # effective state is draft until re-signed).
            rec["state"] = "draft"
            rec["auto_reverted"] = True
    else:
        rec["state"] = "draft"
    return rec


def is_publishable(tenant_id: str, unit: str) -> bool:
    """True iff the unit is approved AND its approval still matches the current
    content sha. This is the publish gate the build calls per content unit."""
    return unit_status(tenant_id, unit).get("state") == "approved"


def approve(tenant_id: str, unit: str, *, approved_by: str, role: str) -> dict:
    """Move a unit to 'approved', binding it to the current content sha. ROLE
    GATE: only a CLINICAL_SIGNER may approve — raises PermissionError otherwise
    (a platform operator can edit layout but cannot sign clinical content).
    Persists to content_status.yaml. Returns the new record."""
    if role != CLINICAL_SIGNER_ROLE:
        raise PermissionError(
            f"role {role!r} cannot approve clinical content; "
            f"only {CLINICAL_SIGNER_ROLE!r} may sign off (the practice owns its content)."
        )
    data = load_status(tenant_id)
    cs = data.setdefault("content_status", {})
    units = cs.setdefault("units", {})
    rec = {
        "state": "approved",
        "approved_by": approved_by,
        "approved_sha": content_sha(tenant_id),
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    units[unit] = rec
    _save_status(tenant_id, data)
    return rec


def approved_units(tenant_id: str) -> list[str]:
    """Units that are currently publishable (approved + sha matches)."""
    units = (load_status(tenant_id).get("content_status", {}) or {}).get("units", {}) or {}
    return sorted(u for u in units if is_publishable(tenant_id, u))
