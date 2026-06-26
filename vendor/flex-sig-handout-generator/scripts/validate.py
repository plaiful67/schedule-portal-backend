#!/usr/bin/env python3
"""Validation suite for the flex-sig-handout-generator skill.

Mirrors bowel-prep-generator/scripts/validate.py — same failure classes:
  - Unresolved {{PLACEHOLDER}} tokens that slip into rendered output
  - render.py errors against any band/lang/location combo
  - Template "orphan" placeholders (used in a template but never set in render.py)
  - Hardcoded canonical strings (drift from practice.yaml / procedure.yaml)
  - English residue in *.es.html templates

Usage:
    .venv/bin/python scripts/validate.py            # full suite (~45s)
    .venv/bin/python scripts/validate.py --quick    # lint only, skip renders (<1s)

Exit codes:
    0 — all checks passed
    1 — one or more checks failed (see report)

Wired into a git pre-commit hook via scripts/install_pre_commit.sh.
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL / "templates"
PROCEDURE_YAML = SKILL / "data" / "procedure.yaml"
PRACTICE_YAML = SKILL / "practice.yaml"
RENDER = SKILL / "scripts" / "render.py"
SCRIPTS = SKILL / "scripts"
PYTHON = SKILL / ".venv" / "bin" / "python"

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")


# ----------------------------------------------------------------------
# Inputs
# ----------------------------------------------------------------------
def load_bands():
    """Bands live at procedures.flex-sig.bands[*] in this skill's procedure.yaml."""
    with open(PROCEDURE_YAML) as f:
        data = yaml.safe_load(f)
    return data["procedures"]["flex-sig"]["bands"]


# ----------------------------------------------------------------------
# Check 1: Template placeholder lint
# ----------------------------------------------------------------------
def lint_template_placeholders():
    used = set()
    used_by_file = {}
    for tmpl in TEMPLATES.rglob("*.html"):
        content = tmpl.read_text(encoding="utf-8")
        matches = set(PLACEHOLDER_RE.findall(content))
        used.update(matches)
        if matches:
            used_by_file[tmpl.relative_to(SKILL)] = matches

    set_keys = set()
    for script in SCRIPTS.glob("*.py"):
        text = script.read_text(encoding="utf-8")
        set_keys.update(re.findall(r'"(\{\{[A-Z0-9_]+\}\})"\s*:', text))

    # Cross-skill shared partials (footer/legal, feedback bar, NPO table) live in
    # the meta repo; render.py's _load_shared_partials() resolves them dynamically,
    # so they never appear as static dict keys.
    shared_partials_dir = Path.home() / "peds-gi-prep-system" / "shared" / "partials"
    if shared_partials_dir.is_dir():
        for p in shared_partials_dir.glob("_*.html"):
            name = p.name[1:].split(".")[0]
            set_keys.add("{{PARTIAL_" + name.upper() + "}}")

    orphans = used - set_keys
    return used, set_keys, orphans, used_by_file


# ----------------------------------------------------------------------
# Check 2: Canonical-string drift
# ----------------------------------------------------------------------
def _load_practice_canonicals():
    if not PRACTICE_YAML.exists():
        return []
    with open(PRACTICE_YAML) as f:
        data = yaml.safe_load(f)
    out = []
    p = data.get("practice", {})
    if p.get("footer_en"):
        out.append(("practice.footer_en", p["footer_en"]))
    if p.get("footer_es"):
        out.append(("practice.footer_es", p["footer_es"]))
    return out


def _load_procedure_canonicals():
    with open(PROCEDURE_YAML) as f:
        data = yaml.safe_load(f)
    out = []
    for loc_id, loc in data.get("locations", {}).items():
        if loc.get("address"):
            out.append((f"locations.{loc_id}.address", loc["address"]))
        if loc.get("phone"):
            out.append((f"locations.{loc_id}.phone", loc["phone"]))
    return out


def lint_canonical_strings():
    canonicals = _load_practice_canonicals() + _load_procedure_canonicals()

    files_to_scan = list((SKILL / "templates").rglob("*.html"))
    for p in (SKILL / "scripts").glob("*.py"):
        if p.name != "validate.py":
            files_to_scan.append(p)

    findings = []
    for path in files_to_scan:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for label, value in canonicals:
            if value not in text:
                continue
            for i, line in enumerate(text.split("\n"), 1):
                if value in line and "canonical-ok" not in line:
                    findings.append((label, str(path.relative_to(SKILL)), i, line.strip()[:90]))
    return findings


# ----------------------------------------------------------------------
# Check 3: Translation-gap audit
# ----------------------------------------------------------------------
EN_RESIDUE_MARKERS = [
    "Nothing to eat",
    "the procedure",
    "Patient portal",
    "saline enema",
    "Stop this many hours",
    "before arrival",
    "What to wear",
    "What to bring",
    "Have your child",
    "with a sip",
    "1-2 hours",
]


