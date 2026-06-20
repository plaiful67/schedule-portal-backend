#!/usr/bin/env python3
"""
Generate the weight-band lb labels in the two hand-maintained downstream repos
from the canonical kg cutpoints (CR-1):

  giready-apex/data/subdomains.json   band_sets.bowel_prep[*] + band_sets.flex_sig[*]
  schedule-giready/app.js             BOWEL_PREP_BANDS[*] label lb token

dosing.yaml (bowel-prep) and flex-sig procedure.yaml are the single source of
truth for the cutpoints; render.lb_phrase() is the single source for the lb
wording. This script reads those, derives each band's lb label, and rewrites
ONLY the lb (and, for flex-sig, kg) tokens in the two repos — preserving all
surrounding hand-authored structure so the diff is reviewable.

Idempotent: re-running with no cutpoint change is a no-op. Run from the skill:
    .venv/bin/python scripts/sync_band_labels.py            # write
    .venv/bin/python scripts/sync_band_labels.py --check    # fail if drifted
Wired into the meta-repo `make sites` / `make all`.
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required.\n")
    sys.exit(1)

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
from render import lb_phrase, lb_bounds  # noqa: E402

DOSING_PATH = SKILL_DIR / "data" / "dosing.yaml"
FLEXSIG_PROC = (Path.home() / ".claude" / "skills" / "flex-sig-handout-generator"
                / "data" / "procedure.yaml")
SYSTEM_ROOT = Path.home() / "Desktop" / "peds-gi-system"
APEX_JSON = SYSTEM_ROOT / "giready-apex" / "data" / "subdomains.json"
SCHEDULER_JS = SYSTEM_ROOT / "schedule-giready" / "app.js"


# ---------------------------------------------------------------------------
# Source: derive lb labels per band from the canonical cutpoints
# ---------------------------------------------------------------------------
def _bowel_prep_bands():
    bands = yaml.safe_load(DOSING_PATH.read_text(encoding="utf-8"))["bands"]
    return {b["id"]: b for b in bands}


def _flex_sig_bands():
    data = yaml.safe_load(FLEXSIG_PROC.read_text(encoding="utf-8"))
    bands = data["procedures"]["flex-sig"]["bands"]
    return {b["id"]: b for b in bands}


def _apex_flex_skill_id(apex_id):
    """apex flex_sig ids drop the 'kg' suffix the skill uses."""
    return apex_id + "kg"


def _scheduler_lb_token(band):
    """The parenthesized lb token the scheduler dropdown shows, e.g.
    "(33–45 lb)" or open-high "(≥112 lb)". Returns None for open-low bands
    (the scheduler intentionally shows no lb for the <15 kg rows)."""
    lo, hi = lb_bounds(band)
    if lo is not None and hi is not None:
        return f"({lo}–{hi} lb)"
    if hi is None and lo is not None:
        return f"(≥{lo} lb)"
    return None


# ---------------------------------------------------------------------------
# apex subdomains.json — line-scoped rewrite of band-set lb/kg tokens
# ---------------------------------------------------------------------------
_KEY_VAL = lambda key: re.compile(r'("' + key + r'"\s*:\s*")[^"]*(")')
_ID_RE = re.compile(r'"id"\s*:\s*"([^"]+)"')


def _set_key(line, key, value):
    return _KEY_VAL(key).sub(lambda m: m.group(1) + value + m.group(2), line, count=1)


def sync_apex(write):
    bp = _bowel_prep_bands()
    fs = _flex_sig_bands()
    lines = APEX_JSON.read_text(encoding="utf-8").splitlines(keepends=True)
    current_set = None
    out, changes = [], []
    for line in lines:
        if '"bowel_prep"' in line and "[" in line:
            current_set = "bowel_prep"
        elif '"flex_sig"' in line and "[" in line:
            current_set = "flex_sig"
        elif current_set and re.match(r"\s*[}\]]", line) and '"lb_label"' not in line:
            # leaving the array / object block
            if "]" in line:
                current_set = None

        if current_set and '"lb_label"' in line:
            m = _ID_RE.search(line)
            if not m:
                out.append(line)
                continue
            bid = m.group(1)
            if current_set == "bowel_prep":
                band = bp.get(bid)
                if band is None:
                    out.append(line)
                    continue
                new = _set_key(line, "lb_label", lb_phrase(band, "en", "plain"))
                new = _set_key(new, "lb_label_es", lb_phrase(band, "es", "plain"))
                # bowel-prep kg labels are unchanged by CR-1 — leave kg_label.
            else:  # flex_sig
                band = fs.get(_apex_flex_skill_id(bid))
                if band is None:
                    out.append(line)
                    continue
                new = _set_key(line, "lb_label", lb_phrase(band, "en", "plain"))
                new = _set_key(new, "lb_label_es", lb_phrase(band, "es", "plain"))
                new = _set_key(new, "kg_label", band["label_en"].split(" (")[0])
                new = _set_key(new, "kg_label_es", band["label_es"].split(" (")[0])
            if new != line:
                changes.append(f"  apex[{current_set}/{bid}]")
            out.append(new)
        else:
            out.append(line)

    if changes and write:
        APEX_JSON.write_text("".join(out), encoding="utf-8")
    return changes


# ---------------------------------------------------------------------------
# scheduler app.js — rewrite the lb token inside each BOWEL_PREP_BANDS label
# ---------------------------------------------------------------------------
_LB_PAREN = re.compile(r"\([^)]*\blb\b[^)]*\)")


def sync_scheduler(write):
    bp = _bowel_prep_bands()
    text = SCHEDULER_JS.read_text(encoding="utf-8")
    # Limit work to the BOWEL_PREP_BANDS array literal.
    start = text.find("const BOWEL_PREP_BANDS")
    end = text.find("]", start)
    if start == -1 or end == -1:
        sys.exit("ERROR: could not locate BOWEL_PREP_BANDS array in app.js")
    head, block, tail = text[:start], text[start:end], text[end:]

    changes = []
    out_lines = []
    for line in block.splitlines(keepends=True):
        m = re.search(r'id\s*:\s*"([^"]+)"', line)
        if m and m.group(1) in bp:
            token = _scheduler_lb_token(bp[m.group(1)])
            if token and _LB_PAREN.search(line):
                new = _LB_PAREN.sub(token, line, count=1)
                if new != line:
                    changes.append(f"  scheduler[{m.group(1)}] -> {token}")
                line = new
        out_lines.append(line)
    new_block = "".join(out_lines)

    if changes and write:
        SCHEDULER_JS.write_text(head + new_block + tail, encoding="utf-8")
    return changes


def main():
    ap = argparse.ArgumentParser(description="Sync derived lb labels into apex + scheduler")
    ap.add_argument("--check", action="store_true",
                    help="Exit non-zero if any label is out of date (no writes)")
    args = ap.parse_args()
    write = not args.check

    changes = []
    if APEX_JSON.exists():
        changes += sync_apex(write)
    else:
        print(f"  (skip) apex not found at {APEX_JSON}")
    if SCHEDULER_JS.exists():
        changes += sync_scheduler(write)
    else:
        print(f"  (skip) scheduler not found at {SCHEDULER_JS}")

    if not changes:
        print("✅ apex + scheduler lb labels already match the canonical cutpoints")
        return 0
    verb = "would update" if args.check else "updated"
    print(f"{'❌ DRIFT' if args.check else '✏️ '} {verb} {len(changes)} label(s):")
    for c in changes:
        print(c)
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
