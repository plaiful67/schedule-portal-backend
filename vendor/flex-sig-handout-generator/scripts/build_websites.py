#!/usr/bin/env python3
"""
Build the two static-site repos that back the flexible-sigmoidoscopy mobile QR
codes:
  ~/Desktop/peds-gi-system/flexsig-giready/    -> flexsig.giready.com   (SCC)
  ~/Desktop/peds-gi-system/flexsig86-giready/  -> flexsig86.giready.com (PMCH)

Layout (per repo, per language):
  index.html                    landing page — band picker grid
  <band_path>/index.html        per-band page (e.g. 20-40kg/index.html)
  es/index.html                 Spanish landing
  es/<band_path>/index.html     Spanish per-band page

Each band page contains the FULL flex-sig prep instructions (medications,
shopping list, 1-day-before diet, day-of enema, NPO table, resources). The
build pulls dose strings from `render.build_band_placeholders()` so the mobile
and print outputs stay in lock-step and there is one source of truth for the
clinical text.

Usage:
    python scripts/build_websites.py
"""

import base64
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml\n")
    sys.exit(1)

SKILL_DIR      = Path(__file__).resolve().parent.parent
TEMPLATES      = SKILL_DIR / "templates"
LOGO_PATH      = TEMPLATES / "logo-pmch.png"
PRACTICE_PATH  = SKILL_DIR / "practice.yaml"
PROCEDURE_PATH = SKILL_DIR / "data" / "procedure.yaml"

# Pre-rendered print PDFs to copy alongside each band's mobile page so users
# can print the canonical handout from the website. Populated by
# scripts/render.py; if missing the build still succeeds but the PDF link is
# dropped from that band.
PDF_REVIEW_DIR = Path.home() / "Desktop" / "peds-gi-system" / "flex-sig-pdf-review"

# Pull the single-source-of-truth render helpers from render.py so the mobile
# pages are guaranteed to use the same content as the print PDF, and the same
# conditional-block stripping for SIMPLE_DIET / FULL_DIET / INFANT_CALLOUT.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import (  # noqa: E402
    build_practice_placeholders,
    build_location_placeholders,
    build_band_placeholders,
    apply_conditional_blocks,
    lb_phrase,
    _inject_shared_mobile_a11y,
    _load_shared_partials,
)

# Per-location legacy target repo. Subdomains come from procedure.yaml's
# locations[*].mobile_subdomain. Used only when the build is NOT driven by the
# giready-sites monorepo (see _repo_out_dir below).
SITES = {
    "scc":  Path.home() / "Desktop" / "peds-gi-system" / "flexsig-giready",
    "pmch": Path.home() / "Desktop" / "peds-gi-system" / "flexsig86-giready",
}

# Default delivery target: the two legacy `flexsig*-giready` site repos on the
# Desktop (SITES above). When the giready-sites monorepo drives the build it
# sets GIREADY_SITES_OUT to its `sites/` root; then output goes to
# `<root>/<subdomain>/` (bare subdomain, one dir per subdomain) — matching the
# bowel-prep / egd builders. Content is identical either way; only the
# destination path changes. See giready-sites/data/sites.yaml (Item 5).
_SITES_OUT_ROOT = os.environ.get("GIREADY_SITES_OUT", "").strip()


def _repo_out_dir(location_id: str, subdomain: str) -> Path:
    if _SITES_OUT_ROOT:
        return Path(_SITES_OUT_ROOT) / subdomain
    return SITES[location_id]

# Order of bands as they appear in the landing picker (light -> heavy).
BAND_ORDER = ["under-15kg", "20-40kg", "over-40kg"]

# Map band id -> URL path on the mobile site. The procedure.yaml doesn't
# carry a `mobile_path` field (unlike bowel-prep's dosing.yaml), so the
# mapping lives here.
BAND_MOBILE_PATH = {
    "under-15kg": "u15kg",
    "20-40kg":    "20-40kg",
    "over-40kg":  "over-40kg",
}

# Compact label shown at the top of each band page (in the H1 hero).
# Concise — the lb-equivalent appears as subtitle.
BAND_LABELS = {
    "under-15kg": {"en": "Under 15 kg",  "es": "Menos de 15 kg"},
    "20-40kg":    {"en": "15–40 kg",     "es": "15–40 kg"},
    "over-40kg":  {"en": "Over 40 kg",   "es": "Más de 40 kg"},
}

# Protocol-disambiguation note appended after the (derived) lb range, split
# back out into a coral tag by render_band_cards on " · ".
BAND_LB_NOTE = {
    "under-15kg": {"en": " · saline enema given by staff",
                   "es": " · enema salina por el personal"},
}


