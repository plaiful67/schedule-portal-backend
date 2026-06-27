"""schedule.giready.com backend — FastAPI app.

Endpoints:
  POST /render        → personalized PDF (bowel_prep in Phase 1; other procedures in Phase 2)
  GET  /medications   → meds table for the frontend autocomplete (language-filtered)
  GET  /healthz       → 200 for liveness probes
"""
from __future__ import annotations

import json
import os
import sys
import time as _time
from datetime import datetime, time

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import TypeAdapter, ValidationError

from . import medications, physicians, tenant
from .adapters import bowel_prep, combined, composed, egd, egd_phmii
from .adapters.bowel_prep import ComposedTemplateUnsupported
from .adapters.composed import CompositionInputError
from .adapters._paths import skill_source
from .personalization import (
    build_followup_block,
    format_appt_date,
    format_time_12h,
)
from .schemas import RenderRequest

_RENDER_ADAPTER = TypeAdapter(RenderRequest)

app = FastAPI(title="schedule.giready.com", version="0.1.0")

# ALLOWED_ORIGINS is a comma-separated allowlist set at deploy time. Always
# include the two local dev origins so `make dev` keeps working.
_extra_origins = os.environ.get("ALLOWED_ORIGINS", "").split(",")
_extra_origins = [o.strip() for o in _extra_origins if o.strip()]
_LOCAL_DEV = ["http://localhost:5500", "http://127.0.0.1:5500",
              "http://localhost:5501", "http://127.0.0.1:5501"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_LOCAL_DEV + _extra_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    # `Content-Disposition` is not a CORS-safelisted response header, so the
    # browser hides it from JS unless we explicitly expose it. Without this,
    # `r.headers.get("Content-Disposition")` returns null on the frontend and
    # the descriptive filename is lost (regression → "unknown.pdf").
    expose_headers=["Content-Disposition"],
    allow_credentials=False,
)


# Filename tokens for the descriptive download name.
# Format: prep-{YYYY-MM-DD}-{PhysicianLast}-[{band}kg-]{Variant}-{Facility}.pdf
VARIANT_TOKEN = {
    "bowel_prep": "Colon",
    "combined": "EGDColon",
    "composed": "Composed",
    "egd": "EGD",
    "egd_phmii": "EGDpH",
    "flex_sig": "FlexSig",
}
FACILITY_TOKEN = {"scc": "SCC", "pmch": "PMCH"}


def _emit_event(payload: dict) -> None:
    """Write one JSON line to stdout. Cloud Run forwards it to Cloud Logging,
    which parses the line into `jsonPayload`. No PHI is ever included — only
    procedure metadata (procedure_type, location, physician, weight_band, etc.)."""
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


# Path is /__health (not /healthz or /health) because Cloud Run's load
# balancer reserves /healthz and /health at the GFE layer — they 404 before
# the request reaches the container.
@app.get("/__health")
def healthz():
    return {
        "ok": True,
        "skills": {
            "bowel-prep-generator":   skill_source("bowel-prep-generator"),
            "egd-handout-generator":  skill_source("egd-handout-generator"),
            "flex-sig-handout-generator": skill_source("flex-sig-handout-generator"),
        },
    }


@app.get("/medications")
def get_medications(lang: str = "en"):
    if lang not in ("en", "es"):
        raise HTTPException(status_code=400, detail="lang must be 'en' or 'es'")
    return medications.for_language(lang)


@app.get("/config")
def get_config(
    jwt_tenant: str = Depends(tenant.get_tenant),
    tenant_q: str | None = Query(default=None, alias="tenant"),
):
    """Tenant config for the frontend control panel: doctors, procedures, bands,
    prep_types, locations. The tenant is resolved server-side from the Access
    JWT (get_tenant dependency). `?tenant=<id>` is honored for any KNOWN tenant
    — /config is read-only, tenant-scoped, carries no PHI and no patient data,
    so letting the demo frontend select its tenant for display is safe. The
    JWT-derived tenant is the default when no (valid) override is given. The
    write path (/render) does NOT trust a header/query tenant unless the
    PORTAL_ALLOW_HEADER_TENANT dev opt-in is set."""
    tenant_id = jwt_tenant
    requested = (tenant_q or "").strip()
    if requested and requested in tenant.known_tenants():
        tenant_id = requested
    return tenant.config(tenant_id)


