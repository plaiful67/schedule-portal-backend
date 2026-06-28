# VENDORED FOR CI from ~/peds-gi-prep-system/scripts/calm_assert.py — the GH
# Actions runner has no meta-repo checkout. Keep in sync (make vendor-check
# flags drift). The smoke prefers the live meta copy; this is the fallback.
#!/usr/bin/env python3
"""assert_calm — prove a PDF was actually rendered in the Calm theme.

Calm correctness is a *font* fact: Calm sets Newsreader (serif) + Hanken
Grotesk (sans). If the Calm stylesheet didn't get swapped in (e.g. a missing
vendored calm-print.css) or the fonts weren't available at render time
(container missing the baked TTFs), WeasyPrint silently falls back to a default
serif/sans and the PDF looks "off" but never errors. Enumerating the embedded
fonts catches both failure modes with a single check — no human has to look.

Used by:
  - the website live-PDF smoke (verify-pdf-live)
  - scripts/smoke_scheduler_pdf.py (scheduler, post-deploy)
  - ad-hoc:  python scripts/calm_assert.py path/to.pdf
             curl ... | python scripts/calm_assert.py -
"""
from __future__ import annotations

import sys
from io import BytesIO

# Substrings expected in the BaseFont names of a Calm PDF (case-insensitive).
CALM_FONTS = ("newsreader", "hanken")


def embedded_fonts(pdf_bytes: bytes) -> set[str]:
    """Return the set of BaseFont names referenced anywhere in the PDF."""
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(pdf_bytes))
    names: set[str] = set()

    def walk_resources(res) -> None:
        if not res:
            return
        res = res.get_object()
        fonts = res.get("/Font")
        if fonts:
            for f in fonts.get_object().values():
                fo = f.get_object()
                bf = fo.get("/BaseFont")
                if bf:
                    names.add(str(bf).lstrip("/"))
                # Type0 composite fonts nest the real face in DescendantFonts.
                for df in (fo.get("/DescendantFonts") or []):
                    dbf = df.get_object().get("/BaseFont")
                    if dbf:
                        names.add(str(dbf).lstrip("/"))
        xobjs = res.get("/XObject")
        if xobjs:
            for xo in xobjs.get_object().values():
                walk_resources(xo.get_object().get("/Resources"))

    for page in reader.pages:
        walk_resources(page.get("/Resources"))
    return names


def is_calm(pdf_bytes: bytes) -> tuple[bool, set[str]]:
    fonts = embedded_fonts(pdf_bytes)
    low = " ".join(fonts).lower()
    ok = all(tok in low for tok in CALM_FONTS)
    return ok, fonts


def assert_calm(pdf_bytes: bytes, label: str = "pdf") -> None:
    ok, fonts = is_calm(pdf_bytes)
    if not ok:
        raise AssertionError(
            f"{label}: NOT Calm — expected embedded fonts containing "
            f"{CALM_FONTS}, found: {sorted(fonts) or '(none)'}"
        )


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: calm_assert.py <file.pdf|->", file=sys.stderr)
        return 2
    src = argv[1]
    data = sys.stdin.buffer.read() if src == "-" else open(src, "rb").read()
    ok, fonts = is_calm(data)
    print(f"{'CALM ✓' if ok else 'NOT CALM ✗'}  fonts={sorted(fonts)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
