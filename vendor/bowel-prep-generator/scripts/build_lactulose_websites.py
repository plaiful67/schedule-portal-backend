#!/usr/bin/env python3
"""
Build the two HIDDEN static-site repos for the lactulose bowel-prep variant:

  ~/Desktop/peds-gi-system/preplact-giready/    -> preplact.giready.com   (SCC)
  ~/Desktop/peds-gi-system/preplact86-giready/  -> preplact86.giready.com (PMCH)

Lactulose is a scheduler-only backup prep — these sites are NOT linked from
giready.com and carry `X-Robots-Tag: noindex, nofollow`. Patients reach them
only via personalized URLs handed out by the scheduler portal.

Phase-1 status: builds the local repo content. No `git init`, no GitHub
remote, no Cloudflare Pages — those are Phase-2 work alongside the scheduler
prep_type selector.

Architecture mirrors build_colonoscopy_websites.py exactly — same partials,
same location placeholders, same QR + analytics affordances (analytics is
skipped for hidden sites; see `_inject_analytics` in the public builder).
Shared helpers are imported rather than duplicated.

Usage:
    python scripts/build_lactulose_websites.py
"""

import re
import shutil
import sys
from pathlib import Path

try:
    import yaml  # noqa: F401  -- used implicitly via _load_yaml import
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml\n")
    sys.exit(1)

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
LOGO_PATH = TEMPLATES / "logo-pmch.png"

# Reuse the public-builder helpers — keeps lactulose pages 100% in lock-step
# with the public-site chrome.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import build_lactulose_strings, _load_partials  # noqa: E402
from build_colonoscopy_websites import (  # noqa: E402
    _load_yaml,
    build_practice_placeholders,
    build_location_placeholders,
    _do_replace,
    PRACTICE_PATH,
    DOSING_PATH,
)

# Hidden sites — one per location.
SITES = {
    "scc":  Path.home() / "Desktop" / "peds-gi-system" / "preplact-giready",
    "pmch": Path.home() / "Desktop" / "peds-gi-system" / "preplact86-giready",
}

BAND_ORDER = [
    "under-15-lact",  # u15kgLact  — daily-dose lactulose for infants
    "15-20-lact",     # u20kgLact  — big-prep lactulose for 15–20 kg
    "21-30-lact",     # u30kgLact  — big-prep lactulose for 21–30 kg
]

# Mobile-tuned compact labels (override the long dosing.yaml label that
# includes "Lactulose" inline — the page already says "Lactulose Prep
# Option" prominently, so the hero label should be the weight band alone).
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

# Subtitle under the H1 — distinguishes from MiraLAX so patients (and the
# scheduler handing out the link) know this is the backup prep.
BAND_NOTE = {
    "under-15-lact": {"en": "Lactulose option (oral)",
                      "es": "Opción Lactulosa (oral)"},
    "15-20-lact":    {"en": "Lactulose option (oral)",
                      "es": "Opción Lactulosa (oral)"},
    "21-30-lact":    {"en": "Lactulose option (oral)",
                      "es": "Opción Lactulosa (oral)"},
}

HTML_TITLE_BAND_EN = "Colonoscopy Prep — Lactulose — {label} — What to Expect"
HTML_TITLE_BAND_ES = "Preparación para Colonoscopia — Lactulosa — {label} — Qué Esperar"
HTML_TITLE_LANDING_EN = "Colonoscopy Bowel Prep — Lactulose (Internal)"
HTML_TITLE_LANDING_ES = "Preparación para Colonoscopia — Lactulosa (Interno)"

# Stronger noindex than the public sites — these must never be indexed.
HEADERS_CONTENT = """/*
  X-Robots-Tag: noindex, nofollow, noarchive, nosnippet
  X-Frame-Options: SAMEORIGIN
  Referrer-Policy: no-referrer
"""

GITIGNORE_CONTENT = """.DS_Store
*.swp
.idea/
.vscode/
"""

README_TEMPLATE = """# {repo_name}

**INTERNAL / SCHEDULER-ONLY** lactulose bowel-prep website for the **{location_name}**.

- Target subdomain: **https://{subdomain}.giready.com/** (Phase-2; not yet provisioned)
- Spanish version: **https://{subdomain}.giready.com/es/**

This site is **not linked** from `giready.com` and carries `X-Robots-Tag: noindex, nofollow` so it does not appear in search results. Patients reach it only via personalized URLs handed out by the scheduler portal (`schedule.giready.com`).

The HTML is generated from the [`bowel-prep-generator` skill](../../.claude/skills/bowel-prep-generator/) — edit `templates/colonoscopy-mobile-lactulose-*.html`, `data/dosing.yaml`, or `practice.yaml`, then re-run `python scripts/build_lactulose_websites.py` from the skill folder. Don't hand-edit the HTML in this repo; changes will be overwritten.

## Phase-1 status (2026-05-16)

Local build only. No `git init`, no GitHub remote, no Cloudflare Pages. Phase 2 provisions deployment alongside the scheduler `prep_type` selector and the `egdcolonlact-giready` companion repo for combined EGD+colon lactulose handouts.
"""