def band_lb(band, lang):
    """lb subtitle = derived lb range (from kg cutpoints) + optional note."""
    return lb_phrase(band, lang) + BAND_LB_NOTE.get(band["id"], {}).get(lang, "")

HTML_TITLE_BAND_EN = "Flexible Sigmoidoscopy Prep — {label} — What to Expect"
HTML_TITLE_BAND_ES = "Preparación para Sigmoidoscopia Flexible — {label} — Qué Esperar"
HTML_TITLE_LANDING_EN = "Flexible Sigmoidoscopy Prep — What to Expect"
HTML_TITLE_LANDING_ES = "Preparación para Sigmoidoscopia Flexible — Qué Esperar"

# Canonical giready security-header block — single source for all skills:
# ~/peds-gi-prep-system/shared/gi_header_config.py (ends the former 3-way CSP mirror).
for _cand in (Path.home() / "peds-gi-prep-system" / "shared",):
    if (_cand / "gi_header_config.py").exists():
        sys.path.insert(0, str(_cand))
        break
from gi_header_config import write_headers  # noqa: E402

GITIGNORE_CONTENT = """.DS_Store
*.swp
.idea/
.vscode/
"""

README_TEMPLATE = """# {repo_name}

Mobile-friendly website for the **{location_name}** flexible-sigmoidoscopy bowel-prep handout.

- Live at: **https://{subdomain}.giready.com/**
- Spanish version: **https://{subdomain}.giready.com/es/**

The HTML is generated from the [`flex-sig-handout-generator` skill](../../.claude/skills/flex-sig-handout-generator/) — edit `templates/flex-sig-mobile*.html`, `data/procedure.yaml`, or `practice.yaml`, then re-run `python scripts/build_websites.py` from the skill folder. Don't hand-edit the HTML in this repo; changes will be overwritten.

The site is multi-page — one HTML per weight band, served at its own path
(e.g. `/20-40kg/`). The root `/` is a band-picker landing. QR codes printed
in the handouts encode the per-band paths directly.

## Deploy
Cloudflare Pages, connected to this GitHub repo. Build settings: framework = None, build command = (empty), output directory = `/`.
"""


def _load_yaml(path):
    if not path.exists():
        sys.exit(f"ERROR: required file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_band_cards(bands_by_id, lang, band_ids):
    """Build the landing-page band picker grid (one card per band).

    Calm "lb-first" card: the pound range is the serif hero, the kg band is
    the small secondary line. A trailing " · ..." note becomes a coral tag.
    """
    arrow_svg = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                 'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
                 '<path d="M5 12h14M13 6l6 6-6 6"/></svg>')
    n = len(band_ids)
    cards = []
    for i, bid in enumerate(band_ids):
        path = BAND_MOBILE_PATH[bid]
        label = BAND_LABELS[bid][lang]
        lb_main, _, extra = band_lb(bands_by_id[bid], lang).partition(" · ")
        tag_html = f'<span class="tag">{extra.strip()}</span>' if extra.strip() else ""
        wide = " wide" if (n % 2 == 1 and i == n - 1) else ""
        cards.append(
            f'  <a class="band{wide}" href="{path}/">\n'
            f'    <div class="info"><h2>{lb_main}</h2><div class="kg">{label}</div>{tag_html}</div>\n'
            f'    <span class="arr">{arrow_svg}</span>\n'
            f'  </a>'
        )
    return "\n".join(cards)


def _do_replace(src, replacements, template_label):
    out = src
    for token, value in replacements.items():
        out = out.replace(token, value)
    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", out)
    if unreplaced:
        raise RuntimeError(
            f"Unreplaced placeholders in {template_label}: {sorted(set(unreplaced))}"
        )
    return out


def find_handout_pdf(band, location_id, lang):
    """Locate the rendered print PDF for this band/location/lang.

    The flex-sig PDF layout is:
        flex-sig-pdf-review/<LOC>/<Lang>/flex-sig-<stem>-<LOC>[-es]-print.pdf

    Returns the source Path, or None if the PDF has not been rendered yet.
    The build keeps going either way; a missing PDF just means the band
    page omits the download link (rather than hard-failing the build).
    """
    stem = band["filename_stem"]  # e.g. "20-40kg", "under-15kg"
    loc_upper = location_id.upper()
    lang_dir = "English" if lang == "en" else "Spanish"
    es_suffix = "-es" if lang == "es" else ""

    base = PDF_REVIEW_DIR / loc_upper / lang_dir
    if not base.exists():
        return None

    # Calm is the default download-PDF theme; HANDOUT_PDF_THEME=color reverts.
    calm_suffix = "-calm" if os.environ.get("HANDOUT_PDF_THEME", "calm").strip().lower() == "calm" else ""
    pdf_name = f"flex-sig-{stem}-{loc_upper}{es_suffix}-print{calm_suffix}.pdf"
    candidate = base / pdf_name
    return candidate if candidate.exists() else None


