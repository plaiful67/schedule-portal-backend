#!/usr/bin/env python3
"""
Build the two static-site repos that back the combined-handout (EGD + colonoscopy)
mobile QR codes:
  ~/Desktop/egdcolon-giready/    -> egdcolon.giready.com   (SCC content)
  ~/Desktop/egdcolon86-giready/  -> egdcolon86.giready.com (PMCH content)

Layout (per repo, per language):
  index.html                    landing page — band picker grid
  <band_path>/index.html        per-band page (e.g. u30kg/index.html)
  es/index.html                 Spanish landing
  es/<band_path>/index.html     Spanish per-band page

Combined sites cover only the 5 STANDARD weight bands — no infant variants.
The infant under-15 protocol does not apply to combined EGD + colonoscopy.

Each per-band page now includes the FULL bowel-prep algorithm (the same
content as the printed handout) plus an "What Are These Procedures?"
EGD intro section above the bowel-prep content.

Self-contained: reads practice.yaml + data/dosing.yaml directly via PyYAML
(does NOT couple to scripts/render.py beyond the dose-string helpers).

Usage:
    python scripts/build_combined_websites.py
"""

import sys
from pathlib import Path

try:
    import yaml  # noqa: F401  (verifies install; loaded transitively below)
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml\n")
    sys.exit(1)

# Reuse the colonoscopy script's per-repo build logic and per-band rendering.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_colonoscopy_websites import (  # noqa: E402
    TEMPLATES,
    LOGO_PATH,  # noqa: F401  (referenced by build_for_repo via module globals)
    build_for_repo,
    write_repo_metadata,
    _load_yaml,
    PRACTICE_PATH,
    DOSING_PATH,
)

# Per-location target repo. The subdomain comes from `mobile_subdomain_combined`
# in dosing.yaml.
SITES = {
    "scc":  Path.home() / "Desktop" / "peds-gi-system" / "egdcolon-giready",
    "pmch": Path.home() / "Desktop" / "peds-gi-system" / "egdcolon86-giready",
}

# Combined sites cover all 7 protocols: both infant variants + 5 standard bands.
BAND_ORDER = [
    "under-15",         # infant MiraLAX
    "under-15-enema",   # infant clear-liquids + saline enema
    "15-20",
    "21-30",
    "31-40",
    "41-50",
    "over-50",
]

HTML_TITLE_BAND_EN = "EGD + Colonoscopy Prep — {label} — What to Expect"
HTML_TITLE_BAND_ES = "Preparación para EGD y Colonoscopia — {label} — Qué Esperar"
HTML_TITLE_LANDING_EN = "EGD + Colonoscopy Prep — What to Expect"
HTML_TITLE_LANDING_ES = "Preparación para EGD y Colonoscopia — Qué Esperar"


def main():
    practice_cfg = _load_yaml(PRACTICE_PATH)
    dosing_cfg   = _load_yaml(DOSING_PATH)
    locations    = dosing_cfg["locations"]
    bands_by_id  = {b["id"]: b for b in dosing_cfg["bands"]}

    for bid in BAND_ORDER:
        if bid not in bands_by_id:
            sys.exit(f"band {bid!r} missing from data/dosing.yaml")

    landing_template_en = TEMPLATES / "combined-mobile-landing.en.html"
    landing_template_es = TEMPLATES / "combined-mobile-landing.es.html"

    written_total = 0
    for location_id, repo_dir in SITES.items():
        if location_id not in locations:
            sys.exit(f"location {location_id!r} missing from data/dosing.yaml")
        location = locations[location_id]
        subdomain = location.get("mobile_subdomain_combined", location_id)

        written = build_for_repo(
            repo_dir, location_id, location, practice_cfg, bands_by_id, BAND_ORDER,
            landing_template_en, landing_template_es,
            HTML_TITLE_LANDING_EN, HTML_TITLE_LANDING_ES,
            HTML_TITLE_BAND_EN, HTML_TITLE_BAND_ES,
            family="combined",
        )
        written += write_repo_metadata(repo_dir, location, subdomain)
        written_total += len(written)
        print(f"  built {repo_dir} ({location_id} -> {subdomain}.giready.com): "
              f"{len(written)} files")

    print(f"\n{written_total} files written across {len(SITES)} site repos.")


if __name__ == "__main__":
    main()
