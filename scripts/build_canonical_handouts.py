#!/usr/bin/env python3
"""Render the canonical ("office") handout PDF set for the practice Google Drive.

"Office" = the scheduler *custom* PDF look (Calm theme, ADA/PDF-UA tagging,
inlined directions, no legal footer) but GENERIC: **no procedure date** and
**all doctors** instead of a single performing physician. Produced by the
scheduler backend adapters in ``audience="office"`` mode (see
``app/adapters/_office.py``).

This writes the same folder tree + filenames the (now-retired) color set used —
so it's a drop-in replacement of the *procedure* PDFs under
``~/Desktop/peds-gi-system/peds-gi-handouts/``. It reuses the SAME drive-routing
YAML as ``peds-gi-prep-system/scripts/consolidate_handouts.py``:

  * bowel-prep — ``dosing.yaml::consolidate`` (drive_folder / drive_filename /
    is_combined / drive_has_bands)
  * EGD + flex-sig — each skill's ``procedure.yaml`` (drive_folder /
    drive_filename / locations)

so adding a prep/procedure stays a YAML-only change. Only the render-param
mapping (which prep/variant/bands a routing entry corresponds to) lives here.

Directions PDFs (location root) and Staff references are NOT touched — those
stay with consolidate_handouts.py. This driver wipes only the procedure
subfolders it owns, then rewrites them.

Run inside the backend venv (weasyprint/pango live there):
    .venv/bin/python scripts/build_canonical_handouts.py
    .venv/bin/python scripts/build_canonical_handouts.py --only bowel_prep
    .venv/bin/python scripts/build_canonical_handouts.py --out /tmp/office-preview
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# Import the backend render adapters (this script lives in the backend repo).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.adapters import bowel_prep, egd, egd_phmii, flex_sig  # noqa: E402

HOME = Path.home()
DESKTOP = HOME / "Desktop" / "peds-gi-system"
OUT_ROOT = DESKTOP / "peds-gi-handouts"

# Skill data dirs (drive-routing YAML — same sources consolidate_handouts.py reads).
SKILLS = HOME / ".claude" / "skills"
DOSING_YAML = SKILLS / "bowel-prep-generator" / "data" / "dosing.yaml"
EGD_PROC_YAML = SKILLS / "egd-handout-generator" / "data" / "procedure.yaml"
FLEXSIG_PROC_YAML = SKILLS / "flex-sig-handout-generator" / "data" / "procedure.yaml"

LOCATIONS = ["SCC", "PMCH"]
LANGS = ["en", "es"]

# Top-level Drive folder names — must byte-match consolidate_handouts.py so this
# is a true drop-in replacement of the existing tree.
LOCATION_FOLDER = {
    "SCC": "SCC - Surgery Center Carmel - Preop patient handouts",
    "PMCH": "PMCH - Main Hospital - 86th St - Preop patient handouts",
}

# Which USER weight bands each bowel-prep routing entry renders, and the
# (prep_type, variant) that drive the office render. Bands are USER bands — the
# adapter remaps lactulose/clenpiq/suprep to their dosing.yaml entries.
MIRALAX_BANDS = ["under-15", "under-15-enema", "15-20", "21-30", "31-40", "41-50", "over-50"]
BP_RENDER = {
    "bowel_prep": {"prep": "miralax", "variant": "standard", "bands": MIRALAX_BANDS, "simple": False},
    "combined": {"prep": "miralax", "variant": "combined", "bands": MIRALAX_BANDS, "simple": False},
    "suprep": {"prep": "suprep", "variant": "standard", "bands": ["over-50"], "simple": False},
    "lactulose": {"prep": "lactulose", "variant": "standard", "bands": ["under-15", "15-20", "21-30"], "simple": True},
    "clenpiq": {"prep": "clenpiq", "variant": "standard", "bands": ["31-40"], "simple": False},
}

# Office-only combined alt-prep sets — combined (EGD+colonoscopy) is the primary
# peds handout, so the alternative preps need combined variants too. These had no
# color-set precedent (no dosing.yaml::consolidate entry), so their EGDcolon
# option-folder routing lives here. Same lactulose/clenpiq/suprep band rules as
# the standalone-colonoscopy entries, but variant="combined".
BP_OFFICE_ONLY = [
    {"id": "combined_suprep", "prep": "suprep", "variant": "combined", "bands": ["over-50"],
     "simple": False, "drive_folder": "EGDcolon (SUPREP option)", "drive_filename": "EGDcolon SUPREP",
     "has_bands": False},
    {"id": "combined_clenpiq", "prep": "clenpiq", "variant": "combined", "bands": ["31-40"],
     "simple": False, "drive_folder": "EGDcolon (CLENPIQ option)", "drive_filename": "EGDcolon CLENPIQ",
     "has_bands": False},
    {"id": "combined_lactulose", "prep": "lactulose", "variant": "combined",
     "bands": ["under-15", "15-20", "21-30"], "simple": True,
     "drive_folder": "EGDcolon (Lactulose option)", "drive_filename": "EGDcolon Lactulose",
     "has_bands": True},
]


def band_label(band_id: str, *, simple: bool) -> str:
    """Friendly weight-band label for the output filename. Matches the labels
    consolidate_handouts.py produces so filenames stay identical.

    ``simple`` drops the (MiraLAX) qualifier on the infant band (lactulose /
    flex-sig use the plain "Under 15 Kg").
    """
    special = {
        "under-15": "Under 15 Kg" if simple else "Under 15 Kg (MiraLAX)",
        "under-15kg": "Under 15 Kg",
        "under-15-enema": "Under 15 Kg (Saline Enema)",
        "over-50": "Over 50 Kg",
        "over-40kg": "Over 40 Kg",
        "20-40kg": "20-40 Kg",
    }
    if band_id in special:
        return special[band_id]
    m = re.match(r"(\d+)-(\d+)", band_id)
    if m:
        return f"{m.group(1)}-{m.group(2)} Kg"
    return band_id


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        print(f"  WARNING: {path} missing — skipping its family")
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Job model: one Job == one output PDF.
# ---------------------------------------------------------------------------

class Job:
    def __init__(self, *, family, drive_folder, drive_filename, band_suffix, loc, lang, render):
        self.family = family
        self.drive_folder = drive_folder          # e.g. "Colonoscopy", "EGDcolon"
        self.drive_filename = drive_filename      # e.g. "Colonoscopy", "EGDph"
        self.band_suffix = band_suffix            # " 21-30 Kg" or ""
        self.loc = loc                            # "SCC" | "PMCH"
        self.lang = lang                          # "en" | "es"
        self.render = render                      # zero-arg callable -> pdf bytes

    def out_path(self, out_root: Path) -> Path:
        target = out_root / LOCATION_FOLDER[self.loc] / f"{self.loc} - {self.drive_folder}"
        if self.lang == "es":
            target = target / "Spanish"
        es = " ES" if self.lang == "es" else ""
        return target / f"{self.drive_filename}{self.band_suffix} {self.loc}{es}.pdf"


def _bp_jobs(rid, *, prep, variant, bands, simple, drive_folder, drive_filename, has_bands) -> list[Job]:
    jobs: list[Job] = []
    for band in bands:
        suffix = f" {band_label(band, simple=simple)}" if has_bands else ""
        for loc in LOCATIONS:
            for lang in LANGS:
                def make(band=band, loc=loc, lang=lang, variant=variant, prep=prep):
                    return bowel_prep.render_pdf(
                        band_id=band, location_id=loc.lower(), lang=lang,
                        variant=variant, prep_type=prep, audience="office",
                    )
                jobs.append(Job(
                    family=f"bp:{rid}", drive_folder=drive_folder,
                    drive_filename=drive_filename, band_suffix=suffix,
                    loc=loc, lang=lang, render=make,
                ))
    return jobs


def bowel_prep_jobs() -> list[Job]:
    data = _load_yaml(DOSING_YAML)
    jobs: list[Job] = []
    # Colonoscopy-family + combined-miralax — routing (drive_folder / filename /
    # has_bands) comes from dosing.yaml::consolidate; render params from BP_RENDER.
    for entry in data.get("consolidate") or []:
        rid = entry["id"]
        cfg = BP_RENDER.get(rid)
        if cfg is None:
            print(f"  [bowel_prep] no office render mapping for consolidate id {rid!r} — skipping")
            continue
        jobs += _bp_jobs(rid, prep=cfg["prep"], variant=cfg["variant"], bands=cfg["bands"],
                         simple=cfg["simple"], drive_folder=entry["drive_folder"],
                         drive_filename=entry["drive_filename"],
                         has_bands=entry.get("drive_has_bands", True))
    # Office-only combined alt-preps (no color-set precedent; routing inline).
    for spec in BP_OFFICE_ONLY:
        jobs += _bp_jobs(spec["id"], prep=spec["prep"], variant=spec["variant"], bands=spec["bands"],
                         simple=spec["simple"], drive_folder=spec["drive_folder"],
                         drive_filename=spec["drive_filename"], has_bands=spec["has_bands"])
    return jobs


def egd_jobs() -> list[Job]:
    data = _load_yaml(EGD_PROC_YAML)
    # proc_id -> office render adapter
    adapters = {"egd": egd.render_pdf, "egdph": egd_phmii.render_pdf}
    jobs: list[Job] = []
    for pid, proc in (data.get("procedures") or {}).items():
        if not (proc.get("drive_folder") and proc.get("drive_filename")):
            continue
        render_fn = adapters.get(pid)
        if render_fn is None:
            print(f"  [egd] no office adapter for procedure {pid!r} — skipping")
            continue
        allowed = [loc.upper() for loc in (proc.get("locations") or ["scc", "pmch"])]
        for loc in [loc for loc in LOCATIONS if loc in allowed]:
            for lang in LANGS:
                def make(loc=loc, lang=lang, render_fn=render_fn):
                    return render_fn(location_id=loc.lower(), lang=lang, audience="office")
                jobs.append(Job(
                    family=f"egd:{pid}", drive_folder=proc["drive_folder"],
                    drive_filename=proc["drive_filename"], band_suffix="",
                    loc=loc, lang=lang, render=make,
                ))
    return jobs


def flex_sig_jobs() -> list[Job]:
    data = _load_yaml(FLEXSIG_PROC_YAML)
    jobs: list[Job] = []
    for pid, proc in (data.get("procedures") or {}).items():
        if not (proc.get("drive_folder") and proc.get("drive_filename")):
            continue
        allowed = [loc.upper() for loc in (proc.get("locations") or ["scc", "pmch"])]
        bands = [b["id"] for b in (proc.get("bands") or [])]
        for band in bands:
            suffix = f" {band_label(band, simple=True)}"
            for loc in [loc for loc in LOCATIONS if loc in allowed]:
                for lang in LANGS:
                    def make(band=band, loc=loc, lang=lang):
                        return flex_sig.render_pdf(
                            weight_band=band, prep_type="enema",
                            location_id=loc.lower(), lang=lang, audience="office",
                        )
                    jobs.append(Job(
                        family=f"flexsig:{pid}", drive_folder=proc["drive_folder"],
                        drive_filename=proc["drive_filename"], band_suffix=suffix,
                        loc=loc, lang=lang, render=make,
                    ))
    return jobs


FAMILIES = {
    "bowel_prep": bowel_prep_jobs,
    "egd": egd_jobs,
    "flex_sig": flex_sig_jobs,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT_ROOT), help=f"Output root (default: {OUT_ROOT})")
    ap.add_argument("--only", choices=sorted(FAMILIES), action="append",
                    help="Restrict to one or more families (repeatable). Default: all.")
    ap.add_argument("--dry-run", action="store_true",
                    help="List the output paths without rendering.")
    args = ap.parse_args()

    out_root = Path(args.out).expanduser()
    families = args.only or list(FAMILIES)

    jobs: list[Job] = []
    for fam in families:
        jobs.extend(FAMILIES[fam]())
    print(f"Planned {len(jobs)} office PDFs across families: {', '.join(families)} -> {out_root}\n")

    if args.dry_run:
        for j in jobs:
            print(f"  [{j.family}] {j.out_path(out_root).relative_to(out_root)}")
        return 0

    # Wipe only the procedure subfolders we own (leaves directions PDFs at the
    # location root + Staff references untouched).
    owned = {(j.loc, j.drive_folder) for j in jobs}
    import shutil
    for loc, folder in sorted(owned):
        d = out_root / LOCATION_FOLDER[loc] / f"{loc} - {folder}"
        if d.exists():
            shutil.rmtree(d)

    ok = 0
    failures: list[str] = []
    for j in jobs:
        try:
            pdf = j.render()
        except Exception as e:  # keep going; report at the end
            failures.append(f"{j.family} {j.loc}/{j.lang}{j.band_suffix}: {type(e).__name__}: {e}")
            print(f"  FAIL {j.family} {j.loc}/{j.lang}{j.band_suffix}: {type(e).__name__}: {e}")
            continue
        dest = j.out_path(out_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pdf)
        ok += 1
        print(f"  ok  {dest.relative_to(out_root)}")

    print(f"\nDone. {ok}/{len(jobs)} office PDFs written to {out_root}.")
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