PDF_BUTTON_LABEL = {
    "en": "Download printable PDF",
    "es": "Descargar PDF imprimible",
}

# Short tokens used in the patient-facing download filename — chosen so the
# saved PDF is self-describing on a phone's downloads list. PMCH gets
# "StVincent" rather than "PMCH" because parents recognize the hospital name
# more easily than the abbreviation.
PDF_LOCATION_SHORT = {"scc": "SCC", "pmch": "StVincent"}


def pdf_download_name(band, location_id):
    """Build the descriptive filename a patient sees on download.

    Example: band=20-40kg loc=scc → Flex_Sig_Prep_20-40kg_SCC.pdf
    """
    band_slug = band.get("filename_stem", band["id"])
    loc_short = PDF_LOCATION_SHORT.get(location_id, location_id.upper())
    return f"Flex_Sig_Prep_{band_slug}_{loc_short}.pdf"


def render_band_page(lang, procedure, band, location, practice_cfg, qr,
                     logo_src, lang_toggle_href, landing_href, html_title,
                     handout_pdf_href="", handout_pdf_download_name=""):
    """Render a single per-band mobile page.

    Pulls all dose / location / practice strings from render.py's helpers
    so the mobile page stays in lock-step with the print PDF, then strips
    the per-band conditional blocks (SIMPLE_DIET / FULL_DIET / INFANT_CALLOUT)
    the same way render.render_pdf does.
    """
    template_path = TEMPLATES / f"flex-sig-mobile.{lang}.html"
    src = template_path.read_text(encoding="utf-8")

    # Apply conditional blocks BEFORE token substitution — same as render.py.
    flags = {
        "SIMPLE_DIET":    bool(band.get("simple_diet")),
        "FULL_DIET":      not bool(band.get("simple_diet")),
        "INFANT_CALLOUT": bool(band.get("infant_callout")),
    }
    src = apply_conditional_blocks(src, flags)

    location_phone_tel = re.sub(r"\D", "", location.get("phone", ""))
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""
    youtube_url = qr["youtube_url_es" if lang == "es" else "youtube_url_en"]
    portal_url = qr["portal_url"]
    gikids_url = qr["gikids_url"]

    if handout_pdf_href:
        download_attr = f' download="{handout_pdf_download_name}"' if handout_pdf_download_name else ""
        pdf_button_block = (
            f'<a class="pdf-download" href="{handout_pdf_href}"{download_attr} '
            f'target="_blank" rel="noopener">'
            f'<span aria-hidden="true">\U0001F4C4</span> '
            f'{PDF_BUTTON_LABEL[lang]}</a>'
        )
    else:
        pdf_button_block = ""

    replacements = {
        # Shared partials first (e.g. {{PARTIAL_NPO_TABLE_NOTHICKENER_MOBILE}});
        # their inner tokens (NPO_*) resolve from the band placeholders below.
        **_load_shared_partials(lang),
        **build_practice_placeholders(lang),
        **build_location_placeholders(location, lang),
        **build_band_placeholders(procedure, band, lang, location=location),
        # Override the print-tuned title/label with the mobile-tuned versions
        # from BAND_LABELS / HTML_TITLE_BAND_*.
        "{{HTML_TITLE}}":         html_title,
        "{{BAND_LABEL}}":         BAND_LABELS[band["id"]][lang],
        "{{BAND_LB}}":            band_lb(band, lang),
        "{{LOGO_SRC}}":           logo_src,
        "{{LANG_TOGGLE_HREF}}":   lang_toggle_href,
        "{{LANDING_HREF}}":       landing_href,
        "{{MAPS_URL}}":           maps_url,
        "{{YOUTUBE_URL}}":        youtube_url,
        "{{PORTAL_URL}}":         portal_url,
        "{{GIKIDS_URL}}":         gikids_url,
        "{{LOCATION_PHONE_TEL}}": location_phone_tel,
        "{{PDF_BUTTON_BLOCK}}":   pdf_button_block,
    }

    return _do_replace(src, replacements, template_path.name)


