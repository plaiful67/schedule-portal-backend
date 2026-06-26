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
    # build_*_websites.py for mobile site rendering. Two legitimate setter
    # patterns: dict literals (`"{{KEY}}": value`) and subscript
    # assignment (`d["{{KEY}}"] = value`).
    set_keys = set()
    for script in SCRIPTS.glob("*.py"):
        text = script.read_text(encoding="utf-8")
        set_keys.update(re.findall(r'"(\{\{[A-Z0-9_]+\}\})"\s*[:\]]', text))

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

    # Cross-skill shared partials (footer/legal, feedback bar, NPO table) live in
    # the meta repo; render.py's _load_shared_partials() resolves them the same way.
    shared_partials_dir = Path.home() / "peds-gi-prep-system" / "shared" / "partials"
    if shared_partials_dir.is_dir():
        for p in shared_partials_dir.glob("_*.html"):
            name = p.name[1:].split(".")[0]
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


# render.py builds a `combined` (EGD+colonoscopy) print ONLY for the MiraLAX-family
# protocols — standard / infant / infant-enema (render_band, render.py ~L2518-2526).
# The scheduler-only alternative preps (SUPREP, CLENPIQ, lactulose) have no combined
# print template by design, so `combined × {those}` is unbuildable: render.py raises
# "Unknown protocol for combined variant". Sweeping them produced 10 expected-red
# failures that masked real regressions, so validate SKIPS (and logs) them instead.
COMBINED_BUILDABLE_PROTOCOLS = ("standard", "infant", "infant-enema")


def _combo_buildable(protocol: str, variant: str) -> bool:
    """Mirror render.py's matrix boundary: only the MiraLAX-family protocols have a
    combined print template. Everything is buildable in the `standard` variant."""
    if variant == "combined":
        return protocol in COMBINED_BUILDABLE_PROTOCOLS
    return True


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


