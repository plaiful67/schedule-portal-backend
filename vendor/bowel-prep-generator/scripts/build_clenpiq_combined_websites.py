#!/usr/bin/env python3
"""
Build the two HIDDEN combined-EGD+colonoscopy CLENPIQ static-site repos:

  ~/Desktop/peds-gi-system/egdcolonclenpiq-giready/    -> egdcolonclenpiq.giready.com   (SCC)
  ~/Desktop/peds-gi-system/egdcolonclenpiq86-giready/  -> egdcolonclenpiq86.giready.com (PMCH)

These cover the combined EGD + colonoscopy procedure with the CLENPIQ
alternative prep. Scheduler-only — not linked from giready.com and carry
`X-Robots-Tag: noindex, nofollow`. Patients reach them only via personalized
URLs handed out by the scheduler portal.

Single unified band (CLENPIQ dosing is identical across 31-40, 41-50, and
over-50 kg) — no band-picker landing page, content lives at /clenpiq/
directly.

Usage:
    python scripts/build_clenpiq_combined_websites.py
"""

import re
import shutil
import sys
from pathlib import Path

try:
    import yaml  # noqa: F401
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml\n")
    sys.exit(1)

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
LOGO_PATH = TEMPLATES / "logo-pmch.png"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import build_clenpiq_strings, _load_partials  # noqa: E402
from build_colonoscopy_websites import (  # noqa: E402
    _load_yaml,
    build_practice_placeholders,
    build_location_placeholders,
    _do_replace,
    PRACTICE_PATH,
    DOSING_PATH,
)
from build_clenpiq_websites import (  # noqa: E402
    HEADERS_CONTENT,
    GITIGNORE_CONTENT,
    BAND_LABELS,
    BAND_LB,
    BAND_NOTE,
    clean_repo,
)

SITES = {
    "scc":  Path.home() / "Desktop" / "peds-gi-system" / "egdcolonclenpiq-giready",
    "pmch": Path.home() / "Desktop" / "peds-gi-system" / "egdcolonclenpiq86-giready",
}

BAND_ORDER = ["clenpiq"]

HTML_TITLE_BAND_EN = "EGD + Colonoscopy Prep — CLENPIQ — What to Expect"
HTML_TITLE_BAND_ES = "Preparación para EGD y Colonoscopia — CLENPIQ — Qué Esperar"

README_TEMPLATE = """# {repo_name}

**INTERNAL / SCHEDULER-ONLY** combined EGD + colonoscopy CLENPIQ bowel-prep website for the **{location_name}**.

- Target subdomain: **https://{subdomain}.giready.com/clenpiq/** (Phase-2; not yet provisioned)
- Spanish version: **https://{subdomain}.giready.com/es/clenpiq/**

CLENPIQ (sodium picosulfate / magnesium oxide / citric acid) is a
scheduler-only alternative prep for patients **31 kg and up** who cannot
tolerate the MiraLAX + Gatorade volume.

This site is **not linked** from `giready.com` and carries `X-Robots-Tag: noindex, nofollow`. Patients reach it only via personalized URLs handed out by the scheduler portal (`schedule.giready.com`).

The HTML is generated from the [`bowel-prep-generator` skill](../../.claude/skills/bowel-prep-generator/) — edit `templates/combined-mobile-clenpiq-standard.{{en,es}}.html`, `data/dosing.yaml`, or `practice.yaml`, then re-run `python scripts/build_clenpiq_combined_websites.py` from the skill folder. Don't hand-edit the HTML in this repo; changes will be overwritten.
"""


def render_band_page(lang, band, location, practice_cfg, qr,
                     logo_src, lang_toggle_href, html_title):
    """Render the single CLENPIQ band page (combined EGD+colonoscopy mobile)."""
    template_path = TEMPLATES / f"combined-mobile-clenpiq-standard.{lang}.html"
    src = template_path.read_text(encoding="utf-8")

    for token, body in _load_partials(lang).items():
        src = src.replace(token, body)

    dose_replacements = build_clenpiq_strings(band, lang, location=location)

    location_phone_tel = re.sub(r"\D", "", location.get("phone", ""))
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""
    youtube_url = qr["youtube_url_es" if lang == "es" else "youtube_url_en"]
    portal_url = qr["portal_url"]
    gikids_url = qr["gikids_url"]

    replacements = {
        **build_practice_placeholders(practice_cfg),
        **build_location_placeholders(location, lang),
        **dose_replacements,
        "{{HTML_TITLE}}":         html_title,
        "{{BAND_LABEL}}":         BAND_LABELS[band["id"]][lang],
        "{{LOGO_SRC}}":           logo_src,
        "{{LANG_TOGGLE_HREF}}":   lang_toggle_href,
        "{{BAND_LB}}":            BAND_LB[band["id"]][lang],
        "{{BAND_NOTE}}":          BAND_NOTE[band["id"]][lang],
        "{{MAPS_URL}}":           maps_url,
        "{{YOUTUBE_URL}}":        youtube_url,
        "{{PORTAL_URL}}":         portal_url,
        "{{GIKIDS_URL}}":         gikids_url,
        "{{LOCATION_PHONE_TEL}}": location_phone_tel,
    }

    return _do_replace(src, replacements, template_path.name)


