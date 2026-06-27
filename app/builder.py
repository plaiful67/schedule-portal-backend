"""No-code builder API (plan M6) + AI-draft endpoint (M7).

The builder SPA (giready-builder/) is CRUD over the tenant model plus two
triggers. This module is the thin server side:

  POST /builder/tenant  — write tenants/<id>/tenant.yaml + a content_status.yaml
                          in DRAFT state, and the uploaded logo. Pure CRUD over
                          the M0 tenant model; it does NOT bypass the M5 gate.
  POST /builder/build   — run render.py --tenant + build_websites.py --tenant to
                          a LOCAL preview dir; return a file:// preview path.
                          No DNS, no wrangler, no deploy.
  POST /draft           — AI authoring assist (plan Layer 5). Server-side
                          Anthropic Messages API (key stays server-side); falls
                          back to a clearly-labelled MOCK draft when no key is
                          available. The draft is INERT — it only fills the
                          builder's editable textarea and (when saved) writes to
                          DRAFT state, feeding the SAME M5 approval gate. A
                          clinical_signer must approve before anything publishes.

GUARDRAILS: local/preview only (no real domain — apex stays *.example); the
builder writes DRAFT content, never approved; AI draft != approval.

NO-PHI BOUNDARY: /draft handles TEMPLATE PROSE only — a practice's plain-English
description of its prep instructions. No patient identifiers are sent to the
LLM. The scheduler personalization path (/render) stays LLM-free. If a future
version lets practices paste PHI into the description box, a BAA is required
before the LLM call.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException

from .adapters._paths import skill_dir, shared_dir

router = APIRouter()

# Where the builder writes tenant records. Prefer the live meta-repo checkout
# (so render.py reading via Path.home() sees them); the builder is a local-dev
# tool, so the live tree is the right target.
_TENANTS_DIR = Path.home() / "peds-gi-prep-system" / "tenants"
# Local preview output root (never a real deploy target).
_PREVIEW_ROOT = Path(tempfile.gettempdir()) / "giready-builder-preview"

# Anthropic model — current capable default per the claude-api skill.
_ANTHROPIC_MODEL = "claude-opus-4-8"

# Site families the prototype builder can request. Mirrors data/sites.yaml.
_PROCEDURE_FAMILIES = {"colonoscopy", "combined"}


def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "tenant"


def _safe_tenant_id(tenant_id: str) -> str:
    """A tenant id that can never escape the tenants/ dir or clobber giready."""
    tid = _slug(tenant_id)
    if tid in ("giready", "", "."):
        raise HTTPException(status_code=400,
                            detail="tenant id must not be 'giready' (the production tenant).")
    return tid


def _require_example_apex(apex: str) -> str:
    """Guardrail: a builder-created tenant must use a .example apex — no real
    domain may be registered or referenced while the legal/IP gate is open."""
    apex = (apex or "").strip().lower()
    if not apex.endswith(".example"):
        raise HTTPException(
            status_code=400,
            detail=f"apex must end with '.example' for the prototype (got {apex!r}); "
                   "real domains are a later, counsel-cleared step.")
    return apex


# --- POST /builder/tenant ---------------------------------------------------

def write_tenant(payload: dict) -> dict:
    """Write tenants/<id>/{tenant.yaml,content_status.yaml,logo}. Content is
    written in DRAFT state — publishing stays blocked until a clinical_signer
    approves via /content/approve (the M5 gate). Returns {tenant_id, ...}."""
    tenant_id = _safe_tenant_id(payload.get("tenant_id") or payload.get("display_name", ""))
    practice = payload.get("practice", {}) or {}
    apex = _require_example_apex(payload.get("apex") or practice.get("apex", ""))
    storage_prefix = _slug(payload.get("storage_prefix") or tenant_id)
    theme = payload.get("theme", "calm")
    display_name = payload.get("display_name") or tenant_id

    # Logo: optional base64 → tenants/<id>/<file>. A stub is used if omitted.
    logo_filename = "giready-logo.png"  # stub (reuses the bundled artwork)
    logo_alt = f"{display_name}"
    tdir = _TENANTS_DIR / tenant_id
    tdir.mkdir(parents=True, exist_ok=True)
    logos = payload.get("logos") or {}
    b64 = logos.get("base64") or ""
    if b64:
        # data URL or bare base64
        if "," in b64 and b64.strip().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        try:
            raw = base64.b64decode(b64, validate=False)
            ext = ".png" if logos.get("type", "").endswith("png") else ".img"
            (tdir / f"logo{ext}").write_bytes(raw)
            logo_filename = f"logo{ext}"
        except Exception:
            pass  # fall back to stub; never fail the build on a bad upload

    # Doctors[].
    doctors = []
    for d in payload.get("doctors", []) or []:
        slug = _slug(d.get("id") or d.get("name_short", ""))
        if not slug:
            continue
        doctors.append({
            "id": slug,
            "name_short": d.get("name_short", slug),
            "profile_url": d.get("profile_url", f"https://{apex}/providers/{slug}"),
        })

    # Locations{}.
    locations = {}
    for key, lb in (payload.get("locations") or {}).items():
        lk = _slug(key)
        if not lk:
            continue
        phone = lb.get("phone", "(555) 014-2210")
        locations[lk] = {
            "name_en": lb.get("name_en", lk),
            "name_es": lb.get("name_es", lb.get("name_en", lk)),
            "cheatsheet_name": lb.get("name_en", lk),
            "address": lb.get("address", ""),
            "phone": phone,
            "phone_label_en": "Office phone",
            "phone_label_es": "Teléfono",
            "arrival_en": "Arrive 1 hour before your scheduled procedure",
            "arrival_es": "Llegue 1 hora antes de su procedimiento programado",
            "arrival_minutes_before": 60,
            "arrival_facility_short_en": lb.get("name_en", lk),
            "arrival_facility_short_es": lb.get("name_en", lk),
            "maps_url_en": f"https://maps.{apex}/{lk}",
            "maps_url_es": f"https://maps.{apex}/{lk}?hl=es",
            "mobile_subdomain": _slug(lb.get("mobile_subdomain", "prep")),
            "mobile_subdomain_combined": _slug(lb.get("mobile_subdomain_combined",
                                                       "egdcolon")),
            "clears_npo_hours": 2,
        }

    tenant_doc = {
        "tenant": {
            "id": tenant_id,
            "display_name": display_name,
            "apex": apex,
            "storage_prefix": storage_prefix,
            "theme": theme,
        },
        "practice": {
            "phone": practice.get("phone", "(555) 014-2200"),
            "phone_tel": re.sub(r"\D", "", practice.get("phone", "(555) 014-2200")),
            "logo_filename": logo_filename,
            "logo_alt": logo_alt,
            "cover_stack_en": [display_name, "Pediatric Gastroenterology",
                               practice.get("phone", "(555) 014-2200")],
            "cover_stack_es": [display_name, "Gastroenterología Pediátrica",
                               practice.get("phone", "(555) 014-2200")],
            "footer_en": f"{display_name}  ·  {practice.get('phone', '(555) 014-2200')}",
            "footer_es": f"{display_name}  ·  {practice.get('phone', '(555) 014-2200')}",
            "doctors": doctors,
        },
        "qr_targets": {
            "youtube_url_en": f"https://{apex}/videos/prep",
            "youtube_url_es": f"https://{apex}/videos/prep-es",
            "portal_url": f"https://{apex}/patient-portal",
            "gikids_url": "https://gikids.org/tests-procedures/colonoscopy/",
            "meds_giready_url": f"https://meds.{apex}",
        },
        "locations": locations,
    }
    # NOTE: the builder INHERITS giready's dose tables (dosing.yaml) for the
    # prototype — the practice supplies prose + branding, not dose numbers.
    (tdir / "tenant.yaml").write_text(
        "# Generated by the no-code builder (M6). PROTOTYPE — local preview only.\n"
        "# Inherits giready's dose tables; the practice supplies prose + branding.\n"
        + yaml.safe_dump(tenant_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8")

    # content_status.yaml — every requested family starts DRAFT. Publishing is
    # blocked by the M5 gate until a clinical_signer approves.
    procedures = [p for p in (payload.get("procedures") or ["colonoscopy", "combined"])
                  if p in _PROCEDURE_FAMILIES]
    units = {fam: {"state": "draft"} for fam in (procedures or ["colonoscopy"])}
    cs_doc = {"content_status": {"default_signer_role": "clinical_signer", "units": units}}
    (tdir / "content_status.yaml").write_text(
        "# Generated by the builder. All units start DRAFT — a clinical_signer\n"
        "# must approve (POST /content/approve) before anything publishes.\n"
        + yaml.safe_dump(cs_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8")

    return {"tenant_id": tenant_id, "apex": apex,
            "units": list(units.keys()), "state": "draft",
            "tenant_path": str(tdir / "tenant.yaml")}


# --- POST /builder/build ----------------------------------------------------

def build_preview(tenant_id: str, allow_draft_preview: bool = True) -> dict:
    """Render + build the tenant to a LOCAL preview dir. By default builds
    unsigned (draft) units as WATERMARKED noindex previews so the operator can
    see them — they are NEVER published to a real apex. Returns a file:// URL."""
    tenant_id = _safe_tenant_id(tenant_id)
    skill = skill_dir("bowel-prep-generator")
    py = sys.executable  # the backend venv python (has the skill deps vendored)
    # Use the LIVE skill scripts (they carry M1/M2 --tenant support).
    render_py = skill / "scripts" / "render.py"
    build_py = skill / "scripts" / "build_websites.py"
    out = _PREVIEW_ROOT / tenant_id
    if out.exists():
        import shutil
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    logs = []
    # 1. Render branded mobile + PDF (scc location).
    r1 = subprocess.run(
        [py, str(render_py), "--tenant", tenant_id, "--out", str(out / "render"),
         "--location", "scc"],
        capture_output=True, text=True, env=env)
    logs.append(("render", r1.returncode, r1.stderr[-600:]))
    # 2. Build the mobile sites to the preview root (draft → watermarked).
    build_args = [py, str(build_py), "--tenant", tenant_id,
                  "--preview-out", str(out / "sites")]
    if allow_draft_preview:
        build_args.append("--allow-draft-preview")
    build_args += ["colonoscopy", "combined"]
    r2 = subprocess.run(build_args, capture_output=True, text=True, env=env)
    logs.append(("build", r2.returncode, (r2.stdout[-400:] + r2.stderr[-600:])))

    if r1.returncode != 0 and r2.returncode != 0:
        raise HTTPException(status_code=500,
                            detail=f"build failed: {logs}")

    # Find a representative preview page.
    preview_page = None
    for cand in sorted((out / "sites").rglob("index.html")):
        preview_page = cand
        break
    return {
        "tenant_id": tenant_id,
        "preview_url": (preview_page.as_uri() if preview_page else out.as_uri()),
        "preview_dir": str(out),
        "watermarked_draft": allow_draft_preview,
        "note": "DRAFT content is watermarked + noindex and is NOT published. "
                "A clinical_signer must approve each unit before it can ship.",
        "build_log": [{"step": s, "rc": rc} for s, rc, _ in logs],
    }