def audit_translation_gaps():
    findings = []
    for path in (SKILL / "templates").rglob("*.es.html"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(text.split("\n"), 1):
            if "translation-ok" in line:
                continue
            for marker in EN_RESIDUE_MARKERS:
                if marker in line:
                    findings.append((str(path.relative_to(SKILL)), i, marker, line.strip()[:90]))
                    break
    return findings


def audit_meds_giready_reference():
    """Every flex-sig-*.html template should reference meds.giready.com (the
    phase-2 Medications callout points families there for any drug not shown
    in the handout). Returns list of file paths missing the reference."""
    missing = []
    for path in (SKILL / "templates").glob("flex-sig-*.html"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        # Landing pages don't carry the Medications callout (they redirect
        # to the band-specific handout).
        if "landing" in path.name:
            continue
        if "meds.giready.com" not in text:
            missing.append(str(path.relative_to(SKILL)))
    return missing


_LB_PAREN_RE = re.compile(r"\(([^)]*\b[Ll]b\b[^)]*)\)")
_INT_RE = re.compile(r"\d+")


def audit_weight_band_contiguity():
    """CR-1: the 3 flex-sig bands form a gapless kg-canonical partition and
    every hand-written lb figure is a derived edge of its band's bounds.

    Partition: distinct [kg_lo, kg_hi) intervals sorted by kg_lo must have
    kg_hi[n] == kg_lo[n+1] and derived lb_hi[n] + 1 == lb_lo[n+1]; first opens
    at 0, last opens (None). Derived-lb consistency: each integer next to "lb"
    in label_*/folder_* must be in {lo, lo-1, hi, hi+1}. Wording is free.
    Returns list of (band_id, reason).
    """
    sys.path.insert(0, str(SCRIPTS))
    from render import lb_bounds  # single source of derivation
    failures = []
    bands = load_bands()
    by_id = {b["id"]: b for b in bands}

    intervals = {}
    for b in bands:
        intervals.setdefault((b.get("kg_lo") or 0, b.get("kg_hi")), b["id"])
    ordered = sorted(intervals, key=lambda iv: iv[0])
    if ordered and ordered[0][0] != 0:
        failures.append((intervals[ordered[0]], f"partition does not start at 0 kg (kg_lo={ordered[0][0]})"))
    if ordered and ordered[-1][1] is not None:
        failures.append((intervals[ordered[-1]], f"partition does not end open (kg_hi={ordered[-1][1]})"))
    for iv1, iv2 in zip(ordered, ordered[1:]):
        id1, id2 = intervals[iv1], intervals[iv2]
        if iv1[1] != iv2[0]:
            failures.append((id1, f"kg gap/overlap: kg_hi={iv1[1]} != next {id2} kg_lo={iv2[0]}"))
        _, lb_hi1 = lb_bounds(by_id[id1])
        lb_lo2, _ = lb_bounds(by_id[id2])
        if lb_hi1 is None or lb_lo2 is None or lb_hi1 + 1 != lb_lo2:
            failures.append((id1, f"lb gap/overlap: lb_hi={lb_hi1} +1 != next {id2} lb_lo={lb_lo2}"))

    for b in bands:
        lo, hi = lb_bounds(b)
        allowed = {n for n in (lo, lo - 1 if lo is not None else None,
                               hi, hi + 1 if hi is not None else None) if n is not None}
        for field in ("label_en", "label_es", "folder_en", "folder_es"):
            for paren in _LB_PAREN_RE.findall(b.get(field) or ""):
                for num in _INT_RE.findall(paren):
                    if int(num) not in allowed:
                        failures.append((b["id"],
                                         f"{field}: lb figure {num} is not a derived edge {sorted(allowed)}"))
    return failures


# ----------------------------------------------------------------------
# Check 4: Render every band × lang × location combo
# ----------------------------------------------------------------------
def render_combo(band_id, lang, location, out_dir):
    cmd = [
        str(PYTHON), str(RENDER),
        "--out", str(out_dir),
        "--location", location,
        "--band", band_id,
        "--lang", lang,
        "--theme", "color",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.returncode, result.stdout, result.stderr


def scan_unresolved_in_dir(directory):
    findings = {}
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".html", ".htm", ".txt"}:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        matches = set(PLACEHOLDER_RE.findall(content))
        if matches:
            findings[str(path.relative_to(directory))] = matches
    return findings


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Validate flex-sig-handout-generator skill")
    ap.add_argument("--quick", action="store_true",
                    help="Skip render checks; lint templates only (<1s)")
    args = ap.parse_args()

    failures = []

    print("=" * 64)
    print("VALIDATION: flex-sig-handout-generator")
    print("=" * 64)

    # 1. Template placeholder lint
    print("\n[1/4] Template placeholder lint")
    used, set_keys, orphans, used_by_file = lint_template_placeholders()
    print(f"      {len(used)} unique placeholders used across {len(used_by_file)} templates")
    print(f"      {len(set_keys)} placeholders set across scripts/*.py")
    if orphans:
        print(f"      ❌ {len(orphans)} orphan placeholder(s):")
        for o in sorted(orphans):
            offenders = [str(f) for f, ms in used_by_file.items() if o in ms]
            example = offenders[0] if offenders else "?"
            more = f" (+{len(offenders)-1} more)" if len(offenders) > 1 else ""
            print(f"         {o}    e.g. {example}{more}")
        failures.append(f"orphan placeholders: {sorted(orphans)}")
    else:
        print("      ✅ all placeholders are set somewhere in scripts/")

    # 2. Canonical-string drift
    print("\n[2/4] Canonical-string drift")
    canon_findings = lint_canonical_strings()
    if canon_findings:
        from collections import defaultdict
        by_label = defaultdict(list)
        for label, fpath, lineno, line in canon_findings:
            by_label[label].append((fpath, lineno, line))
        print(f"      ❌ {len(canon_findings)} hardcoded canonical value(s):")
        for label, hits in sorted(by_label.items()):
            print(f"         {label}  ({len(hits)} occurrence{'s' if len(hits)!=1 else ''}):")
            for fpath, lineno, line in hits[:3]:
                print(f"           {fpath}:{lineno}  {line!r}")
            if len(hits) > 3:
                print(f"           ... +{len(hits)-3} more")
        print("      Hint: use a placeholder, or add `canonical-ok` on that line.")
        failures.append(f"canonical drift: {len(canon_findings)} hit(s)")
    else:
        print("      ✅ no canonical strings hardcoded outside their source files")

    # 3. Translation gaps
    print("\n[3/4] Translation gaps in *.es.html")
    trans_findings = audit_translation_gaps()
    if trans_findings:
        print(f"      ⚠ {len(trans_findings)} likely English-residue line(s):")
        for fpath, lineno, marker, line in trans_findings[:8]:
            print(f"         {fpath}:{lineno}  ['{marker}']  {line!r}")
        if len(trans_findings) > 8:
            print(f"         ... +{len(trans_findings)-8} more")
        print("      Hint: translate the line, or add `translation-ok` if intentional.")
        failures.append(f"translation gaps: {len(trans_findings)} hit(s)")
    else:
        print("      ✅ no English-residue markers found in ES templates")

    # 3b. meds.giready.com reference audit (phase 2)
    print("\n[3b] meds.giready.com reference in flex-sig-*.html")
    missing = audit_meds_giready_reference()
    if missing:
        print(f"      ❌ {len(missing)} template(s) missing the meds.giready.com reference:")
        for fpath in missing:
            print(f"         {fpath}")
        print("      Hint: every flex-sig handout template (mobile + print) should")
        print("      contain the Medications callout's verify-line + QR pointing at")
        print("      https://meds.giready.com.")
        failures.append(f"meds.giready.com reference: {len(missing)} template(s)")
    else:
        print("      ✅ every flex-sig handout template references meds.giready.com")

    # 3c. Weight-band contiguity + derived-lb consistency (CR-1)
    print("\n[3c] Weight-band contiguity (kg-canonical partition + derived lb)")
    contig_failures = audit_weight_band_contiguity()
    if contig_failures:
        print(f"      ❌ {len(contig_failures)} contiguity/derivation issue(s):")
        for band_id, reason in contig_failures:
            print(f"         {band_id}: {reason}")
        print("      Hint: bands are [kg_lo, kg_hi) intervals; lb labels derive "
              "from them. Fix the cutpoint or the lb figure so they agree.")
        failures.append(f"weight-band contiguity: {len(contig_failures)} hit(s)")
    else:
        print("      ✅ bands tile the axis with no gap/overlap; lb labels derive cleanly")

    if args.quick:
        return _summary(failures)

    # 4. Render every band × lang × location combo
    bands = load_bands()
    combos = [(b["id"], lang, loc) for b in bands for lang in ("en", "es") for loc in ("scc", "pmch")]
    print(f"\n[4/4] Rendering {len(combos)} band×lang×location combinations")
    print(f"      bands: {[b['id'] for b in bands]}")
    print()

    render_failures = 0
    unresolved_failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        for band_id, lang, location in combos:
            out_dir = tmp_root / f"{band_id}_{lang}_{location}"
            out_dir.mkdir(parents=True, exist_ok=True)
            rc, stdout, stderr = render_combo(band_id, lang, location, out_dir)
            tag = f"{band_id:12} {lang:2} {location}"
            if rc != 0:
                print(f"      ❌ render failed:        {tag}")
                if stderr:
                    print(f"             stderr tail: ...{stderr[-200:].strip()}")
                render_failures += 1
                failures.append(f"render failed: {band_id}/{lang}/{location}")
                continue

            findings = scan_unresolved_in_dir(out_dir)
            if findings:
                print(f"      ❌ unresolved placeholders: {tag}")
                for fname, matches in list(findings.items())[:2]:
                    print(f"             {fname}: {sorted(matches)}")
                unresolved_failures += 1
                failures.append(f"unresolved placeholders: {band_id}/{lang}/{location}")
            else:
                print(f"      ✅ ok                    {tag}")

    print()
    print(f"      render: {len(combos) - render_failures}/{len(combos)} succeeded")
    print(f"      placeholder scan: {len(combos) - render_failures - unresolved_failures}/{len(combos) - render_failures} clean")

    return _summary(failures)


def _summary(failures):
    print()
    print("=" * 64)
    if failures:
        print(f"❌ VALIDATION FAILED: {len(failures)} issue(s)")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