def render_landing_page(template_path, lang, bands_by_id, band_ids,
                        logo_src, lang_toggle_href, html_title):
    src = template_path.read_text(encoding="utf-8")
    replacements = {
        **build_practice_placeholders(lang),
        "{{HTML_TITLE}}":       html_title,
        "{{LOGO_SRC}}":         logo_src,
        "{{LANG_TOGGLE_HREF}}": lang_toggle_href,
        "{{BAND_CARDS}}":       render_band_cards(bands_by_id, lang, band_ids),
    }
    return _do_replace(src, replacements, template_path.name)


# ---------------------------------------------------------------------------
# Build orchestration
# ---------------------------------------------------------------------------

def clean_repo(repo_dir, band_ids):
    """Remove obsolete files from a previous build run.

    Removes top-level *.html files, the en+es per-band folders (whatever
    they may be named in BAND_MOBILE_PATH), the legacy _redirects file,
    and the es/<band>/ folders. Preserves _headers, .gitignore, README.md,
    logo-pmch.png, and the .git directory.
    """
    if not repo_dir.exists():
        return
    for f in repo_dir.glob("*.html"):
        f.unlink()
    redirects = repo_dir / "_redirects"
    if redirects.exists():
        redirects.unlink()
    for bid in band_ids:
        path = BAND_MOBILE_PATH[bid]
        d = repo_dir / path
        if d.is_dir():
            shutil.rmtree(d)
    es_dir = repo_dir / "es"
    if es_dir.exists():
        for f in es_dir.glob("*.html"):
            f.unlink()
        for bid in band_ids:
            path = BAND_MOBILE_PATH[bid]
            d = es_dir / path
            if d.is_dir():
                shutil.rmtree(d)


_ANALYTICS_SNIPPET = (
    '<meta name="giready:context" content=\'{ctx}\'>\n'
    '  <script defer src="https://analytics.giready.com/gi.js" data-site="{site}"></script>\n'
    '  <script defer src="https://analytics.giready.com/survey.js" data-site="{site}" data-survey-delay="90"></script>'
)

_ANALYTICS_SITE_BY_LOC = {
    "scc":  "flexsig",
    "pmch": "flexsig86",
}


def _inject_analytics(html, location_id, lang, band_id=""):
    """Inject the giready analytics + survey embed snippets before </head>. Idempotent."""
    import json
    # Shared WCAG 2.1 AA base on every mobile page (independent of analytics);
    # _inject_analytics is the single last-mile transform before write_text.
    html = _inject_shared_mobile_a11y(html)
    site = _ANALYTICS_SITE_BY_LOC.get(location_id)
    if not site:
        return html
    ctx = json.dumps({
        "procedure": "flex-sig",
        "band": band_id,
        "location": location_id,
        "lang": lang,
        "source": "web",
    }, separators=(",", ":"))
    snippet = _ANALYTICS_SNIPPET.format(site=site, ctx=ctx)
    if 'giready:context' in html and f'data-site="{site}"' in html and 'survey.js' in html:
        return html
    return html.replace("</head>", f"  {snippet}\n</head>", 1)