# --- POST /draft (M7, AI authoring assist) ----------------------------------

_DRAFT_SYSTEM = (
    "You are a clinical-content drafting assistant for a pediatric "
    "gastroenterology practice's patient handouts. You draft sectioned HTML that "
    "fills the existing handout template structure. You NEVER invent or alter "
    "medication doses, volumes, or timing — those come from the practice's own "
    "validated dose tables, not from you. Draft prose only: the About section, "
    "the rationale, reassurance, and what-to-expect copy. A clinician reviews and "
    "signs off on every word before anything is shown to a patient."
)


def _draft_prompt(description: str, procedures: list[str]) -> str:
    procs = ", ".join(procedures) if procedures else "colonoscopy"
    return (
        f"Draft patient-handout prose for a {procs} bowel-prep handout, based on "
        f"this practice's plain-English description:\n\n{description}\n\n"
        "Return sectioned HTML using <h2> section headings and <p> paragraphs. "
        "Cover: a brief friendly About section, why the prep matters, and what to "
        "expect. Do NOT include any specific doses, volumes, or clock times — the "
        "template fills those from the practice's dose tables. Keep it warm, plain, "
        "and reassuring for a parent. No PHI."
    )


def make_draft(description: str, procedures: list[str], tenant_id: str) -> dict:
    """Return a sectioned-HTML draft. Uses the Anthropic Messages API when a key
    is available (key stays server-side); otherwise returns a clearly-labelled
    MOCK draft so the demo isn't blocked. The draft is INERT — it feeds the M5
    approval gate; nothing publishes until a clinical_signer approves."""
    description = (description or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="description is required")
    procedures = procedures or ["colonoscopy"]

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        try:
            import anthropic  # noqa: PLC0415
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=_ANTHROPIC_MODEL,
                max_tokens=2000,
                system=_DRAFT_SYSTEM,
                output_config={"effort": "medium"},
                messages=[{"role": "user",
                           "content": _draft_prompt(description, procedures)}],
            )
            html = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            return {"mock": False, "model": _ANTHROPIC_MODEL, "state": "draft",
                    "html": html,
                    "note": "AI draft — INERT. A clinical_signer must approve "
                            "before this content can publish."}
        except Exception as e:
            # Fall through to mock on any SDK/availability error.
            return _mock_draft(description, procedures, reason=f"anthropic call failed: {e}")
    return _mock_draft(description, procedures,
                       reason="no ANTHROPIC_API_KEY in this environment")


