#!/usr/bin/env python3
"""Validation suite for the egd-handout-generator skill.

Mirrors bowel-prep-generator/scripts/validate.py — same failure classes:
  - Unresolved {{PLACEHOLDER}} tokens that slip into rendered output
  - render.py errors against any lang/theme combo
  - Template "orphan" placeholders (used in a template but never set in render.py)
  - Hardcoded canonical strings (drift from practice.yaml / procedure.yaml)
  - English residue in *.es.html templates

Usage:
    .venv/bin/python scripts/validate.py            # full suite (~30s)
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
# Check 1: Template placeholder lint
# ----------------------------------------------------------------------
def lint_template_placeholders():
    """Find placeholders used in templates but never set in render.py / build_*.py."""
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
    """Per-location addresses and phones — any hardcoded copy is drift-prone."""
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
    """Find canonical values from practice.yaml / procedure.yaml that appear
    hardcoded in templates or scripts. Lines containing `canonical-ok` are
    skipped (use to whitelist intentional hardcodes — e.g. CSS @bottom-left
    that can't accept a placeholder)."""
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
    "Watch the prep video",
    "Patient portal",
    "Have your child",
    "with a sip",
    "Stop this many hours",
    "before arrival",
    "What to wear",
    "What to bring",
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


# ----------------------------------------------------------------------
# Check 4: Render every lang × theme × location combo
# ----------------------------------------------------------------------
def audit_meds_giready_reference():
    """Every egd-*.html template should reference meds.giready.com (the
    phase-2 Medications callout points families there for any drug not
    shown in the handout). Returns list of file paths missing the reference."""
    missing = []
    for path in (SKILL / "templates").glob("egd-*.html"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if "meds.giready.com" not in text:
            missing.append(str(path.relative_to(SKILL)))
    return missing


def render_combo(lang, theme, location, out_dir):
    cmd = [
        str(PYTHON), str(RENDER),
        "--out", str(out_dir),
        "--location", location,
        "--lang", lang,
        "--theme", theme,
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
    ap = argparse.ArgumentParser(description="Validate egd-handout-generator skill")
    ap.add_argument("--quick", action="store_true",
                    help="Skip render checks; lint templates only (<1s)")
    args = ap.parse_args()

    failures = []

    print("=" * 64)
    print("VALIDATION: egd-handout-generator")
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
    print("\n[3b] meds.giready.com reference in egd-*.html")
    missing = audit_meds_giready_reference()
    if missing:
        print(f"      ❌ {len(missing)} template(s) missing the meds.giready.com reference:")
        for fpath in missing:
            print(f"         {fpath}")
        print("      Hint: every EGD template should contain the Medications callout's")
        print("      verify-line + QR pointing at https://meds.giready.com.")
        failures.append(f"meds.giready.com reference: {len(missing)} template(s)")
    else:
        print("      ✅ every egd-*.html template references meds.giready.com")

    if args.quick:
        return _summary(failures)

    # 4. Render every lang × location combo (theme=color is enough for placeholder check)
    combos = [(lang, "color", loc) for lang in ("en", "es") for loc in ("scc", "pmch")]
    print(f"\n[4/4] Rendering {len(combos)} lang×location combinations")
    print()

    render_failures = 0
    unresolved_failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        for lang, theme, location in combos:
            out_dir = tmp_root / f"{lang}_{theme}_{location}"
            out_dir.mkdir(parents=True, exist_ok=True)
            rc, stdout, stderr = render_combo(lang, theme, location, out_dir)
            tag = f"{lang:2} {theme:11} {location}"
            if rc != 0:
                print(f"      ❌ render failed:        {tag}")
                if stderr:
                    print(f"             stderr tail: ...{stderr[-200:].strip()}")
                render_failures += 1
                failures.append(f"render failed: {lang}/{theme}/{location}")
                continue

            findings = scan_unresolved_in_dir(out_dir)
            if findings:
                print(f"      ❌ unresolved placeholders: {tag}")
                for fname, matches in list(findings.items())[:2]:
                    print(f"             {fname}: {sorted(matches)}")
                unresolved_failures += 1
                failures.append(f"unresolved placeholders: {lang}/{theme}/{location}")
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
