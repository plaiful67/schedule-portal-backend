#!/usr/bin/env python3
"""
Build the two HIDDEN combined-EGD+colonoscopy lactulose static-site repos:

  ~/Desktop/peds-gi-system/egdcolonlact-giready/    -> egdcolonlact.giready.com   (SCC)
  ~/Desktop/peds-gi-system/egdcolonlact86-giready/  -> egdcolonlact86.giready.com (PMCH)

These cover the combined EGD + colonoscopy procedure with the lactulose
backup prep. Scheduler-only — not linked from giready.com and carry
`X-Robots-Tag: noindex, nofollow`. Patients reach them only via personalized
URLs handed out by the scheduler portal.

Combined EGDs ARE offered for kids under 15 kg (matches the existing
`build_combined_websites.py` BAND_ORDER which includes "under-15"), so we
ship a `lactulose-infant` variant for that band too.

Phase-2 status: builds the local repo content. Cloudflare provisioning,
GitHub remote push, and scheduler adapter wiring still pending.

Usage:
    python scripts/build_lactulose_combined_websites.py
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
from render import build_lactulose_strings, _load_partials  # noqa: E402
from build_colonoscopy_websites import (  # noqa: E402
    _load_yaml,
    build_practice_placeholders,
    build_location_placeholders,
    _do_replace,
    _inject_analytics,
    PRACTICE_PATH,
    DOSING_PATH,
)
from build_lactulose_websites import (  # noqa: E402
    HEADERS_CONTENT,
    GITIGNORE_CONTENT,
    clean_repo,
)

SITES = {
    "scc":  Path.home() / "Desktop" / "peds-gi-system" / "egdcolonlact-giready",
    "pmch": Path.home() / "Desktop" / "peds-gi-system" / "egdcolonlact86-giready",
}

BAND_ORDER = [
    "under-15-lact",  # u15kgLact  — daily-dose lactulose for infants
    "15-20-lact",     # u20kgLact  — big-prep lactulose for 15–20 kg
    "21-30-lact",     # u30kgLact  — big-prep lactulose for 21–30 kg
]

BAND_LABELS = {
    "under-15-lact": {"en": "Under 15 kg",  "es": "Menos de 15 kg"},
    "15-20-lact":    {"en": "15–20 kg",     "es": "15–20 kg"},
    "21-30-lact":    {"en": "21–30 kg",     "es": "21–30 kg"},
}

BAND_LB = {
    "under-15-lact": {"en": "[Under 33 lb]", "es": "[Menos de 33 lb]"},
    "15-20-lact":    {"en": "[33–44 lb]",    "es": "[33–44 lb]"},
    "21-30-lact":    {"en": "[46–66 lb]",    "es": "[46–66 lb]"},
}

BAND_NOTE = {
    "under-15-lact": {"en": "Lactulose option (oral)", "es": "Opción Lactulosa (oral)"},
    "15-20-lact":    {"en": "Lactulose option (oral)", "es": "Opción Lactulosa (oral)"},
    "21-30-lact":    {"en": "Lactulose option (oral)", "es": "Opción Lactulosa (oral)"},
}

HTML_TITLE_BAND_EN = "EGD + Colonoscopy Prep — Lactulose — {label} — What to Expect"
HTML_TITLE_BAND_ES = "Preparación para EGD y Colonoscopia — Lactulosa — {label} — Qué Esperar"
HTML_TITLE_LANDING_EN = "EGD + Colonoscopy Prep — Lactulose (Internal)"
HTML_TITLE_LANDING_ES = "Preparación para EGD y Colonoscopia — Lactulosa (Interno)"

README_TEMPLATE = """# {repo_name}

**INTERNAL / SCHEDULER-ONLY** combined EGD + colonoscopy lactulose bowel-prep website for the **{location_name}**.

- Target subdomain: **https://{subdomain}.giready.com/** (Phase-2; not yet provisioned)
- Spanish version: **https://{subdomain}.giready.com/es/**

This site is **not linked** from `giready.com` and carries `X-Robots-Tag: noindex, nofollow`. Patients reach it only via personalized URLs handed out by the scheduler portal (`schedule.giready.com`).

The HTML is generated from the [`bowel-prep-generator` skill](../../.claude/skills/bowel-prep-generator/) — edit `templates/combined-mobile-lactulose-standard.{{en,es}}.html`, `data/dosing.yaml`, or `practice.yaml`, then re-run `python scripts/build_lactulose_combined_websites.py` from the skill folder. Don't hand-edit the HTML in this repo; changes will be overwritten.