def build_for_repo(repo_dir, location_id, location, practice_cfg, procedure,
                   bands_by_id, band_ids,
                   landing_template_en, landing_template_es,
                   landing_title_en, landing_title_es,
                   band_title_en_fmt, band_title_es_fmt):
    """Build all pages for a single repo (single location)."""
    qr = practice_cfg["qr_targets"]

    repo_dir.mkdir(parents=True, exist_ok=True)
    clean_repo(repo_dir, band_ids)
    (repo_dir / "es").mkdir(exist_ok=True)

    written = []

    # --- EN landing (root index.html) --------------------------------------
    en_landing_html = render_landing_page(
        landing_template_en, "en", bands_by_id, band_ids,
        logo_src="logo-pmch.png",
        lang_toggle_href="es/",
        html_title=landing_title_en,
    )
    p = repo_dir / "index.html"
    p.write_text(_inject_analytics(en_landing_html, location_id, "en"), encoding="utf-8")
    written.append(p)

    # --- ES landing (es/index.html) ----------------------------------------
    es_landing_html = render_landing_page(
        landing_template_es, "es", bands_by_id, band_ids,
        logo_src="../logo-pmch.png",
        lang_toggle_href="../",
        html_title=landing_title_es,
    )
    p = repo_dir / "es" / "index.html"
    p.write_text(_inject_analytics(es_landing_html, location_id, "es"), encoding="utf-8")
    written.append(p)

    # --- Per-band pages ----------------------------------------------------
    for bid in band_ids:
        band = bands_by_id[bid]
        path = BAND_MOBILE_PATH[bid]
        label_en = BAND_LABELS[bid]["en"]
        label_es = BAND_LABELS[bid]["es"]

        # EN: <repo>/<path>/index.html
        en_dir = repo_dir / path
        # Wipe first so overwrites don't trigger macOS NSFileVersion
        # side-write of "handout 2.pdf" / "index 2.html" duplicates.
        shutil.rmtree(en_dir, ignore_errors=True)
        en_dir.mkdir(parents=True, exist_ok=True)
        en_pdf_src = find_handout_pdf(band, location_id, "en")
        en_pdf_href = ""
        if en_pdf_src:
            shutil.copy(en_pdf_src, en_dir / "handout.pdf")
            en_pdf_href = "handout.pdf"
            written.append(en_dir / "handout.pdf")
        en_html = render_band_page(
            "en", procedure, band, location, practice_cfg, qr,
            logo_src="../logo-pmch.png",
            lang_toggle_href=f"../es/{path}/",
            landing_href="../",
            html_title=band_title_en_fmt.format(label=label_en),
            handout_pdf_href=en_pdf_href,
            handout_pdf_download_name=pdf_download_name(band, location_id),
        )
        p = en_dir / "index.html"
        p.write_text(_inject_analytics(en_html, location_id, "en", bid), encoding="utf-8")
        written.append(p)

        # ES: <repo>/es/<path>/index.html
        es_dir = repo_dir / "es" / path
        shutil.rmtree(es_dir, ignore_errors=True)
        es_dir.mkdir(parents=True, exist_ok=True)
        es_pdf_src = find_handout_pdf(band, location_id, "es")
        es_pdf_href = ""
        if es_pdf_src:
            shutil.copy(es_pdf_src, es_dir / "handout.pdf")
            es_pdf_href = "handout.pdf"
            written.append(es_dir / "handout.pdf")
        es_html = render_band_page(
            "es", procedure, band, location, practice_cfg, qr,
            logo_src="../../logo-pmch.png",
            lang_toggle_href=f"../../{path}/",
            landing_href="../",
            html_title=band_title_es_fmt.format(label=label_es),
            handout_pdf_href=es_pdf_href,
            handout_pdf_download_name=pdf_download_name(band, location_id),
        )
        p = es_dir / "index.html"
        p.write_text(_inject_analytics(es_html, location_id, "es", bid), encoding="utf-8")
        written.append(p)

    # Logo
    if LOGO_PATH.exists():
        shutil.copy(LOGO_PATH, repo_dir / "logo-pmch.png")
        written.append(repo_dir / "logo-pmch.png")

    return written


def write_repo_metadata(repo_dir, location, subdomain):
    """Create .gitignore/README.md if missing; always rewrite _headers."""
    written = []
    written += write_headers(repo_dir)

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
    practice_cfg  = _load_yaml(PRACTICE_PATH)
    procedure_cfg = _load_yaml(PROCEDURE_PATH)
    locations     = procedure_cfg["locations"]
    procedure     = procedure_cfg["procedures"]["flex-sig"]
    bands_list    = procedure.get("bands", [])
    bands_by_id   = {b["id"]: b for b in bands_list}

    # Sanity check that every BAND_ORDER entry exists in the data.
    for bid in BAND_ORDER:
        if bid not in bands_by_id:
            sys.exit(f"band {bid!r} missing from data/procedure.yaml")

    landing_template_en = TEMPLATES / "flex-sig-mobile-landing.en.html"
    landing_template_es = TEMPLATES / "flex-sig-mobile-landing.es.html"

    written_total = 0
    for location_id in SITES:
        if location_id not in locations:
            sys.exit(f"location {location_id!r} missing from data/procedure.yaml")
        location = locations[location_id]
        subdomain = location.get("mobile_subdomain", location_id)
        # Legacy Desktop repo, OR <GIREADY_SITES_OUT>/<subdomain> when the
        # monorepo drives the build (Item 5).
        repo_dir = _repo_out_dir(location_id, subdomain)

        written = build_for_repo(
            repo_dir, location_id, location, practice_cfg, procedure,
            bands_by_id, BAND_ORDER,
            landing_template_en, landing_template_es,
            HTML_TITLE_LANDING_EN, HTML_TITLE_LANDING_ES,
            HTML_TITLE_BAND_EN, HTML_TITLE_BAND_ES,
        )
        written += write_repo_metadata(repo_dir, location, subdomain)
        written_total += len(written)
        print(f"  built {repo_dir} ({location_id} -> {subdomain}.giready.com): "
              f"{len(written)} files")

    print(f"\n{written_total} files written across {len(SITES)} site repos.")


if __name__ == "__main__":
    main()