def _band_template_for_lact(protocol, lang):
    """Pick the lactulose mobile template for this band's protocol.

    Both lactulose protocols live in the colonoscopy-mobile-lactulose-*
    template family — same design language as the public prep.giready.com
    pages, with lactulose-specific dosing baked in.
    """
    if protocol == "lactulose-infant":
        return TEMPLATES / f"colonoscopy-mobile-lactulose-infant.{lang}.html"
    if protocol == "lactulose-standard":
        return TEMPLATES / f"colonoscopy-mobile-lactulose-standard.{lang}.html"
    raise ValueError(f"Unknown lactulose protocol: {protocol!r}")


def render_band_cards(bands_by_id, lang, band_ids):
    """Build the landing-page band picker grid (one card per band)."""
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
    """Render a single per-band lactulose page."""
    protocol = band["protocol"]
    template_path = _band_template_for_lact(protocol, lang)
    src = template_path.read_text(encoding="utf-8")

    # Inject partials first ({{PARTIAL_PERSONALIZE}} for the date/time picker
    # CSS+JS — identical to what the public prep.giready.com pages use).
    for token, body in _load_partials(lang).items():
        src = src.replace(token, body)

    dose_replacements = build_lactulose_strings(band, lang, location=location)

    location_phone_tel = re.sub(r"\D", "", location.get("phone", ""))
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""
    youtube_url = qr["youtube_url_es" if lang == "es" else "youtube_url_en"]
    portal_url = qr["portal_url"]
    gikids_url = qr["gikids_url"]

    # No download PDF in Phase 1 — Phase 2 will add server-side personalized
    # PDF generation via the scheduler portal.
    pdf_button_block = ""

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
        "{{PDF_BUTTON_BLOCK}}":   pdf_button_block,
        # Lactulose-infant template uses {{WARNING_WEIGHT}} for the
        # "under 15 kg only" callout; lactulose-standard doesn't reference
        # it. Pull from the band; default to "15 kg" if absent.
        "{{WARNING_WEIGHT}}":     band.get(f"warning_weight_{lang}",
                                            band.get("warning_weight_en", "15 kg")),
    }

    return _do_replace(src, replacements, template_path.name)


def render_landing_page(template_path, lang, practice_cfg, bands_by_id, band_ids,
                        logo_src, lang_toggle_href, html_title):
    """Render the landing page. Reuses the public landing template but with
    a lactulose-specific intro banner."""
    src = template_path.read_text(encoding="utf-8")
    replacements = {
        **build_practice_placeholders(practice_cfg),
        "{{HTML_TITLE}}":       html_title,
        "{{LOGO_SRC}}":         logo_src,
        "{{LANG_TOGGLE_HREF}}": lang_toggle_href,
        "{{BAND_CARDS}}":       render_band_cards(bands_by_id, lang, band_ids),
    }
    out = _do_replace(src, replacements, template_path.name)

    # Add a prominent internal-only banner above the band picker so anyone
    # who lands here directly (rather than via a scheduler link) sees the
    # disclaimer immediately.
    if lang == "en":
        banner = (
            '<div style="background:#fff8e1;border:2px solid #f57c00;border-radius:6px;'
            'padding:14px 18px;margin:16px auto;max-width:720px;font-size:15px;line-height:1.45;">'
            '<strong>Internal — not for browsing.</strong><br>'
            'This is the lactulose backup prep. Use the personalized link given to you by the office. '
            'If you reached this page by accident, the standard MiraLAX prep is at '
            '<a href="https://prep.giready.com/">prep.giready.com</a>.</div>'
        )
    else:
        banner = (
            '<div style="background:#fff8e1;border:2px solid #f57c00;border-radius:6px;'
            'padding:14px 18px;margin:16px auto;max-width:720px;font-size:15px;line-height:1.45;">'
            '<strong>Interno — no para navegación.</strong><br>'
            'Esta es la preparación de respaldo con lactulosa. Use el enlace personalizado que le dio el consultorio. '
            'Si llegó aquí por accidente, la preparación estándar con MiraLAX está en '
            '<a href="https://prep.giready.com/es/">prep.giready.com/es/</a>.</div>'
        )
    out = out.replace("<body>", f"<body>\n{banner}", 1)
    return out