No infant variant — combined EGDs are not offered for kids under 15 kg.
"""


def _band_template_for_lact_combined(protocol, lang):
    """Pick the combined-lactulose template by protocol."""
    if protocol == "lactulose-infant":
        return TEMPLATES / f"combined-mobile-lactulose-infant.{lang}.html"
    if protocol == "lactulose-standard":
        return TEMPLATES / f"combined-mobile-lactulose-standard.{lang}.html"
    raise ValueError(f"Combined lactulose family does not support protocol {protocol!r}")


def render_band_cards(bands_by_id, lang, band_ids):
    cards = []
    for bid in band_ids:
        path = bands_by_id[bid]["mobile_path"]
        label = BAND_LABELS[bid][lang]
        lb = BAND_LB[bid][lang]
        note = BAND_NOTE[bid][lang]
        note_html = f'    <div class="band-note">{note}</div>\n' if note else ""
        arrow = "View instructions →" if lang == "en" else "Ver instrucciones →"
        cards.append(
            f'  <a class="band-card" href="{path}/">\n'
            f'    <div class="band-label">{label} <span class="band-lb-inline">{lb}</span></div>\n'
            f'{note_html}'
            f'    <div class="band-arrow">{arrow}</div>\n'
            f'  </a>'
        )
    return "\n".join(cards)


def render_band_page(lang, band, location, practice_cfg, qr,
                     logo_src, lang_toggle_href, landing_href, html_title):
    protocol = band["protocol"]
    template_path = _band_template_for_lact_combined(protocol, lang)
    src = template_path.read_text(encoding="utf-8")

    for token, body in _load_partials(lang).items():
        src = src.replace(token, body)

    dose_replacements = build_lactulose_strings(band, lang, location=location)

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
        "{{LANDING_HREF}}":       landing_href,
        "{{BAND_LB}}":            BAND_LB[band["id"]][lang],
        "{{BAND_NOTE}}":          BAND_NOTE[band["id"]][lang],
        "{{MAPS_URL}}":           maps_url,
        "{{YOUTUBE_URL}}":        youtube_url,
        "{{PORTAL_URL}}":         portal_url,
        "{{GIKIDS_URL}}":         gikids_url,
        "{{LOCATION_PHONE_TEL}}": location_phone_tel,
        "{{PDF_BUTTON_BLOCK}}":   "",
        # combined-mobile-lactulose-infant.{lang}.html references
        # {{WARNING_WEIGHT}} in the "for infants and children under X only"
        # callout. Pull from the band; default to "15 kg" if absent.
        "{{WARNING_WEIGHT}}":     band.get(f"warning_weight_{lang}",
                                            band.get("warning_weight_en", "15 kg")),
    }

    return _do_replace(src, replacements, template_path.name)


def render_landing_page(template_path, lang, practice_cfg, bands_by_id, band_ids,
                        logo_src, lang_toggle_href, html_title):
    src = template_path.read_text(encoding="utf-8")
    replacements = {
        **build_practice_placeholders(practice_cfg),
        "{{HTML_TITLE}}":       html_title,
        "{{LOGO_SRC}}":         logo_src,
        "{{LANG_TOGGLE_HREF}}": lang_toggle_href,
        "{{BAND_CARDS}}":       render_band_cards(bands_by_id, lang, band_ids),
    }
    out = _do_replace(src, replacements, template_path.name)
    if lang == "en":
        banner = (
            '<div style="background:#fff8e1;border:2px solid #f57c00;border-radius:6px;'
            'padding:14px 18px;margin:16px auto;max-width:720px;font-size:15px;line-height:1.45;">'
            '<strong>Internal — not for browsing.</strong><br>'
            'This is the lactulose backup prep for combined EGD + colonoscopy. Use the personalized link given to you by the office. '
            'If you reached this page by accident, the standard MiraLAX combined prep is at '
            '<a href="https://egdcolon.giready.com/">egdcolon.giready.com</a>.</div>'
        )
    else:
        banner = (
            '<div style="background:#fff8e1;border:2px solid #f57c00;border-radius:6px;'
            'padding:14px 18px;margin:16px auto;max-width:720px;font-size:15px;line-height:1.45;">'
            '<strong>Interno — no para navegación.</strong><br>'
            'Esta es la preparación de respaldo con lactulosa para EGD y colonoscopia combinados. Use el enlace personalizado que le dio el consultorio. '
            'Si llegó aquí por accidente, la preparación estándar con MiraLAX está en '
            '<a href="https://egdcolon.giready.com/es/">egdcolon.giready.com/es/</a>.</div>'
        )
    return out.replace("<body>", f"<body>\n{banner}", 1)


def build_for_repo(repo_dir, location_id, location, practice_cfg, bands_by_id, band_ids,
                   landing_template_en, landing_template_es):
    qr = practice_cfg["qr_targets"]

    repo_dir.mkdir(parents=True, exist_ok=True)
    clean_repo(repo_dir, band_ids, bands_by_id)
    (repo_dir / "es").mkdir(exist_ok=True)

    written = []

    en_landing_html = render_landing_page(
        landing_template_en, "en", practice_cfg, bands_by_id, band_ids,
        logo_src="logo-pmch.png",
        lang_toggle_href="es/",
        html_title=HTML_TITLE_LANDING_EN,
    )
    p = repo_dir / "index.html"
    p.write_text(_inject_analytics(en_landing_html, "lactulose-combined", location_id, "en"), encoding="utf-8")
    written.append(p)

    es_landing_html = render_landing_page(
        landing_template_es, "es", practice_cfg, bands_by_id, band_ids,
        logo_src="../logo-pmch.png",
        lang_toggle_href="../",
        html_title=HTML_TITLE_LANDING_ES,
    )
    p = repo_dir / "es" / "index.html"
    p.write_text(_inject_analytics(es_landing_html, "lactulose-combined", location_id, "es"), encoding="utf-8")
    written.append(p)

    for bid in band_ids:
        band = bands_by_id[bid]
        path = band["mobile_path"]
        label_en = BAND_LABELS[bid]["en"]
        label_es = BAND_LABELS[bid]["es"]

        en_dir = repo_dir / path
        shutil.rmtree(en_dir, ignore_errors=True)
        en_dir.mkdir(parents=True, exist_ok=True)
        en_html = render_band_page(
            "en", band, location, practice_cfg, qr,
            logo_src="../logo-pmch.png",
            lang_toggle_href=f"../es/{path}/",
            landing_href="../",
            html_title=HTML_TITLE_BAND_EN.format(label=label_en),
        )
        p = en_dir / "index.html"
        p.write_text(_inject_analytics(en_html, "lactulose-combined", location_id, "en", bid), encoding="utf-8")
        written.append(p)

        es_dir = repo_dir / "es" / path
        shutil.rmtree(es_dir, ignore_errors=True)
        es_dir.mkdir(parents=True, exist_ok=True)
        es_html = render_band_page(
            "es", band, location, practice_cfg, qr,
            logo_src="../../logo-pmch.png",
            lang_toggle_href=f"../../{path}/",
            landing_href="../",
            html_title=HTML_TITLE_BAND_ES.format(label=label_es),
        )
        p = es_dir / "index.html"
        p.write_text(_inject_analytics(es_html, "lactulose-combined", location_id, "es", bid), encoding="utf-8")
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
                f"band {bid!r} is marked public — combined lactulose builder "
                f"requires `public: false` on every band"
            )
        if bands_by_id[bid]["protocol"] not in ("lactulose-infant", "lactulose-standard"):
            sys.exit(
                f"band {bid!r} has protocol {bands_by_id[bid]['protocol']!r} — "
                f"combined lactulose builder only supports lactulose-* protocols"
            )

    landing_template_en = TEMPLATES / "combined-mobile-landing.en.html"
    landing_template_es = TEMPLATES / "combined-mobile-landing.es.html"

    written_total = 0
    for location_id, repo_dir in SITES.items():
        if location_id not in locations:
            sys.exit(f"location {location_id!r} missing from data/dosing.yaml")
        location = locations[location_id]
        subdomain = "egdcolonlact" if location_id == "scc" else "egdcolonlact86"

        written = build_for_repo(
            repo_dir, location_id, location, practice_cfg, bands_by_id, BAND_ORDER,
            landing_template_en, landing_template_es,
        )
        written += write_repo_metadata(repo_dir, location, subdomain)
        written_total += len(written)
        print(f"  built {repo_dir} ({location_id} -> {subdomain}.giready.com): "
              f"{len(written)} files")

    print(f"\n{written_total} files written across {len(SITES)} hidden combined-lactulose repos.")


if __name__ == "__main__":
    main()
