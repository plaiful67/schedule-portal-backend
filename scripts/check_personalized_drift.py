#!/usr/bin/env python3
"""Provenance drift gate for the scheduler's personalized print templates.

WHY
---
The backend's `app/templates/**/*-personalized.*.html` files are derived from
the canonical handout templates in the authoring skills (vendored under
`vendor/<skill>/templates/`). Most are hand-maintained *forks*, so when a
canonical template changes, the fork silently keeps the old (possibly clinically
stale) content. This gate makes that drift LOUD.

WHAT IT DOES (and does NOT)
---------------------------
It is a *provenance* check, not a content-equality check. For each personalized
template it records the sha256 of the canonical it was last synced from
(`scripts/personalized_provenance.json`). On `check`, it recomputes each
canonical's sha and FAILS if any changed since the recorded sync — naming the
exact personalized file that now needs review.

- It catches NEW drift: a canonical edited after the last sync.
- It does NOT assert the fork's *content* currently equals canonical+transform.
  Pre-existing fork staleness is tracked separately (the staged "de-fork", see
  docs/PERSONALIZED_TEMPLATE_DRIFT.md) and is out of scope here.

ORPHANS
-------
A personalized template with NO skill canonical is an ORPHAN — a scheduler-served
handout with no single source of truth in the authoring skills. Per the
digital-twin doctrine (docs/superpowers/plans/2026-07-02-drift-hardening-program.md,
Item 1), orphans are a HARD gate FAILURE by default: every served handout MUST have
a skill canonical.

The combined x {suprep,clenpiq,lactulose} orphans were retired in Item 1
(2026-07-02): they now have skill canonicals + build_personalized_templates.py
VARIANTS rows, so they are ordinary "forked" templates.

The only remaining allowed orphans are enumerated in ALLOWED_ORPHANS below, each
with a pointer to the plan item that will retire it. Anything NOT in that set that
resolves to no canonical FAILS the gate.

USAGE
-----
  check_personalized_drift.py            # gate: exit non-zero on drift / untracked
  check_personalized_drift.py --update   # re-baseline manifest to current canonicals
                                         # (run after reviewing + syncing a fork)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BACKEND_DIR / "app" / "templates"
VENDOR_DIR = BACKEND_DIR / "vendor"
MANIFEST = Path(__file__).resolve().parent / "personalized_provenance.json"

# Personalized templates permitted to have NO skill canonical (backend-only).
# Per the digital-twin doctrine this set MUST shrink to empty. Each entry names
# the plan item that will retire it. Anything backend-only NOT listed here FAILS.
#   flexsig/print-personalized.{en,es}: retired by drift-hardening Item 5
#   (fold flexsig into the monorepo + add its canonical mapping to
#   _canonical_rel_for). The flex-sig skill IS vendored; only the gate's naming
#   rule doesn't map the `flexsig` family yet — Item 5 closes that.
ALLOWED_ORPHANS = {
    "flexsig/print-personalized.en.html",
    "flexsig/print-personalized.es.html",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_rel_for(personalized_rel: str) -> str | None:
    """Map an app/templates-relative personalized path to its vendor-relative
    canonical path (the file it derives from). Returns None when the naming
    rule yields no candidate. Existence is checked by the caller.
    """
    p = Path(personalized_rel)
    family = p.parts[0]                       # bowel_prep | egd | egd_phmii
    name = p.name                             # e.g. suprep-standard-print-personalized.en.html
    # split "<stem>-personalized.<lang>.html"
    if "-personalized." not in name:
        return None
    stem, tail = name.split("-personalized.", 1)   # tail = "en.html"
    lang_html = tail                                # "en.html"

    if family == "egd":
        return f"egd-handout-generator/templates/egd-print.{lang_html}"
    if family == "composed":
        return f"egd-handout-generator/templates/egd-print.{lang_html}"
    if family == "egd_phmii":
        return f"egd-handout-generator/templates/egdph-print.{lang_html}"
    if family == "bowel_prep":
        # The colonoscopy-only "standard" fork is named just print-personalized.*
        if stem == "print":
            stem = "standard-print"
        return f"bowel-prep-generator/templates/{stem}.{lang_html}"
    return None


def _discover() -> list[str]:
    """All personalized templates on disk, as app/templates-relative posix paths."""
    return sorted(
        str(p.relative_to(TEMPLATES_DIR).as_posix())
        for p in TEMPLATES_DIR.rglob("*-personalized.*.html")
    )


def _build_manifest() -> dict:
    templates: dict[str, dict] = {}
    for rel in _discover():
        canon_rel = _canonical_rel_for(rel)
        entry: dict[str, object] = {}
        if canon_rel and (VENDOR_DIR / canon_rel).exists():
            entry["canonical"] = canon_rel
            entry["canonical_sha256"] = _sha256(VENDOR_DIR / canon_rel)
            entry["status"] = "forked"
        else:
            entry["canonical"] = None
            entry["status"] = "backend-only"
            entry["note"] = (
                "no skill canonical (matrix leak / backend-only); "
                "reachable via the scheduler but hand-maintained"
            )
        templates[rel] = entry
    return {
        "_doc": (
            "Provenance for scheduler personalized print templates. "
            "canonical_sha256 = sha of the vendored canonical at last sync. "
            "check_personalized_drift.py FAILS when a canonical changes since "
            "this baseline (NEW drift). Pre-existing fork staleness is a separate "
            "backlog: docs/PERSONALIZED_TEMPLATE_DRIFT.md. Re-baseline with --update "
            "after reviewing + syncing the affected fork."
        ),
        "templates": templates,
    }


def _load_manifest() -> dict:
    if not MANIFEST.exists():
        print(f"FATAL: manifest not found at {MANIFEST}. Run with --update to create it.",
              file=sys.stderr)
        sys.exit(2)
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def cmd_update() -> int:
    manifest = _build_manifest()
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    n = len(manifest["templates"])
    forked = sum(1 for e in manifest["templates"].values() if e["status"] == "forked")
    orphan = n - forked
    print(f"Wrote {MANIFEST.relative_to(BACKEND_DIR)} — {n} templates "
          f"({forked} forked, {orphan} backend-only).")
    return 0


def cmd_check() -> int:
    manifest = _load_manifest()
    recorded = manifest["templates"]
    on_disk = set(_discover())
    tracked = set(recorded)

    drift: list[str] = []
    untracked = sorted(on_disk - tracked)
    missing = sorted(tracked - on_disk)
    ok = 0
    orphans: list[str] = []          # allowed backend-only (ALLOWED_ORPHANS)
    bad_orphans: list[str] = []      # backend-only NOT on the allowlist -> FAIL

    for rel in sorted(on_disk & tracked):
        entry = recorded[rel]
        canon_rel = entry.get("canonical")
        if not canon_rel:
            if rel in ALLOWED_ORPHANS:
                orphans.append(rel)
            else:
                bad_orphans.append(rel)
            continue
        canon_path = VENDOR_DIR / canon_rel
        if not canon_path.exists():
            drift.append(f"{rel}: canonical vanished ({canon_rel}) — was forked, now parentless")
            continue
        if _sha256(canon_path) != entry.get("canonical_sha256"):
            drift.append(f"{rel}: canonical CHANGED since last sync ({canon_rel}) — review + re-sync this fork")
            continue
        ok += 1

    # Report
    print(f"Personalized-template drift gate — {len(on_disk)} templates")
    print(f"  OK (canonical unchanged):     {ok}")
    print(f"  ORPHAN (allowed backend-only):{len(orphans)}")
    for o in orphans:
        print(f"      · {o}")
    if bad_orphans:
        print(f"  ORPHAN (NO canonical — FAIL): {len(bad_orphans)}")
        for o in bad_orphans:
            print(f"      ✗ {o}  — every served handout must have a skill canonical "
                  f"(add it + a build_personalized_templates VARIANTS row, or list it "
                  f"in ALLOWED_ORPHANS with a retirement pointer)")
    if untracked:
        print(f"  UNTRACKED (add via --update): {len(untracked)}")
        for u in untracked:
            print(f"      ! {u}")
    if missing:
        print(f"  MISSING (in manifest, not on disk): {len(missing)}")
        for m in missing:
            print(f"      ! {m}")
    if drift:
        print(f"  DRIFT (canonical changed):    {len(drift)}")
        for d in drift:
            print(f"      ✗ {d}")

    failed = bool(drift or untracked or missing or bad_orphans)
    print("\n" + ("FAIL — drift/untracked/missing/unlisted-orphan above." if failed
                  else "PASS — no new drift; every served handout has a canonical "
                       "(allowed backend-only orphans are on the shrinking allowlist)."))
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update", action="store_true",
                    help="re-baseline the manifest to current canonical hashes")
    args = ap.parse_args()
    return cmd_update() if args.update else cmd_check()


if __name__ == "__main__":
    sys.exit(main())
