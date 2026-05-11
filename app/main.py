"""schedule.giready.com backend — FastAPI app.

Endpoints:
  POST /render        → personalized PDF (bowel_prep in Phase 1; other procedures in Phase 2)
  GET  /medications   → meds table for the frontend autocomplete (language-filtered)
  GET  /healthz       → 200 for liveness probes
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from . import medications
from .adapters import bowel_prep, combined, egd
from .adapters._paths import skill_source
from .personalization import (
    build_stop_meds_block,
    format_appt_date,
    format_time_12h,
)
from .qr import deep_link_qr_data_uri
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
    allow_credentials=False,
)


@app.get("/healthz")
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
    # Validate every stop_meds id exists.
    for med_id in req.stop_meds:
        if not medications.lookup(med_id):
            raise HTTPException(status_code=422, detail=f"unknown med id: {med_id!r}")

    appt_date_human = format_appt_date(req.appointment_date, req.language)
    appt_time = format_time_12h(req.appointment_time)
    arrival_time = format_time_12h(req.arrival_time)
    stop_block = build_stop_meds_block(req.stop_meds, req.language)

    # QR encodes the request payload so the viewer page can reconstruct.
    qr_payload = req.model_dump(mode="json")
    qr_uri = deep_link_qr_data_uri(qr_payload)

    common = dict(
        location_id=req.location_id,
        lang=req.language,
        appt_date_human=appt_date_human,
        appt_time_display=appt_time,
        arrival_time_display=arrival_time,
        stop_meds_block_html=stop_block,
        deep_link_qr_data_uri=qr_uri,
    )

    if req.procedure_type == "bowel_prep":
        pdf_bytes = bowel_prep.render_pdf(band_id=req.weight_band, **common)
        slug = "bowel-prep"
    elif req.procedure_type == "combined":
        pdf_bytes = combined.render_pdf(band_id=req.weight_band, **common)
        slug = "egd-colonoscopy"
    elif req.procedure_type == "egd":
        pdf_bytes = egd.render_pdf(**common)
        slug = "egd"
    else:
        raise HTTPException(
            status_code=501,
            detail=f"procedure_type={req.procedure_type!r} not yet implemented (Phase 2)",
        )

    filename = f"{slug}-{req.appointment_date.isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