def clean_repo(repo_dir, band_ids, bands_by_id):
    """Remove obsolete files from a previous build run."""
    if not repo_dir.exists():
        return
    for f in repo_dir.glob("*.html"):
        f.unlink()
    redirects = repo_dir / "_redirects"
    if redirects.exists():
        redirects.unlink()
    for bid in band_ids:
        path = bands_by_id[bid]["mobile_path"]
        d = repo_dir / path
        if d.is_dir():
            shutil.rmtree(d)
    es_dir = repo_dir / "es"
    if es_dir.exists():
        for f in es_dir.glob("*.html"):
            f.unlink()
        for bid in band_ids:
            path = bands_by_id[bid]["mobile_path"]
            d = es_dir / path
            if d.is_dir():
                shutil.rmtree(d)


def build_for_repo(repo_dir, location_id, location, practice_cfg, bands_by_id, band_ids,
                   landing_template_en, landing_template_es):
    qr = practice_cfg["qr_targets"]

    repo_dir.mkdir(parents=True, exist_ok=True)
    clean_repo(repo_dir, band_ids, bands_by_id)
    (repo_dir / "es").mkdir(exist_ok=True)

    written = []

    # EN landing
    en_landing_html = render_landing_page(
        landing_template_en, "en", practice_cfg, bands_by_id, band_ids,
        logo_src="logo-pmch.png",
        lang_toggle_href="es/",
        html_title=HTML_TITLE_LANDING_EN,
    )
    p = repo_dir / "index.html"
    p.write_text(en_landing_html, encoding="utf-8")
    written.append(p)

    # ES landing
    es_landing_html = render_landing_page(
        landing_template_es, "es", practice_cfg, bands_by_id, band_ids,
        logo_src="../logo-pmch.png",
        lang_toggle_href="../",
        html_title=HTML_TITLE_LANDING_ES,
    )
    p = repo_dir / "es" / "index.html"
    p.write_text(es_landing_html, encoding="utf-8")
    written.append(p)

    # Per-band pages
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
        p.write_text(en_html, encoding="utf-8")
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
        p.write_text(es_html, encoding="utf-8")
        written.append(p)

    # Logo
    if LOGO_PATH.exists():
        shutil.copy(LOGO_PATH, repo_dir / "logo-pmch.png")
        written.append(repo_dir / "logo-pmch.png")

    return written


def write_repo_metadata(repo_dir, location, subdomain):
    """Create _headers, .gitignore, README.md if missing."""
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

    # Inverse sanity check vs the public builders: every BAND_ORDER entry
    # MUST be marked `public: false` so the scheduler-only contract holds.
    for bid in BAND_ORDER:
        if bid not in bands_by_id:
            sys.exit(f"band {bid!r} missing from data/dosing.yaml")
        if bands_by_id[bid].get("public", True):
            sys.exit(
                f"band {bid!r} is marked public (or missing the `public: false` flag) — "
                f"it must not appear in the lactulose builder's BAND_ORDER"
            )

    # Reuse the public landing template — the band-picker grid CSS is
    # identical, only the {{BAND_CARDS}} substitution differs and we
    # prepend an internal-only banner in render_landing_page.
    landing_template_en = TEMPLATES / "colonoscopy-mobile-landing.en.html"
    landing_template_es = TEMPLATES / "colonoscopy-mobile-landing.es.html"

    written_total = 0
    for location_id, repo_dir in SITES.items():
        if location_id not in locations:
            sys.exit(f"location {location_id!r} missing from data/dosing.yaml")
        location = locations[location_id]
        # Hidden subdomain naming: preplact (SCC) / preplact86 (PMCH).
        # Derived from the existing mobile_subdomain by prefixing with
        # "preplact" / suffixing with "86" — the convention `{subdomain}-giready`
        # is preserved.
        subdomain = "preplact" if location_id == "scc" else "preplact86"

        written = build_for_repo(
            repo_dir, location_id, location, practice_cfg, bands_by_id, BAND_ORDER,
            landing_template_en, landing_template_es,
        )
        written += write_repo_metadata(repo_dir, location, subdomain)
        written_total += len(written)
        print(f"  built {repo_dir} ({location_id} -> {subdomain}.giready.com): "
              f"{len(written)} files")

    print(f"\n{written_total} files written across {len(SITES)} hidden site repos.")


if __name__ == "__main__":
    main()