def audit_meds_giready_paired_with_medications_drugs():
    """Every template that references {{HTML_MEDICATIONS_DRUGS}} must also
    reference meds.giready.com (the verify-line + QR row appended in phase 2).
    This catches the regression where a new template variant is created
    without the meds.giready.com reference — patients should see the
    "verify at meds.giready.com" pointer on every handout that lists
    medications to stop. Returns list of (file_relpath,) tuples for failures."""
    missing = []
    for path in (SKILL / "templates").rglob("*.html"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if "{{HTML_MEDICATIONS_DRUGS}}" not in text:
            continue
        # Standard- and infant-non-print stubs use a simple <li> list, not a
        # callout — phase 2 deferred those (DOCX-stub HTML, not patient-facing
        # via the site/PDF path). Mark with `meds-giready-exempt` in a comment
        # to skip this check.
        if "meds-giready-exempt" in text:
            continue
        if "meds.giready.com" not in text:
            missing.append(str(path.relative_to(SKILL)))
    return missing


def audit_partner_variant_bands():
    """Partner-variant bands (id pattern `{canonical-id}-{partner-slug}`)
    must satisfy two invariants:
      1. A canonical band with the prefix exists in the same dosing.yaml.
      2. The variant band is marked `public: false` so it doesn't leak to
         the public mobile sites.

    Phase 1 ships with no partner-variant bands, so this is a no-op pass.
    The check activates the moment the first partner is onboarded.

    Returns list of (band_id, reason) tuples for failures.
    """
    # Known protocol suffixes that are NOT partner slugs — these are the
    # already-shipped variant patterns (lactulose / clenpiq / enema). Any
    # other trailing token after a canonical band-id prefix is treated as a
    # partner slug.
    KNOWN_PROTOCOL_SUFFIXES = {"lact", "enema"}

    bands = load_bands()
    by_id = {b["id"]: b for b in bands}

    failures = []
    for band in bands:
        band_id = band["id"]
        if "-" not in band_id:
            continue
        # Iterate suffix-length candidates so multi-segment canonical ids
        # like "under-15" are handled (a child "under-15-dunn" must split
        # off the trailing "dunn", not the trailing "15-dunn").
        suffix_candidate = band_id.rsplit("-", 1)[-1]
        prefix_candidate = band_id[: -(len(suffix_candidate) + 1)]
        if suffix_candidate in KNOWN_PROTOCOL_SUFFIXES:
            continue
        # If the prefix isn't itself a canonical band, this is just a
        # canonical band whose id happens to contain a hyphen (e.g. "15-20",
        # "under-15") — skip it.
        if prefix_candidate not in by_id:
            continue
        # Looks like a partner-variant band. Enforce invariants.
        if band.get("public") is not False:
            failures.append((band_id, "must declare `public: false`"))
        if band.get("protocol") != "standard":
            failures.append((
                band_id,
                f"partner variants must use protocol: standard (got {band.get('protocol')!r})",
            ))
    return failures


def audit_shopping_totals():
    """The Plan-Ahead shopping row promises "enough for big prep with rescue",
    so for every standard-protocol band with a rescue plan:
      1. contingency_total_caps must equal miralax_capfuls + evening + morning
         rescue caps (render.py sums the parts; the yaml total is the
         cross-check that catches a half-edited band).
      2. contingency_total_grams must equal contingency_total_caps * 17
         (17 g per capful — the rescue/shopping convention; the big-prep
         miralax_grams rounds differently per band and is NOT checked here).
      3. miralax_shopping_note_{en,es} must be non-empty — the shopping row
         prints the bottle hint unconditionally, so a missing hint renders a
         dangling "rescue —" fragment.

    Returns list of (band_id, reason) tuples for failures.
    """
    failures = []
    for band in load_bands():
        if band.get("protocol") != "standard":
            continue
        if "contingency_evening_caps" not in band:
            continue
        band_id = band["id"]
        expected_caps = (band["miralax_capfuls"]
                         + band["contingency_evening_caps"]
                         + band["contingency_morning_caps"])
        total_caps = band.get("contingency_total_caps")
        if total_caps != expected_caps:
            failures.append((band_id,
                             f"contingency_total_caps={total_caps} but big prep + rescue = {expected_caps}"))
        total_grams = band.get("contingency_total_grams")
        if total_grams != (total_caps or 0) * 17:
            failures.append((band_id,
                             f"contingency_total_grams={total_grams} but {total_caps} caps x 17 g = {(total_caps or 0) * 17}"))
        for lang in ("en", "es"):
            if not (band.get(f"miralax_shopping_note_{lang}") or "").strip():
                failures.append((band_id, f"missing miralax_shopping_note_{lang} (bottle-size hint)"))
    return failures


# Parenthesized group that contains an "lb"/"Lb" token (the lb display portion
# of a label), and a bare-integer matcher to pull edge numbers out of it.
_LB_PAREN_RE = re.compile(r"\(([^)]*\b[Ll]b\b[^)]*)\)")
_INT_RE = re.compile(r"\d+")


def _render_lb_helpers():
    """Import the single-source lb derivation helpers from render.py."""
    sys.path.insert(0, str(SCRIPTS))
    from render import lb_bounds, select_band, LB_PER_KG  # noqa: E402
    return lb_bounds, select_band, LB_PER_KG


def audit_weight_band_contiguity():
    """CR-1: weight bands form a gapless, non-overlapping kg-canonical partition,
    and every hand-written lb figure is a legal edge of its band's derived bounds.

    1. Partition — the PRIMARY bands (protocol infant/infant-enema/standard) tile
       the axis: distinct intervals sorted by kg_lo must satisfy, for each
       adjacent pair, kg_hi[n] == kg_lo[n+1] AND derived lb_hi[n] + 1 ==
       lb_lo[n+1]. The first interval must open at 0 and the last at None so the
       whole 0..∞ range is covered.
    2. Derived-lb consistency — for every band carrying cutpoints, each integer
       sitting next to "lb" inside its label/heading/folder strings must be a
       derived edge {lo, lo-1, hi, hi+1}. Catches a cutpoint changed without the
       label (or vice-versa); wording style ("90-111 lb" / ">111 lb" / "68+ lb")
       is free.

    Returns list of (band_id, reason).
    """
    lb_bounds, _select, _K = _render_lb_helpers()
    failures = []
    bands = load_bands()
    by_id = {b["id"]: b for b in bands}

    # ---- 1. partition over the primary (tiling) bands ----
    PRIMARY = {"infant", "infant-enema", "standard"}
    intervals = {}  # (kg_lo, kg_hi) -> first band id with that interval
    for b in bands:
        if b.get("protocol") in PRIMARY:
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

    # ---- 2. derived-lb consistency on every band with cutpoints ----
    for b in bands:
        if "kg_lo" not in b and "kg_hi" not in b:
            continue
        lo, hi = lb_bounds(b)
        allowed = {n for n in (lo, lo - 1 if lo is not None else None,
                               hi, hi + 1 if hi is not None else None) if n is not None}
        for field in ("label_en", "label_es", "docx_heading_en",
                      "docx_heading_es", "folder_en", "folder_es"):
            for paren in _LB_PAREN_RE.findall(b.get(field) or ""):
                for num in _INT_RE.findall(paren):
                    if int(num) not in allowed:
                        failures.append((b["id"],
                                         f"{field}: lb figure {num} is not a derived edge {sorted(allowed)}"))
    return failures


def audit_band_selection_sweep():
    """CR-1: sweep kg (0..100, 0.5 steps) and lb (1..220) through select_band()
    over the distinct primary intervals — every weight must land in exactly one
    band. select_band() is gap-detecting (raises on no match); the contiguity
    audit above rules out overlap, so success here == exactly-one coverage.

    Returns list of failure strings.
    """
    _bounds, select_band, LB_PER_KG = _render_lb_helpers()
    bands = load_bands()
    PRIMARY = {"infant", "infant-enema", "standard"}
    seen, prim = set(), []
    for b in bands:
        if b.get("protocol") in PRIMARY:
            iv = (b.get("kg_lo") or 0, b.get("kg_hi"))
            if iv not in seen:
                seen.add(iv)
                prim.append(b)
    failures = []
    steps = [i * 0.5 for i in range(0, 201)]  # 0.0 .. 100.0 kg
    for w in steps:
        try:
            select_band(round(w, 2), prim)
        except ValueError:
            failures.append(f"no band for {w:g} kg")
    for lb in range(1, 221):
        kg = lb / LB_PER_KG
        try:
            select_band(kg, prim)
        except ValueError:
            failures.append(f"no band for {lb} lb (~{kg:.2f} kg)")
    return failures


def audit_calendar_events(update_golden=False):
    """Check [3e]: calendar-export event integrity.

    Builds the full event set for every band × lang × location × family and
    asserts schema invariants, prose↔structured-twin consistency, and golden
    parity (tests/golden/calendar_events.json — refresh with --update-golden
    after an intentional change and review the diff).

    Returns (failures, golden_msg) where failures is a list of strings.
    """
    sys.path.insert(0, str(SCRIPTS))
    import render  # noqa: E402

    HHMM_RE = re.compile(r"^\d{2}:\d{2}$")
    data = render.load_dosing()
    locations = data["locations"]
    cal = data.get("calendar", {})
    failures = []

    # --- prose ↔ structured-twin cross-checks --------------------------
    # clears_start_hhmm must match the "After 2:00 PM" prose in the standard
    # mobile templates (the canonical home of that time).
    clears12 = render._12h(cal["clears_start_hhmm"])
    std_tmpl = (TEMPLATES / "colonoscopy-mobile.en.html").read_text(encoding="utf-8")
    if f"After {clears12}" not in std_tmpl:
        failures.append(f"calendar.clears_start_hhmm ({clears12}) not found in "
                        "colonoscopy-mobile.en.html prose")
    # CLENPIQ/SUPREP dose-1 window twins must match dose1_window_en prose.
    for band in data["bands"]:
        if "dose1_window_start_hhmm" not in band:
            continue
        prose = band.get("dose1_window_en", "")
        for key in ("dose1_window_start_hhmm", "dose1_window_end_hhmm"):
            t12 = render._12h(band[key])
            if t12 not in prose:
                failures.append(f"{band['id']}.{key} ({t12}) does not match "
                                f"dose1_window_en prose ({prose!r})")
    # Infant feeding-cutoff twins must match the fasting-rules prose in the
    # corresponding infant template.
    for cuts_key, tmpl_name in (("infant_cutoffs", "colonoscopy-mobile-infant.en.html"),
                                ("infant_enema_cutoffs", "colonoscopy-mobile-infant-enema.en.html")):
        tmpl = (TEMPLATES / tmpl_name).read_text(encoding="utf-8")
        for cut_id, cut in cal.get(cuts_key, {}).items():
            t12 = render._12h(cut["hhmm"])
            if t12 not in tmpl:
                failures.append(f"calendar.{cuts_key}.{cut_id} ({t12}) not found "
                                f"in {tmpl_name} prose")

    # --- schema checks + golden snapshot --------------------------------
    snapshot = {}
    for family in ("colonoscopy", "combined"):
        snapshot[family] = {}
        for band in data["bands"]:
            snapshot[family][band["id"]] = {}
            for loc_id, loc in locations.items():
                snapshot[family][band["id"]][loc_id] = {}
                for lang in ("en", "es"):
                    tag = f"{family}/{band['id']}/{loc_id}/{lang}"
                    try:
                        events = render.build_calendar_events(band, lang, loc, family)
                    except Exception as e:
                        failures.append(f"{tag}: build_calendar_events raised {e!r}")
                        continue
                    if not events:
                        failures.append(f"{tag}: empty event list")
                        continue
                    seen_loc_ids = set()
                    for ev in events:
                        eid = ev.get("id", "?")
                        etag = f"{tag}:{eid}"
                        forms = sum([bool(ev.get("allDay")),
                                     "offsetMin" in ev,
                                     ("start" in ev and not ev.get("allDay")
                                      and "offsetMin" not in ev)])
                        if forms != 1:
                            failures.append(f"{etag}: must use exactly one time form")
                        for key in ("start", "end"):
                            if key in ev and not HHMM_RE.match(ev[key]):
                                failures.append(f"{etag}: {key}={ev[key]!r} not HH:MM")
                        if "day" in ev and not (-7 <= ev["day"] <= 0):
                            failures.append(f"{etag}: day={ev['day']} outside [-7, 0]")
                        if "start" in ev and "end" in ev and ev["end"] <= ev["start"]:
                            failures.append(f"{etag}: window end <= start")
                        if "offsetEndMin" in ev and ev["offsetEndMin"] <= ev["offsetMin"]:
                            failures.append(f"{etag}: offsetEndMin <= offsetMin")
                        for key in ("titleDiscreet", "titleDetailed", "desc"):
                            val = ev.get(key, "")
                            if not val:
                                failures.append(f"{etag}: empty {key}")
                            elif "<" in val or "{{" in val:
                                failures.append(f"{etag}: {key} contains markup: {val[:60]!r}")
                        if "loc" in ev:
                            seen_loc_ids.add(eid)
                    if not {"arrival", "procedure"} <= seen_loc_ids:
                        failures.append(f"{tag}: arrival/procedure missing loc field")
                    snapshot[family][band["id"]][loc_id][lang] = events

    golden_path = SKILL / "tests" / "golden" / "calendar_events.json"
    golden_msg = ""
    if update_golden:
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(
            __import__("json").dumps(snapshot, indent=1, ensure_ascii=False,
                                     sort_keys=True) + "\n",
            encoding="utf-8")
        golden_msg = f"golden updated: {golden_path.relative_to(SKILL)}"
    elif golden_path.exists():
        import json as _json
        expected = _json.loads(golden_path.read_text(encoding="utf-8"))
        if expected != snapshot:
            failures.append("calendar events differ from golden snapshot — "
                            "review the change, then refresh with "
                            "`validate.py --quick --update-golden`")
    else:
        failures.append(f"missing golden file {golden_path.relative_to(SKILL)} — "
                        "create with `validate.py --quick --update-golden`")
    return failures, golden_msg


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
    ap.add_argument("--update-golden", action="store_true",
                    help="Rewrite tests/golden/calendar_events.json from current output")
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

    # ------------------------------------------------------------------
    # 3c. Partner-variant band integrity (no-op until first partner ships)
    # ------------------------------------------------------------------
    print("\n[3c] Partner-variant band integrity")
    partner_failures = audit_partner_variant_bands()
    if partner_failures:
        print(f"      ❌ {len(partner_failures)} partner-variant band issue(s):")
        for band_id, reason in partner_failures:
            print(f"         {band_id}: {reason}")
        print("      Hint: see dosing.yaml header + bowel_prep.py PARTNER_OVERRIDE_PHYSICIANS.")
        failures.append(f"partner-variant integrity: {len(partner_failures)} hit(s)")
    else:
        print("      ✅ no partner-variant integrity issues")

    # ------------------------------------------------------------------
    # 3d. Shopping totals cover big prep + rescue
    # ------------------------------------------------------------------
    print("\n[3d] Shopping totals (big prep + rescue) consistency")
    shopping_failures = audit_shopping_totals()
    if shopping_failures:
        print(f"      ❌ {len(shopping_failures)} shopping-total issue(s):")
        for band_id, reason in shopping_failures:
            print(f"         {band_id}: {reason}")
        print("      Hint: contingency_total_* must equal big prep + rescue; "
              "every standard band needs miralax_shopping_note_{en,es}.")
        failures.append(f"shopping totals: {len(shopping_failures)} hit(s)")
    else:
        print("      ✅ shopping totals = big prep + rescue on every standard band")

    # ------------------------------------------------------------------
    # 3b. meds.giready.com pairing audit (phase 2)
    # ------------------------------------------------------------------
    print("\n[3b] meds.giready.com pairing with {{HTML_MEDICATIONS_DRUGS}}")
    pairing_missing = audit_meds_giready_paired_with_medications_drugs()
    if pairing_missing:
        print(f"      ❌ {len(pairing_missing)} template(s) reference {{HTML_MEDICATIONS_DRUGS}}")
        print("         but are missing the meds.giready.com verify line:")
        for fpath in pairing_missing:
            print(f"           {fpath}")
        print("      Hint: append the meds-verify-row HTML inside the Medications callout,")
        print("      or add an HTML comment containing `meds-giready-exempt` if intentional.")
        failures.append(f"meds.giready.com pairing: {len(pairing_missing)} template(s)")
    else:
        print("      ✅ every template using {{HTML_MEDICATIONS_DRUGS}} references meds.giready.com")

    # ------------------------------------------------------------------
    # 3e. Calendar-export event integrity + golden snapshot
    # ------------------------------------------------------------------
    print("\n[3e] Calendar-export events (schema + prose twins + golden)")
    cal_failures, golden_msg = audit_calendar_events(update_golden=args.update_golden)
    if golden_msg:
        print(f"      ✏️  {golden_msg}")
    if cal_failures:
        print(f"      ❌ {len(cal_failures)} calendar-event issue(s):")
        for msg in cal_failures[:10]:
            print(f"         {msg}")
        if len(cal_failures) > 10:
            print(f"         ... +{len(cal_failures)-10} more")
        failures.append(f"calendar events: {len(cal_failures)} hit(s)")
    else:
        print("      ✅ calendar events valid for every band×lang×location×family")

    # ------------------------------------------------------------------
    # 3f. Weight-band contiguity + derived-lb consistency (CR-1)
    # ------------------------------------------------------------------
    print("\n[3f] Weight-band contiguity (kg-canonical partition + derived lb)")
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

    # ------------------------------------------------------------------
    # 3g. Band-selection sweep — every kg/lb weight lands in exactly one band
    # ------------------------------------------------------------------
    print("\n[3g] Band-selection sweep (kg 0–100 ×0.5, lb 1–220)")
    sweep_failures = audit_band_selection_sweep()
    if sweep_failures:
        print(f"      ❌ {len(sweep_failures)} weight(s) match no band:")
        for msg in sweep_failures[:10]:
            print(f"         {msg}")
        if len(sweep_failures) > 10:
            print(f"         ... +{len(sweep_failures)-10} more")
        failures.append(f"band-selection sweep: {len(sweep_failures)} hit(s)")
    else:
        print("      ✅ every swept kg/lb weight selects exactly one band")

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

    all_combos = [(b, lang, v) for b in bands for lang in ("en", "es") for v in variants]
    combos = [(b["id"], lang, v) for (b, lang, v) in all_combos
              if _combo_buildable(b.get("protocol", ""), v)]
    skipped = [(b["id"], lang, v) for (b, lang, v) in all_combos
               if not _combo_buildable(b.get("protocol", ""), v)]
    print(f"\n[4/5] Rendering {len(combos)} band\u00d7lang\u00d7variant combinations")
    print(f"      bands: {[b['id'] for b in bands]}")
    print(f"      langs: en, es")
    print(f"      variants: {variants}")
    if skipped:
        print(f"      ⏭  skipping {len(skipped)} unbuildable combo(s): combined × scheduler-only prep")
        print(f"         (SUPREP/CLENPIQ/lactulose have no combined print template — by design;")
        print(f"          render.py render_band raises 'Unknown protocol for combined variant')")
        for band_id, lang, variant in skipped:
            print(f"      ⏭  skip (unbuildable):   {band_id:18} {lang:2} {variant:8}")
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
