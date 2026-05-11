#!/usr/bin/env python3
"""Validation suite for the bowel-prep-generator skill.

Catches the silent-failure classes of bug we've hit in iteration:
  - Unresolved {{PLACEHOLDER}} tokens that slip into rendered output
  - render.py errors against any band/lang/variant combo
  - Missing expected output files
  - Template "orphan" placeholders (used in a template but never set
    anywhere in render.py — usually a typo)

Usage:
    .venv/bin/python scripts/validate.py            # full suite (~2-3 min)
    .venv/bin/python scripts/validate.py --quick    # lint only, skip renders (<1 sec)

Exit codes:
    0 — all checks passed
    1 — one or more checks failed (see report)

Designed to be wired into a git pre-commit hook (see
scripts/install_pre_commit.sh and docs/VALIDATION.md).
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
DOSING = SKILL / "data" / "dosing.yaml"
RENDER = SKILL / "scripts" / "render.py"
SCRIPTS = SKILL / "scripts"
PYTHON = SKILL / ".venv" / "bin" / "python"

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")


# ----------------------------------------------------------------------
# Inputs
# ----------------------------------------------------------------------
def load_bands():
    with open(DOSING) as f:
        data = yaml.safe_load(f)
    return data["bands"]


# ----------------------------------------------------------------------
# Check 1: Template placeholder lint
# ----------------------------------------------------------------------
def lint_template_placeholders():
    """Find placeholders used in templates but never set in render.py.

    Scans every templates/*.html for {{...}} patterns, then cross-references
    against the dictionary keys assigned in render.py. Returns the set of
    "orphan" placeholders (used somewhere but never set).

    Forward-compatible: any new placeholder added to a template just needs
    a corresponding setter in render.py to validate clean.
    """
    used = set()
    used_by_file = {}
    for tmpl in TEMPLATES.rglob("*.html"):
        content = tmpl.read_text(encoding="utf-8")
        matches = set(PLACEHOLDER_RE.findall(content))
        used.update(matches)
        if matches:
            used_by_file[tmpl.relative_to(SKILL)] = matches

    # Scan ALL scripts/ for placeholder setters — render.py for PDF/DOCX,
    # build_*_websites.py for mobile site rendering. Keys appear as
    # "{{KEY}}":  in dict literals.
    set_keys = set()
    for script in SCRIPTS.glob("*.py"):
        text = script.read_text(encoding="utf-8")
        set_keys.update(re.findall(r'"(\{\{[A-Z0-9_]+\}\})"\s*:', text))

    # Forward-compat: infer dynamically-generated PARTIAL_* tokens from the
    # templates/partials/ directory if it exists. Convention:
    # _<name>.<lang>.html  ->  {{PARTIAL_<NAME_UPPER>}}
    # render.py's _load_partials() generates these dynamically, so they
    # don't appear as static dict keys in any script.
    partials_dir = TEMPLATES / "partials"
    if partials_dir.is_dir():
        for p in partials_dir.glob("_*.html"):
            stem = p.name[1:]                # drop leading "_"
            name = stem.split(".")[0]        # drop ".en.html" / ".es.html"
            set_keys.add("{{PARTIAL_" + name.upper() + "}}")

    orphans = used - set_keys
    return used, set_keys, orphans, used_by_file


# ----------------------------------------------------------------------
# Check 2 + 3: Render every combo
# ----------------------------------------------------------------------
def render_combo(band_id, lang, variant, out_dir):
    cmd = [
        str(PYTHON), str(RENDER),
        "--out", str(out_dir),
        "--location", "scc",
        "--band", band_id,
        "--lang", lang,
        "--format", "pdf-print",
        "--theme", "color",
    ]
    if variant and variant != "standard":
        cmd.extend(["--variant", variant])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    return result.returncode, result.stdout, result.stderr


def scan_unresolved_in_dir(directory):
    """Return {file_relpath: {placeholders found}} for any .html/.txt in dir."""
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
# Check 4: Canonical-string lint
# ----------------------------------------------------------------------
# Catches drift between practice.yaml / dosing.yaml canonical values and
# hardcoded copies that appear in templates or render scripts. The classic
# failure mode this prevents: when practice.yaml.footer_{en,es} changes,
# templates that hardcoded the old value (e.g. a CSS @bottom-left rule)
# silently keep the stale string. Use a `canonical-ok` marker (HTML comment
# or Python comment on the same line) to mark known-legitimate hardcodes.

PRACTICE_YAML = SKILL / "practice.yaml"


def _load_practice_canonicals():
    """Return list of (label, value) canonical strings worth linting."""
    with open(PRACTICE_YAML) as f:
        data = yaml.safe_load(f)
    out = []
    p = data.get("practice", {})
    if p.get("footer_en"):
        out.append(("practice.footer_en", p["footer_en"]))
    if p.get("footer_es"):
        out.append(("practice.footer_es", p["footer_es"]))
    return out


def _load_dosing_canonicals():
    """Per-location addresses and phones — any hardcoded copy is drift-prone."""
    with open(DOSING) as f:
        data = yaml.safe_load(f)
    out = []
    for loc_id, loc in data.get("locations", {}).items():
        if loc.get("address"):
            out.append((f"locations.{loc_id}.address", loc["address"]))
        if loc.get("phone"):
            out.append((f"locations.{loc_id}.phone", loc["phone"]))
    return out


def lint_canonical_strings():
    """Find canonical values from practice.yaml/dosing.yaml that appear hardcoded
    in templates or scripts. Returns list of (label, file, lineno, line) tuples.
    Lines containing the marker `canonical-ok` are skipped (use this to whitelist
    legitimate hardcodes — e.g. a CSS @bottom-left rule that can't accept a
    placeholder yet)."""
    canonicals = _load_practice_canonicals() + _load_dosing_canonicals()

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
# Check 5: Translation-gap audit (English residue in *.es.html)
# ----------------------------------------------------------------------
# Catches the slipups where a Spanish template still has English text in
# places that should have been translated. Maintains a small denylist of
# distinctive English markers; lines tagged `translation-ok` are allowed
# through (e.g. brand names like "Gatorade" or English product names).

EN_RESIDUE_MARKERS = [
    "shake well",
    "with a sip",
    "Nothing to eat",
    "the procedure",  # esp. "8 hours before the procedure" English remnant
    "tablespoon",
    "Watch the prep video",  # would have been translated to "Vea el video..."
    "Patient portal",        # capitalized English
    "of Gatorade",           # most ES uses should be "de Gatorade"
    " of clear liquid",
    "until finished",
    "Take the bottle",
    "Have your child drink",
]


def audit_translation_gaps():
    """Scan *.es.html for English-residue markers. Returns list of
    (file, lineno, marker, line) tuples."""
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
                    break  # one finding per line is enough
    return findings


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Validate bowel-prep-generator skill")
    ap.add_argument("--quick", action="store_true",
                    help="Skip render checks; lint templates only (<1 sec)")
    ap.add_argument("--bands", help="Comma-separated band ids to limit render check (default: all)")
    ap.add_argument("--variants", default="standard,combined",
                    help="Comma-separated variants to test (default: standard,combined)")
    args = ap.parse_args()

    failures = []

    print("=" * 64)
    print("VALIDATION: bowel-prep-generator")
    print("=" * 64)

    # ------------------------------------------------------------------
    # 1. Template placeholder lint (orphan check)
    # ------------------------------------------------------------------
    print("\n[1/5] Template placeholder lint")
    used, set_keys, orphans, used_by_file = lint_template_placeholders()
    print(f"      {len(used)} unique placeholders used across {len(used_by_file)} templates")
    print(f"      {len(set_keys)} placeholders set across scripts/*.py")
    if orphans:
        print(f"      \u274c {len(orphans)} orphan placeholder(s) "
              "(used in template but never set in render.py):")
        for o in sorted(orphans):
            offenders = [str(f) for f, ms in used_by_file.items() if o in ms]
            example = offenders[0] if offenders else "?"
            more = f" (+{len(offenders)-1} more)" if len(offenders) > 1 else ""
            print(f"         {o}    e.g. {example}{more}")
        failures.append(f"orphan placeholders: {sorted(orphans)}")
    else:
        print("      \u2705 all placeholders are set somewhere in render.py")

    # ------------------------------------------------------------------
    # 2. Canonical-string lint (drift between practice.yaml and templates)
    # ------------------------------------------------------------------
    print("\n[2/5] Canonical-string drift")
    canon_findings = lint_canonical_strings()
    if canon_findings:
        print(f"      ❌ {len(canon_findings)} hardcoded canonical value(s) found:")
        # Group by label for readable output
        from collections import defaultdict
        by_label = defaultdict(list)
        for label, fpath, lineno, line in canon_findings:
            by_label[label].append((fpath, lineno, line))
        for label, hits in sorted(by_label.items()):
            print(f"         {label}  ({len(hits)} occurrence{'s' if len(hits)!=1 else ''}):")
            for fpath, lineno, line in hits[:3]:
                print(f"           {fpath}:{lineno}  {line!r}")
            if len(hits) > 3:
                print(f"           ... +{len(hits)-3} more")
        print("      Hint: replace the literal with a placeholder, or add `canonical-ok` "
              "in a comment on that line if hardcoding is unavoidable.")
        failures.append(f"canonical drift: {len(canon_findings)} hit(s)")
    else:
        print("      ✅ no canonical strings hardcoded outside their source files")

    # ------------------------------------------------------------------
    # 3. Translation-gap audit (English residue in *.es.html)
    # ------------------------------------------------------------------
    print("\n[3/5] Translation gaps in *.es.html")
    trans_findings = audit_translation_gaps()
    if trans_findings:
        print(f"      ⚠ {len(trans_findings)} likely English-residue line(s) in ES templates:")
        for fpath, lineno, marker, line in trans_findings[:8]:
            print(f"         {fpath}:{lineno}  ['{marker}']  {line!r}")
        if len(trans_findings) > 8:
            print(f"         ... +{len(trans_findings)-8} more")
        print("      Hint: translate the line, or add `translation-ok` in a comment "
              "if the English is intentional (brand name, etc.).")
        failures.append(f"translation gaps: {len(trans_findings)} hit(s)")
    else:
        print("      ✅ no English-residue markers found in ES templates")

    if args.quick:
        return _summary(failures)

    # ------------------------------------------------------------------
    # 4 + 5. Render every combo + scan rendered output for unresolved
    # ------------------------------------------------------------------
    bands = load_bands()
    if args.bands:
        wanted = set(args.bands.split(","))
        bands = [b for b in bands if b["id"] in wanted]
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]

    combos = [(b["id"], lang, v) for b in bands for lang in ("en", "es") for v in variants]
    print(f"\n[4/5] Rendering {len(combos)} band\u00d7lang\u00d7variant combinations")
    print(f"      bands: {[b['id'] for b in bands]}")
    print(f"      langs: en, es")
    print(f"      variants: {variants}")
    print()

    render_failures = 0
    unresolved_failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        for band_id, lang, variant in combos:
            out_dir = tmp_root / f"{band_id}_{lang}_{variant}"
            out_dir.mkdir(parents=True, exist_ok=True)
            rc, stdout, stderr = render_combo(band_id, lang, variant, out_dir)
            tag = f"{band_id:18} {lang:2} {variant:8}"
            if rc != 0:
                print(f"      \u274c render failed:        {tag}")
                if stderr:
                    print(f"             stderr tail: ...{stderr[-200:].strip()}")
                render_failures += 1
                failures.append(f"render failed: {band_id}/{lang}/{variant}")
                continue

            findings = scan_unresolved_in_dir(out_dir)
            if findings:
                print(f"      \u274c unresolved placeholders: {tag}")
                for fname, matches in list(findings.items())[:2]:
                    print(f"             {fname}: {sorted(matches)}")
                unresolved_failures += 1
                failures.append(f"unresolved placeholders: {band_id}/{lang}/{variant}")
            else:
                print(f"      \u2705 ok                    {tag}")

    print()
    print(f"      render: {len(combos) - render_failures}/{len(combos)} succeeded")
    print(f"      placeholder scan: {len(combos) - render_failures - unresolved_failures}/{len(combos) - render_failures} clean")

    return _summary(failures)


def _summary(failures):
    print()
    print("=" * 64)
    if failures:
        print(f"\u274c VALIDATION FAILED: {len(failures)} issue(s)")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("\u2705 ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
