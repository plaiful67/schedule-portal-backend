#!/usr/bin/env python3
"""Post-deploy smoke for schedule.giready.com generated PDFs.

POSTs a no-PHI sample appointment to the live /render endpoint for each
bowel-prep procedure path and asserts the returned PDF is correct *in
production* — the layer that proves the Calm CSS + baked fonts actually took
effect on Cloud Run (not just locally). For each case it checks:

  • HTTP 200 + application/pdf
  • Calm: Newsreader + Hanken Grotesk embedded (calm_assert)   ← the Calm proof
  • Tagged PDF/UA: MarkInfo/Marked + StructTreeRoot
  • No leaked {{TOKEN}} placeholders in the extracted text
  • The performing physician's name is present

No patient identifiers are sent (weight band / procedure / location / language /
physician are not PHI by construction).

Usage:
  python scripts/smoke_scheduler_pdf.py                 # auto-resolve Cloud Run URL via gcloud
  python scripts/smoke_scheduler_pdf.py --base https://schedule-portal-xxxx-uc.a.run.app
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from urllib import request as urlreq

sys.path.insert(0, str(Path.home() / "peds-gi-prep-system" / "scripts"))
from calm_assert import is_calm  # noqa: E402

# A future date keeps the schema's "today or later" validator happy.
APPT = (date.today() + timedelta(days=21)).isoformat()
PHYS = "tibesar"          # → "Dr. Tibesar" in the footer/callout
PHYS_NAME = "Tibesar"

# (label, payload-overrides). Common fields filled in below.
CASES = [
    ("std en",       dict(procedure_type="bowel_prep", weight_band="31-40", language="en")),
    ("std es",       dict(procedure_type="bowel_prep", weight_band="31-40", language="es")),
    ("infant en",    dict(procedure_type="bowel_prep", weight_band="under-15", language="en")),
    ("combined en",  dict(procedure_type="combined",   weight_band="31-40", language="en")),
    ("combined es",  dict(procedure_type="combined",   weight_band="31-40", language="es")),
    ("suprep en",    dict(procedure_type="bowel_prep", weight_band="over-50", language="en", prep_type="suprep")),
    ("clenpiq en",   dict(procedure_type="bowel_prep", weight_band="31-40", language="en", prep_type="clenpiq")),
    ("lactulose en", dict(procedure_type="bowel_prep", weight_band="15-20", language="en", prep_type="lactulose")),
    # EGD / EGD+pH-MII carry no weight band. egd_phmii is PMCH-only.
    ("egd en",       dict(procedure_type="egd", language="en")),
    ("egd es",       dict(procedure_type="egd", language="es")),
    ("egdph en",     dict(procedure_type="egd_phmii", language="en", location_id="pmch")),
    ("egdph es",     dict(procedure_type="egd_phmii", language="es", location_id="pmch")),
]


def resolve_base() -> str:
    out = subprocess.run(
        ["gcloud", "run", "services", "describe", "schedule-portal",
         "--region", "us-central1", "--format=value(status.url)"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def render(base: str, payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    req = urlreq.Request(f"{base}/render", data=body,
                         headers={"Content-Type": "application/json"}, method="POST")
    with urlreq.urlopen(req, timeout=60) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if "application/pdf" not in ctype:
            raise AssertionError(f"content-type={ctype!r} (expected application/pdf)")
        return resp.read()


def checks(pdf: bytes) -> list[str]:
    from pypdf import PdfReader
    problems = []
    reader = PdfReader(BytesIO(pdf))
    root = reader.trailer["/Root"]
    if not (root.get("/MarkInfo", {}).get("/Marked") and "/StructTreeRoot" in root):
        problems.append("not tagged (MarkInfo/StructTreeRoot)")
    calm, fonts = is_calm(pdf)
    if not calm:
        problems.append(f"not Calm (fonts={sorted(fonts)})")
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    leaked = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", text)))
    if leaked:
        problems.append(f"leaked tokens {leaked}")
    if PHYS_NAME not in text:
        problems.append(f"physician {PHYS_NAME!r} missing from text")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None, help="backend base URL (default: resolve via gcloud)")
    args = ap.parse_args()
    base = args.base or resolve_base()
    print(f"smoke → {base}/render  (appt {APPT}, physician {PHYS})\n")

    failed = 0
    for label, over in CASES:
        payload = dict(
            location_id="scc", physician_id=PHYS,
            appointment_date=APPT, appointment_time="08:30", arrival_time="07:30",
            stop_meds=["ibuprofen"], include_directions=True,
        )
        payload.update(over)
        # EGD+pH is PMCH-only; not exercised here (bowel-prep families only).
        try:
            pdf = render(base, payload)
            problems = checks(pdf)
            if problems:
                failed += 1
                print(f"  ✗ {label:13s} {len(pdf):7d}B  — {'; '.join(problems)}")
            else:
                print(f"  ✓ {label:13s} {len(pdf):7d}B  Calm + tagged + clean")
        except Exception as e:
            failed += 1
            print(f"  ✗ {label:13s} ERROR: {type(e).__name__}: {e}")

    print()
    if failed:
        print(f"SMOKE FAILED — {failed}/{len(CASES)} cases bad. Do not keep this revision live.")
        return 1
    print(f"SMOKE PASS — {len(CASES)}/{len(CASES)} live PDFs Calm + tagged + clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
