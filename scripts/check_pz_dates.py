#!/usr/bin/env python3
"""CR-2 guard: the iron / med-stop instruction is a COMPUTED date, not relative.

The handouts state every other date as a real calendar date (low-residue day,
clear-liquids cutoff, big-prep day, arrival time). The iron / anti-diarrhea stop
must be the same: the personalization engine resolves the standard
`data-pz-day="-7" data-pz-template="by {date}"` marker against the appointment
date to "by Wed, Jun 24" (proc - 7 days). This script locks that behavior so a
future change to the date engine (app/personalization.py:apply_pz_substitutions
/ format_appt_date_short) can't silently regress the iron-stop date back to a
relative phrase.

No production code change rides on this — it's a dev/CI check.

Usage:  python scripts/check_pz_dates.py   (exits non-zero on failure)
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.personalization import apply_pz_substitutions  # noqa: E402

# The iron line's standard marker, as used across the bowel-prep / combined
# templates (EN uses "by {date}", ES uses "antes del {date}").
IRON_MARKER_EN = 'Stop iron <span data-pz-day="-7" data-pz-template="by {date}">7 days before</span>.'
IRON_MARKER_ES = 'Stop iron <span data-pz-day="-7" data-pz-template="antes del {date}">7 días antes</span>.'

# Spec acceptance criterion: procedure on 2026-07-01 -> stop iron by 2026-06-24.
APPT = datetime(2026, 7, 1, 9, 0)
CASES = [
    ("en", IRON_MARKER_EN, "by Wed, Jun 24"),
    ("es", IRON_MARKER_ES, "antes del mié, 24 jun"),
]


def main() -> int:
    failures = []
    for lang, markup, expected in CASES:
        out = apply_pz_substitutions(markup, APPT, lang)
        if expected not in out:
            failures.append(f"[{lang}] expected {expected!r} in resolved output, got: {out}")
    if failures:
        print("❌ iron-stop date is NOT computed correctly (proc - 7 days):")
        for f in failures:
            print(f"   {f}")
        return 1
    print("✅ iron-stop date computes to proc - 7 days "
          "(2026-07-01 -> 'by Wed, Jun 24' / 'antes del mié, 24 jun')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