# Map a scheduler request to its content-status unit (mirrors the site-family
# units). The scheduler is a publish path too, so a tenant must have SIGNED OFF
# on the unit before the backend renders a personalized PDF for it. prep_type
# (lactulose/clenpiq/suprep) selects a distinct content family — the same way
# the hidden site variants are distinct units.
_BASE_CONTENT_UNIT = {
    "bowel_prep": "colonoscopy",
    "combined": "combined",
    "egd": "egd",
    "egd_phmii": "egd_phmii",
    "flex_sig": "flex_sig",
    "composed": "combined",
}


def _content_unit_for(req) -> str:
    """The content unit a render request publishes, accounting for prep_type
    (lactulose/clenpiq/suprep are distinct units, like the hidden site variants
    — e.g. bowel_prep+lactulose → 'lactulose', combined+suprep →
    'suprep-combined')."""
    base = _BASE_CONTENT_UNIT.get(req.procedure_type, req.procedure_type)
    prep = getattr(req, "prep_type", "miralax")
    if base in ("colonoscopy", "combined") and prep in ("lactulose", "clenpiq", "suprep"):
        return prep if base == "colonoscopy" else f"{prep}-combined"
    return base


@app.get("/content/status")
def content_status_endpoint(tenant_id: str = Depends(tenant.get_tenant)):
    """The tenant's content-ownership ledger: per content unit, its effective
    state (with the approved_sha auto-revert applied). Read-only."""
    cs = tenant._content_status()
    status = cs.load_status(tenant_id).get("content_status", {}) or {}
    units = status.get("units", {}) or {}
    return {
        "tenant": tenant_id,
        "content_sha": cs.content_sha(tenant_id),
        "units": {u: cs.unit_status(tenant_id, u) for u in units},
        "publishable": cs.approved_units(tenant_id),
    }


@app.post("/content/approve")
def content_approve(
    unit: str = Query(...),
    approved_by: str = Query(...),
    tenant_id: str = Depends(tenant.get_tenant),
    role: str = Depends(tenant.get_signer_role),
):
    """Sign off on a content unit. ROLE GATE: only a clinical_signer (from the
    Access JWT group) may approve — a platform_operator gets 403. Binds the
    approval to the current content sha (auto-reverts on later edits)."""
    cs = tenant._content_status()
    try:
        rec = cs.approve(tenant_id, unit, approved_by=approved_by, role=role)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"tenant": tenant_id, "unit": unit, **rec}


@app.post("/render")
async def render(request: Request, tenant_id: str = Depends(tenant.get_tenant)):
    _t0 = _time.monotonic()
    # Parse the body WITH the resolved-tenant validation context so the schema's
    # identity-membership validator checks physician_id/location_id against THIS
    # tenant's roster/locations. tenant_id comes from the Access JWT dependency,
    # never from the body. A body that carries its own `tenant`/`tenant_id` key
    # is ignored (we strip it before validation).
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if isinstance(body, dict):
        body.pop("tenant", None)
        body.pop("tenant_id", None)
    ctx = {
        "physician_ids": tenant.physician_ids(tenant_id),
        "location_ids": tenant.location_ids(tenant_id),
    }
    try:
        req = _RENDER_ADAPTER.validate_python(body, context=ctx)
    except ValidationError as e:
        # e.errors() can carry the raised exception object in `ctx`, which isn't
        # JSON-serializable; emit a clean, string-only detail (FastAPI-default
        # 422 shape) so the client gets a 422, not a serialization 500.
        detail = [
            {"type": err.get("type"), "loc": list(err.get("loc", [])),
             "msg": str(err.get("msg", ""))}
            for err in e.errors()
        ]
        raise HTTPException(status_code=422, detail=detail)
    # Content-ownership gate: the scheduler is a publish path too. Refuse a PDF
    # for a content unit the tenant hasn't signed off on (approved + sha
    # matches). giready's units are all approved, so its path is unaffected.
    _unit = _content_unit_for(req)
    _cs = tenant._content_status()
    if not _cs.is_publishable(tenant_id, _unit):
        raise HTTPException(
            status_code=403,
            detail=f"content unit {_unit!r} is not approved for tenant {tenant_id!r} "
                   f"— a clinical_signer must sign off before it can be served.")

    # Make the resolved tenant the active one so the adapters' footer/roster
    # lookups (physicians.footer_line / lookup, called without a tenant arg)
    # resolve against THIS tenant. Defaults back to giready between requests.
    physicians.set_active_tenant(tenant_id)
    _event = {
        "evt": "render",
        "tenant_id": tenant_id,
        "procedure_type": req.procedure_type,
        "location_id": req.location_id,
        "physician_id": req.physician_id,
        "language": req.language,
        "weight_band": getattr(req, "weight_band", None),
        "prep_type": getattr(req, "prep_type", None),
        "include_directions": req.include_directions,
        "has_followup": bool(req.followup_date and req.followup_time),
        "appointment_date": req.appointment_date.isoformat(),
        # True when the request routed through a partner-specific MiraLAX
        # variant (per-physician dose overrides). False for canonical doses
        # and for procedure types that don't carry a weight_band + prep_type.
        # Lets analytics.giready.com query per-partner adoption without a
        # schema change. See bowel_prep.PARTNER_OVERRIDE_PHYSICIANS.
        "physician_variant_active": bowel_prep.is_partner_variant_active(
            req.physician_id,
            getattr(req, "weight_band", None),
            getattr(req, "prep_type", "miralax"),
        ),
    }
    try:
        # Validate every stop_meds id exists.
        for med_id in req.stop_meds:
            if not medications.lookup(med_id):
                raise HTTPException(status_code=422, detail=f"unknown med id: {med_id!r}")
        result = _render_impl(req)
        _event.update({
            "status": "ok",
            "render_ms": int((_time.monotonic() - _t0) * 1000),
        })
        _emit_event(_event)
        return result
    except HTTPException as e:
        _event.update({
            "status": "error",
            "error_class": "HTTPException",
            "http_status": e.status_code,
            "render_ms": int((_time.monotonic() - _t0) * 1000),
        })
        _emit_event(_event)
        raise
    except Exception as e:
        _event.update({
            "status": "error",
            "error_class": type(e).__name__,
            "render_ms": int((_time.monotonic() - _t0) * 1000),
        })
        _emit_event(_event)
        raise


