#!/usr/bin/env python3
"""Feature gate for the canonical ("office") handout PDFs.

Renders a representative sample of office PDFs (via the backend adapters in
audience="office" mode) and asserts the properties that DEFINE the new format —
the three Sebastian named (Calm theme, ADA/PDF-UA, looser day-before diet) plus
the two office-specific deltas (all doctors, no procedure date):

  1. Calm theme     — Newsreader + Hanken-Grotesk fonts embedded (a font fact;
                      mirrors scripts/calm_assert.py in the meta repo).
  2. ADA / PDF-UA   — StructTreeRoot present + /MarkInfo Marked true.
  3. Looser diet    — English samples say "day before" and NOT "3 days before".
  4. All doctors    — all five roster names appear; the group roster label shows.
  5. No single doc  — no "Performing physician" / "Médico que realiza" line.
  6. No date        — the appt-callout labels are gone; no concrete Month DD, YYYY.

Run inside the backend venv:
    .venv/bin/python scripts/verify_canonical.py
Exit 0 = all samples pass; exit 1 = one or more assertions failed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.adapters import bowel_prep, egd_phmii, flex_sig  # noqa: E402

CALM_FONTS = ("newsreader", "hanken")
ROSTER = ["Deivanayagam", "Dunn", "Schaefer", "Tibesar", "Zavoian"]

# (label, lang, diet_check, callable -> pdf bytes)
# diet_check applies the looser day-before diet assertion — TRUE only for the
# colonoscopy bowel-prep samples. EGD+pH-MII legitimately says "3 days before"
# for medication washouts (prokinetics/H2 stop 3 days before), so its diet check
# is off to avoid a false positive.
SAMPLES = [
    ("miralax std 21-30 SCC en", "en", True,
     lambda: bowel_prep.render_pdf(band_id="21-30", location_id="scc", lang="en",
                                   variant="standard", prep_type="miralax", audience="office")),
    ("suprep over-50 SCC en", "en", True,
     lambda: bowel_prep.render_pdf(band_id="over-50", location_id="scc", lang="en",
                                   variant="standard", prep_type="suprep", audience="office")),
    ("clenpiq 31-40 SCC en", "en", True,
     lambda: bowel_prep.render_pdf(band_id="31-40", location_id="scc", lang="en",
                                   variant="standard", prep_type="clenpiq", audience="office")),
    ("lactulose 15-20 PMCH es", "es", False,
     lambda: bowel_prep.render_pdf(band_id="15-20", location_id="pmch", lang="es",
                                   variant="standard", prep_type="lactulose", audience="office")),
    ("combined miralax 31-40 SCC en", "en", True,
     lambda: bowel_prep.render_pdf(band_id="31-40", location_id="scc", lang="en",
                                   variant="combined", prep_type="miralax", audience="office")),
    ("egd_phmii PMCH en", "en", False,
     lambda: egd_phmii.render_pdf(location_id="pmch", lang="en", audience="office")),
    ("flexsig enema 20-40kg SCC en", "en", False,
     lambda: flex_sig.render_pdf(weight_band="20-40kg", prep_type="enema",
                                 location_id="scc", lang="en", audience="office")),
]

MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December")
CONCRETE_DATE_RE = re.compile(rf"\b(?:{MONTHS})\s+\d{{1,2}},\s+20\d\d\b")


def _embedded_fonts(reader: PdfReader) -> set[str]:
    fonts: set[str] = set()
    for pg in reader.pages:
        res = pg.get("/Resources")
        if not res:
            continue
        fd = res.get_object().get("/Font")
        if not fd:
            continue
        for f in fd.get_object().values():
            fo = f.get_object()
            bf = fo.get("/BaseFont")
            if bf:
                fonts.add(str(bf).lower())
            for df in (fo.get("/DescendantFonts") or []):
                dbf = df.get_object().get("/BaseFont")
                if dbf:
                    fonts.add(str(dbf).lower())
    return fonts


def check(label: str, lang: str, diet_check: bool, pdf: bytes) -> list[str]:
    """Return a list of failure strings ([] == pass)."""
    fails: list[str] = []
    reader = PdfReader(__import__("io").BytesIO(pdf))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    flat = re.sub(r"\s+", " ", text)  # collapse wraps so "Dr.\nTibesar" matches

    # 1. Calm theme (fonts)
    fonts = _embedded_fonts(reader)
    for want in CALM_FONTS:
        if not any(want in f for f in fonts):
            fails.append(f"calm: no {want!r} font embedded")

    # 2. PDF/UA structure
    root = reader.trailer["/Root"]
    if "/StructTreeRoot" not in root:
        fails.append("pdf/ua: no StructTreeRoot")
    mark_info = root.get("/MarkInfo")
    if not (mark_info and mark_info.get_object().get("/Marked")):
        fails.append("pdf/ua: /MarkInfo Marked not true")

    # 4. All doctors + roster label
    missing = [d for d in ROSTER if d not in flat]
    if missing:
        fails.append(f"all-doctors: missing {missing}")
    roster_label = "gastroenterolog" in flat.lower()  # EN/ES share the stem
    if not roster_label:
        fails.append("all-doctors: roster label absent")

    # 5. No single-physician callout
    for banned in ("Performing physician", "Médico que realiza"):
        if banned in flat:
            fails.append(f"no-date/physician: found {banned!r}")

    # 6. No appointment date box / concrete date
    for banned in ("Procedure date", "Fecha del procedimiento", "Arrival:"):
        if banned in flat:
            fails.append(f"no-date: found appt-callout label {banned!r}")
    m = CONCRETE_DATE_RE.search(flat)
    if m:
        fails.append(f"no-date: concrete date present {m.group(0)!r}")

    # 3. Looser diet — colonoscopy bowel-prep English samples only (Spanish
    # wording differs; EGD/pH-MII "3 days before" is a med washout, not diet).
    if diet_check and lang == "en":
        low = flat.lower()
        if "day before" not in low:
            fails.append("diet: 'day before' wording absent")
        if "3 days before" in low:
            fails.append("diet: legacy '3 days before' wording present")

    return fails


def main() -> int:
    any_fail = False
    for label, lang, diet_check, render in SAMPLES:
        try:
            pdf = render()
        except Exception as e:  # a render crash is itself a gate failure
            print(f"FAIL {label}: render error {type(e).__name__}: {e}")
            any_fail = True
            continue
        fails = check(label, lang, diet_check, pdf)
        if fails:
            any_fail = True
            print(f"FAIL {label}:")
            for f in fails:
                print(f"       - {f}")
        else:
            print(f"PASS {label}")
    print()
    if any_fail:
        print("verify_canonical: FAILED")
        return 1
    print("verify_canonical: all samples PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