def _mock_draft(description: str, procedures: list[str], reason: str) -> dict:
    procs = " + ".join(procedures)
    safe_desc = (description[:400] + "…") if len(description) > 400 else description
    html = (
        f"<h2>About your child's {procs} prep</h2>\n"
        f"<p>This handout explains how to get your child ready. Your care team "
        f"has reviewed every step. Follow the schedule below and call the office "
        f"with any questions.</p>\n"
        f"<h2>Why the prep matters</h2>\n"
        f"<p>A clean, well-prepared bowel lets the doctor see clearly and helps "
        f"the procedure go smoothly and safely. Completing the full prep is the "
        f"single most important thing you can do to help.</p>\n"
        f"<h2>What to expect</h2>\n"
        f"<p>Your child will drink the prep on the schedule shown. It is normal "
        f"for stools to become frequent and watery — that means the prep is "
        f"working. Keep your child near a bathroom and offer plenty of clear "
        f"liquids.</p>\n"
        f"<!-- MOCK DRAFT — generated from the practice description, NOT an LLM. "
        f"Reason: {reason}. Source description: {safe_desc} -->"
    )
    return {"mock": True, "model": None, "state": "draft", "html": html,
            "reason": reason,
            "note": "MOCK DRAFT (no LLM ran in this environment). Still INERT — a "
                    "clinical_signer must approve before it can publish."}
