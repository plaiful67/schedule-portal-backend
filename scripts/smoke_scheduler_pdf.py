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
  python scripts/smoke_scheduler_pdf.py --base-url https://schedule-portal-xxxx-uc.a.run.app
  python scripts/smoke_scheduler_pdf.py --base-url https://candidate---schedule-portal-...a.run.app  # tagged candidate revision (CI)

  (`--base` is kept as a back-compat alias for `--base-url`.)
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

# calm_assert lives in the meta repo for local dev; on a bare CI runner (no meta
# checkout) fall back to the copy vendored beside this script. Dev path is inserted
# last so it wins when present; the _ci fallback covers GitHub Actions.
sys.path.insert(0, str(Path(__file__).resolve().parent / "_ci"))          # CI fallback
sys.path.insert(0, str(Path.home() / "peds-gi-prep-system" / "scripts"))  # dev (meta repo)
from calm_assert import is_calm  # noqa: E402

# A future date keeps the schema's "today or later" validator happy.
APPT = (date.today() + timedelta(days=21)).isoformat()
PHYS = "tibesar"          # → "Dr. Tibesar" in the footer/callout
PHYS_NAME = "Tibesar"

# Extractable proof the {{PARTIAL_FEEDBACK_BAR}} partial rendered (the QR img
# is a data-URI, not text). Guards the EGD-feedback-bar regression class.
#
# Two distinct ES strings exist by design:
#  - Bowel-prep personalized templates (hardcoded): "piensa de estas instrucciones"
#  - EGD/EGD+pH-MII/composed templates (shared partial): "opina sobre estas instrucciones"
# Both are valid; the assertion checks that at least one is present.
FEEDBACK_CAPTION: dict[str, list[str]] = {
    "en": ["Tell us what you think of these instructions"],
    "es": [
        "Díganos qué piensa de estas instrucciones",   # bowel-prep personalized templates
        "Díganos qué opina sobre estas instrucciones",  # shared partial (EGD/composed)
    ],
}

# (label, payload-overrides). Common fields filled in below.
CASES = [
    ("std en",       dict(procedure_type="bowel_prep", weight_band="31-40", language="en")),
    ("std es",       dict(procedure_type="bowel_prep", weight_band="31-40", language="es")),
    ("infant en",    dict(procedure_type="bowel_prep", weight_band="under-15", language="en")),
    ("combined en",  dict(procedure_type="combined",   weight_band="31-40", language="en")),
    ("combined es",  dict(procedure_type="combined",   weight_band="31-40", language="es")),
    ("suprep en",    dict(procedure_type="bowel_prep", weight_band="over-50", language="en", prep_type="suprep")),
    ("clenpiq en",   dict(procedure_type="bowel_prep", weight_band="31-40", language="en", prep_type="clenpiq")),
    # Combined EGD+colonoscopy with the higher-potency preps (loosened diet:
    # eat normally → low-residue through lunch → clears after 2 PM the day before).
    ("combined suprep en",  dict(procedure_type="combined", weight_band="over-50", language="en", prep_type="suprep")),
    ("combined clenpiq es", dict(procedure_type="combined", weight_band="31-40", language="es", prep_type="clenpiq")),
    ("lactulose en", dict(procedure_type="bowel_prep", weight_band="15-20", language="en", prep_type="lactulose")),
    # EGD / EGD+pH-MII carry no weight band. egd_phmii is PMCH-only.
    ("egd en",       dict(procedure_type="egd", language="en")),
    ("egd es",       dict(procedure_type="egd", language="es")),
    ("egdph en",     dict(procedure_type="egd_phmii", language="en", location_id="pmch")),
    ("egdph es",     dict(procedure_type="egd_phmii", language="es", location_id="pmch")),
    # Composed base cases — real registry add-on ids dlb/dise.
    # composed egd has no weight_band/prep_type (egd base forbids them).
    # `expect` key: whitespace-normalized substring that must appear in the PDF text —
    # guards the silent-add-on-drop regression class.
    ("composed colon en",    dict(procedure_type="composed", base="colonoscopy",
                                  weight_band="31-40", prep_type="miralax",
                                  language="en", add_ons=["dlb"], knob_picks={},
                                  expect="direct laryngoscopy and bronchoscopy")),
    ("composed combined es", dict(procedure_type="composed", base="combined",
                                  weight_band="31-40", prep_type="miralax",
                                  language="es", add_ons=["dise"], knob_picks={},
                                  expect="endoscopia del sueño")),
    ("composed egd en",      dict(procedure_type="composed", base="egd",
                                  language="en", add_ons=["dise"], knob_picks={},
                                  expect="sleep endoscopy")),
    # Flex sig — Increment 1: MiraLAX only (relabels the standard colonoscopy
    # template). Lactulose/clenpiq/suprep/enema have their own templates (different
    # hardcoded headings) → tokenized in a later increment.
    ("flexsig miralax 31-40 en",  dict(procedure_type="flex_sig", weight_band="31-40",
                                       language="en", prep_type="miralax",
                                       expect="Flexible Sigmoidoscopy")),
    ("flexsig miralax 21-30 es",  dict(procedure_type="flex_sig", weight_band="21-30",
                                       language="es", prep_type="miralax",
                                       expect="Sigmoidoscopia Flexible")),
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


def checks(pdf: bytes, lang: str) -> list[str]:
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
    # Collapse whitespace before caption search: WeasyPrint word-wraps the
    # feedback bar span so pypdf extracts the caption with embedded newlines
    # (e.g. "Tell us what you think of\nthese instructions"). Normalising to
    # single spaces lets us match the expected string verbatim.
    text_ws = re.sub(r"\s+", " ", text)
    captions = FEEDBACK_CAPTION.get(lang, FEEDBACK_CAPTION["en"])
    if not any(c in text_ws for c in captions):
        problems.append(
            f"feedback-QR bar caption missing for lang={lang!r} "
            f"(expected one of {captions!r}) — feedback bar dropped?"
        )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", dest="base_url", default=None,
                    help="backend base URL to smoke, e.g. a tagged candidate revision "
                         "(default: resolve the live schedule-portal URL via gcloud)")
    ap.add_argument("--base", dest="base_url", default=None,
                    help=argparse.SUPPRESS)  # back-compat alias for --base-url
    args = ap.parse_args()
    base = (args.base_url or resolve_base()).rstrip("/")
    print(f"smoke → {base}/render  (appt {APPT}, physician {PHYS})\n")

    failed = 0
    for label, over in CASES:
        payload = dict(
            location_id="scc", physician_id=PHYS,
            appointment_date=APPT, appointment_time="08:30", arrival_time="07:30",
            stop_meds=["ibuprofen"], include_directions=True,
        )
        # Extract `expect` without mutating the case dict — it's a smoke-guard key, not an API field.
        expect_substr = over.get("expect", None)
        payload.update({k: v for k, v in over.items() if k != "expect"})
        # EGD+pH is PMCH-only; not exercised here (bowel-prep families only).
        try:
            pdf = render(base, payload)
            problems = checks(pdf, payload.get("language", "en"))
            # Add-on text guard: assert the add-on blurb text is present in the PDF.
            if expect_substr is not None:
                from pypdf import PdfReader
                from io import BytesIO
                reader = PdfReader(BytesIO(pdf))
                text_ws = re.sub(r"\s+", " ", "\n".join(
                    (p.extract_text() or "") for p in reader.pages))
                if expect_substr not in text_ws:
                    problems.append(
                        f"add-on text missing: expected {expect_substr!r} in PDF text "
                        f"(add-on blurb silently dropped?)"
                    )
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
