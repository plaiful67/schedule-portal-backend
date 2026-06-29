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

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from . import medications
from .adapters import bowel_prep, combined, composed, egd, egd_phmii, flex_sig
from .adapters.bowel_prep import ComposedTemplateUnsupported
from .adapters.composed import CompositionInputError
from .adapters._paths import skill_source
from .personalization import (
    build_followup_block,
    format_appt_date,
    format_time_12h,
)
from .schemas import RenderRequest

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


@app.post("/render")
def render(req: RenderRequest):
    _t0 = _time.monotonic()
    _event = {
        "evt": "render",
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
    elif req.procedure_type == "flex_sig":
        pdf_bytes = flex_sig.render_pdf(
            weight_band=req.weight_band, prep_type=req.prep_type,
            include_egd=req.include_egd, **common
        )
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
    # EGD + flexible sigmoidoscopy combined gets its own token (mirrors EGDColon)
    # so a printed stack distinguishes it from a standalone flex sig.
    if req.procedure_type == "flex_sig" and getattr(req, "include_egd", False):
        parts.append("EGDFlexSig")
    else:
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