def build_for_repo(repo_dir, location_id, location, practice_cfg, bands_by_id):
    qr = practice_cfg["qr_targets"]

    repo_dir.mkdir(parents=True, exist_ok=True)
    clean_repo(repo_dir)
    (repo_dir / "es").mkdir(exist_ok=True)

    written = []

    band = bands_by_id["clenpiq"]
    path = band["mobile_path"]  # "clenpiq"

    en_dir = repo_dir / path
    en_dir.mkdir(parents=True, exist_ok=True)
    en_html = render_band_page(
        "en", band, location, practice_cfg, qr,
        logo_src="../logo-pmch.png",
        lang_toggle_href=f"../es/{path}/",
        html_title=HTML_TITLE_BAND_EN,
    )
    p = en_dir / "index.html"
    p.write_text(en_html, encoding="utf-8")
    written.append(p)

    es_dir = repo_dir / "es" / path
    es_dir.mkdir(parents=True, exist_ok=True)
    es_html = render_band_page(
        "es", band, location, practice_cfg, qr,
        logo_src="../../logo-pmch.png",
        lang_toggle_href=f"../../{path}/",
        html_title=HTML_TITLE_BAND_ES,
    )
    p = es_dir / "index.html"
    p.write_text(es_html, encoding="utf-8")
    written.append(p)

    if LOGO_PATH.exists():
        shutil.copy(LOGO_PATH, repo_dir / "logo-pmch.png")
        written.append(repo_dir / "logo-pmch.png")

    return written


def write_repo_metadata(repo_dir, location, subdomain):
    written = []
    headers_path = repo_dir / "_headers"
    if not headers_path.exists():
        headers_path.write_text(HEADERS_CONTENT, encoding="utf-8")
        written.append(headers_path)

    gitignore_path = repo_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(GITIGNORE_CONTENT, encoding="utf-8")
        written.append(gitignore_path)

    readme_path = repo_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(README_TEMPLATE.format(
            repo_name=repo_dir.name,
            location_name=location["name_en"],
            subdomain=subdomain,
        ), encoding="utf-8")
        written.append(readme_path)
    return written


def main():
    practice_cfg = _load_yaml(PRACTICE_PATH)
    dosing_cfg = _load_yaml(DOSING_PATH)
    locations = dosing_cfg["locations"]
    bands_by_id = {b["id"]: b for b in dosing_cfg["bands"]}

    for bid in BAND_ORDER:
        if bid not in bands_by_id:
            sys.exit(f"band {bid!r} missing from data/dosing.yaml")
        if bands_by_id[bid].get("public", True):
            sys.exit(
                f"band {bid!r} is marked public — combined CLENPIQ builder "
                f"requires `public: false` on every band"
            )
        if bands_by_id[bid]["protocol"] != "clenpiq-standard":
            sys.exit(
                f"band {bid!r} has protocol {bands_by_id[bid]['protocol']!r} — "
                f"combined CLENPIQ builder only supports clenpiq-standard"
            )

    written_total = 0
    for location_id, repo_dir in SITES.items():
        if location_id not in locations:
            sys.exit(f"location {location_id!r} missing from data/dosing.yaml")
        location = locations[location_id]
        subdomain = "egdcolonclenpiq" if location_id == "scc" else "egdcolonclenpiq86"

        written = build_for_repo(
            repo_dir, location_id, location, practice_cfg, bands_by_id,
        )
        written += write_repo_metadata(repo_dir, location, subdomain)
        written_total += len(written)
        print(f"  built {repo_dir} ({location_id} -> {subdomain}.giready.com/clenpiq/): "
              f"{len(written)} files")

    print(f"\n{written_total} files written across {len(SITES)} hidden combined-CLENPIQ repos.")


if __name__ == "__main__":
    main()
