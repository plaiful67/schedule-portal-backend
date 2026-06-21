#!/usr/bin/env python3
"""Guard: every CSS class a personalized template's own <style> defines must
also be defined by the Calm stylesheets that REPLACE that <style> at render
time (calm-print.css ∪ calm-personalized.css).

The bowel_prep adapter swaps each forked personalized template's <style> for
Calm (app/adapters/bowel_prep.py `_swap_calm`). Any class the template styled
but Calm doesn't would silently lose its rule — a layout break no error would
flag. This check makes that a hard failure instead of an eyeball, per the
change-discipline shift to robot verification.

Run before every scheduler deploy:  python scripts/check_calm_personalized_coverage.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
TPL_DIR = BACKEND / "app" / "templates" / "bowel_prep"
# Read the Calm CSS that actually ships (vendored copy); fall back to the
# meta-repo source so the check works pre-vendor-sync too.
CALM_DIRS = [BACKEND / "vendor" / "shared",
             Path.home() / "peds-gi-prep-system" / "shared"]

# Body-level theme toggles (body.theme-*) are not content classes — the
# scheduler never sets them, so their rules are inert and need no Calm coverage.
IGNORE = re.compile(r"^theme-")


def _defined_classes(css: str) -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: set[str] = set()
    for sel, _ in re.findall(r"([^{}]+)\{([^{}]*)\}", css, re.S):
        out.update(re.findall(r"\.([A-Za-z][\w-]*)", sel))
    return out


def _read_calm(name: str) -> str:
    for d in CALM_DIRS:
        p = d / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def _first_style(html: str) -> str:
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    return m.group(1) if m else ""


def main() -> int:
    calm = _read_calm("calm-print.css")
    if not calm:
        print("FATAL: calm-print.css not found in", CALM_DIRS, file=sys.stderr)
        return 2
    covered = _defined_classes(calm) | _defined_classes(_read_calm("calm-personalized.css"))

    templates = sorted(TPL_DIR.glob("*print-personalized*.html"))
    if not templates:
        print("FATAL: no personalized templates found", file=sys.stderr)
        return 2

    failures: dict[str, set[str]] = {}
    for t in templates:
        defined = _defined_classes(_first_style(t.read_text(encoding="utf-8")))
        orphans = {c for c in defined - covered if not IGNORE.match(c)}
        if orphans:
            failures[t.name] = orphans

    if failures:
        print("FAIL — personalization classes dropped by the Calm swap:\n")
        for name, orphans in failures.items():
            print(f"  {name}: {', '.join('.' + c for c in sorted(orphans))}")
        print("\nAdd these rules to ~/peds-gi-prep-system/shared/calm-personalized.css")
        return 1

    print(f"OK — {len(templates)} personalized templates fully covered by Calm "
          f"({len(covered)} classes in calm-print.css ∪ calm-personalized.css).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