def _render_impl(req: RenderRequest):

    appt_date_human = format_appt_date(req.appointment_date, req.language)
    appt_time = format_time_12h(req.appointment_time)
    arrival_time = format_time_12h(req.arrival_time)

    # Combined datetime drives both the cover-row mobile URL (#d=&t= hash)
    # and the pz-only span substitutions (rescue-cutoff clock time, etc.).
    appt_dt = datetime.combine(
        req.appointment_date, time.fromisoformat(req.appointment_time)
    )

    followup_block_html = build_followup_block(
        req.followup_date, req.followup_time, req.language
    )

    common = dict(
        location_id=req.location_id,
        lang=req.language,
        physician_id=req.physician_id,
        appt_date_human=appt_date_human,
        appt_time_display=appt_time,
        arrival_time_display=arrival_time,
        followup_block_html=followup_block_html,
        appt_dt=appt_dt,
        # Directions are now inlined as a tagged section of the handout render
        # (not appended as a separate untagged PDF) — the adapter handles it.
        include_directions=req.include_directions,
    )

    if req.procedure_type == "bowel_prep":
        pdf_bytes = bowel_prep.render_pdf(
            band_id=req.weight_band, prep_type=req.prep_type, **common
        )
    elif req.procedure_type == "combined":
        pdf_bytes = combined.render_pdf(
            band_id=req.weight_band, prep_type=req.prep_type, **common
        )
    elif req.procedure_type == "egd":
        pdf_bytes = egd.render_pdf(**common)
    elif req.procedure_type == "egd_phmii":
        pdf_bytes = egd_phmii.render_pdf(**common)
    elif req.procedure_type == "composed":
        try:
            pdf_bytes = composed.render_pdf(
                add_ons=req.add_ons, knob_picks=req.knob_picks,
                base=req.base, weight_band=req.weight_band, prep_type=req.prep_type,
                **common,
            )
        except (ComposedTemplateUnsupported, CompositionInputError) as e:
            raise HTTPException(status_code=422, detail=str(e))
    else:
        raise HTTPException(
            status_code=501,
            detail=f"procedure_type={req.procedure_type!r} not yet implemented (Phase 2)",
        )


    # Patient-facing download filename — descriptive so a printed stack can be
    # sorted by date / physician / procedure without opening each PDF.
    # Format: prep-{YYYY-MM-DD}-{PhysicianLast}-[{band}kg-]{Variant}-{Facility}.pdf
    # Example: prep-2026-05-27-Zavoian-31-40kg-EGDColon-SCC.pdf
    # EGD requests have no weight_band; all others do. PhysicianId slugs already
    # mirror lowercase last names (see schemas.PhysicianId), so .title() is enough.
    weight_band = getattr(req, "weight_band", None)
    parts = ["prep", req.appointment_date.isoformat(), req.physician_id.title()]
    if weight_band:
        parts.append(f"{weight_band}kg")
    parts.append(VARIANT_TOKEN[req.procedure_type])
    parts.append(FACILITY_TOKEN[req.location_id])
    if req.language == "es":
        parts.append("ES")
    filename = "-".join(parts) + ".pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
