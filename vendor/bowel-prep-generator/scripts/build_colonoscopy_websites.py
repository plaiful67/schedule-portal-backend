#!/usr/bin/env python3
"""
Build the two static-site repos that back the colonoscopy-only mobile QR codes:
  ~/Desktop/prep-giready/    -> prep.giready.com   (SCC content)
  ~/Desktop/prep86-giready/  -> prep86.giready.com (PMCH content)

Layout (per repo, per language):
  index.html                    landing page — band picker grid
  <band_path>/index.html        per-band page (e.g. u30kg/index.html)
  es/index.html                 Spanish landing
  es/<band_path>/index.html     Spanish per-band page

Each band page now contains the FULL algorithm — the same step-by-step
schedule, dose-by-time, diet tables, and sample-meals grid that the printed
PDF handout has. Patients should never need to flip back to the print
handout to find a number.

The script reuses `render.build_strings()` / `render.build_infant_strings()`
to compute the dose-related placeholders (so the mobile and print outputs
stay in lock-step and there's only one source of truth for dosing prose).

Usage:
    python scripts/build_colonoscopy_websites.py
"""

import re
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml\n")
    sys.exit(1)

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
LOGO_PATH = TEMPLATES / "logo-pmch.png"
PRACTICE_PATH = SKILL_DIR / "practice.yaml"
DOSING_PATH = SKILL_DIR / "data" / "dosing.yaml"

# Pre-rendered print PDFs to copy alongside each band's mobile page so users
# can print the canonical handout from the website. Populated by
# scripts/render.py; if missing the build still succeeds but the PDF link is
# dropped from that band.
PDF_REVIEW_DIR = Path.home() / "Desktop" / "peds-gi-system" / "bowel-prep-pdf-review"

# Pull the single-source-of-truth render helpers from render.py so the mobile
# pages are guaranteed to use the same dose phrasing and the same
# pre-rendered "2 Days Before" HTML block as the print PDF.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import build_strings, build_infant_strings, _load_partials, build_calendar_events_json, lb_phrase, _inject_shared_mobile_a11y  # noqa: E402

# Per-location target repo. The subdomain comes from `mobile_subdomain`
# in dosing.yaml (NOT mobile_subdomain_combined, which points at the
# combined egdcolon* sites).
SITES = {
    "scc":  Path.home() / "Desktop" / "peds-gi-system" / "prep-giready",
    "pmch": Path.home() / "Desktop" / "peds-gi-system" / "prep86-giready",
}

# Order of bands as they appear in the landing picker (light -> heavy,
# infant variants first since they are the most distinct content).
BAND_ORDER = [
    "under-15",         # u15kgPEG  -- MiraLAX option for <15 kg
    "under-15-enema",   # u15kgEnema -- saline enema option for <15 kg
    "15-20",            # u20kg
    "21-30",            # u30kg
    "31-40",            # u40kg
    "41-50",            # u50kg
    "over-50",          # o50kg
]

# Compact label shown at the top of each band page (in the H1 hero, after
# the procedure name). Concise — the lb-equivalent appears as subtitle.
BAND_LABELS = {
    "under-15":       {"en": "Under 15 kg",         "es": "Menos de 15 kg"},
    "under-15-enema": {"en": "Under 15 kg",         "es": "Menos de 15 kg"},
    "15-20":          {"en": "15–20 kg",            "es": "15–20 kg"},
    "21-30":          {"en": "21–30 kg",            "es": "21–30 kg"},
    "31-40":          {"en": "31–40 kg",            "es": "31–40 kg"},
    "41-50":          {"en": "41–50 kg",            "es": "41–50 kg"},
    "over-50":        {"en": "Over 50 kg",          "es": "Más de 50 kg"},
}

# lb-equivalent (shown bracketed inline with the kg label so it pops) is
# DERIVED from each band's kg cutpoints via render.lb_phrase() — single source
# of truth, contiguous by construction. See dosing.yaml's cutpoint header.

# Protocol disambiguation note (shown as the page subtitle, only when the
# kg range alone is ambiguous — i.e. the two infant variants).
BAND_NOTE = {
    "under-15":       {"en": "MiraLAX option",
                       "es": "Opción MiraLAX"},
    "under-15-enema": {"en": "Clear liquids + saline enema",
                       "es": "Líquidos claros + enema salino"},
    "15-20":          {"en": "", "es": ""},
    "21-30":          {"en": "", "es": ""},
    "31-40":          {"en": "", "es": ""},
    "41-50":          {"en": "", "es": ""},
    "over-50":        {"en": "", "es": ""},
}

HTML_TITLE_BAND_EN = "Colonoscopy Prep — {label} — What to Expect"
HTML_TITLE_BAND_ES = "Preparación para Colonoscopia — {label} — Qué Esperar"
HTML_TITLE_LANDING_EN = "Colonoscopy Bowel Prep — What to Expect"
HTML_TITLE_LANDING_ES = "Preparación para Colonoscopia — Qué Esperar"

from header_config import write_headers  # noqa: E402  (single source of truth)

GITIGNORE_CONTENT = """.DS_Store
*.swp
.idea/
.vscode/
"""

README_TEMPLATE = """# {repo_name}

Mobile-friendly website for the **{location_name}** colonoscopy bowel-prep handout.

- Live at: **https://{subdomain}.giready.com/**
- Spanish version: **https://{subdomain}.giready.com/es/**

The HTML is generated from the [`bowel-prep-generator` skill](../../.claude/skills/bowel-prep-generator/) — edit `templates/colonoscopy-mobile*.html`, `data/dosing.yaml`, or `practice.yaml`, then re-run `python scripts/build_colonoscopy_websites.py` from the skill folder. Don't hand-edit the HTML in this repo; changes will be overwritten.

The site is multi-page — one HTML per weight band, served at its own path
(e.g. `/u30kg/`). The root `/` is a band-picker landing. QR codes printed in
the handouts encode the per-band paths directly.

## Deploy
Cloudflare Pages, connected to this GitHub repo. Build settings: framework = None, build command = (empty), output directory = `/`.
"""


def _load_yaml(path):
    if not path.exists():
        sys.exit(f"ERROR: required file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_practice_placeholders(practice_cfg, lang="en"):
    p = practice_cfg["practice"]
    return {
        "{{PRACTICE_LOGO_ALT}}": p.get("logo_alt", ""),
        "{{DISCLAIMER}}":        p.get(f"disclaimer_{lang}") or p.get("disclaimer_en") or "",
    }


def build_location_placeholders(location, lang):
    return {
        "{{LOCATION_NAME}}":        location.get(f"name_{lang}", location.get("name_en", "")),
        "{{LOCATION_ADDRESS}}":     location.get("address", ""),
        "{{LOCATION_PHONE}}":       location.get("phone", ""),
        "{{LOCATION_PHONE_LABEL}}": location.get(f"phone_label_{lang}",
                                                 location.get("phone_label_en", "")),
        "{{LOCATION_ARRIVAL}}":     location.get(f"arrival_{lang}",
                                                 location.get("arrival_en", "")),
        "{{NPO_CLEARS_HOURS}}":     str(location.get("clears_npo_hours", 2)),
        "{{LOCATION_ARRIVAL_MINUTES}}":         str(location.get("arrival_minutes_before", 60)),
        "{{LOCATION_ARRIVAL_FACILITY_SHORT}}":  location.get(f"arrival_facility_short_{lang}",
                                                              location.get("arrival_facility_short_en", "the surgery center")),
    }


def render_band_cards(bands_by_id, lang, band_ids):
    """Build the landing-page band picker grid (one card per band).

    Calm "lb-first" card: the pound range is the serif hero, the kg band is
    the small secondary line, the protocol note (if any) is a coral tag.
    """
    arrow_svg = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                 'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
                 '<path d="M5 12h14M13 6l6 6-6 6"/></svg>')
    n = len(band_ids)
    cards = []
    for i, bid in enumerate(band_ids):
        path = bands_by_id[bid]["mobile_path"]
        label = BAND_LABELS[bid][lang]
        lb = lb_phrase(bands_by_id[bid], lang)
        note = BAND_NOTE[bid][lang]
        tag_html = f'<span class="tag">{note}</span>' if note else ""
        wide = " wide" if (n % 2 == 1 and i == n - 1) else ""
        cards.append(
            f'  <a class="band{wide}" href="{path}/">\n'
            f'    <div class="info"><h3>{lb}</h3><div class="kg">{label}</div>{tag_html}</div>\n'
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


def find_handout_pdf(band, location_id, lang, family):
    """Locate the rendered print PDF for this band/location/lang/family.

    Returns the source Path, or None if the PDF has not been rendered yet.
    The build keeps going either way; a missing PDF just means the band
    page omits the download link (rather than hard-failing the build).
    """
    stem = band["filename_stem"]  # e.g. "31-40kg", "under-15kg-enema"
    loc_upper = location_id.upper()
    lang_dir = "English" if lang == "en" else "Spanish"
    variant = f"{loc_upper}-combined-color" if family == "combined" else f"{loc_upper}-color"

    base = PDF_REVIEW_DIR / variant / lang_dir
    if not base.exists():
        return None

    es_suffix = "-es" if lang == "es" else ""
    family_suffix = "-combined" if family == "combined" else ""
    pdf_name = f"bowel-prep-{stem}-{loc_upper}{es_suffix}-print{family_suffix}.pdf"

    # The band-label folder uses friendly names ("31-40 kg (68-88 Lb)") that
    # we don't track here, so glob across the variant dir to find the file.
    # A lb-range change can leave an orphaned old folder ("(33-44 Lb)") next to
    # the current one ("(33-45 Lb)"); both match the band's filename, and the
    # alphabetically-first one is the stale orphan. Prefer the most recently
    # rendered file so the fresh (tagged) PDF always wins.
    matches = list(base.glob(f"*/{pdf_name}"))
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def _band_template_for(protocol, lang, family):
    """Pick the right per-band mobile template for this band's protocol.

    `family` is "colonoscopy" (single-procedure) or "combined"
    (EGD + colonoscopy). Both families ship per-protocol templates
    (standard / infant / infant-enema).
    """
    if family == "combined":
        if protocol == "standard":
            return TEMPLATES / f"combined-mobile.{lang}.html"
        if protocol == "infant":
            return TEMPLATES / f"combined-mobile-infant.{lang}.html"
        if protocol == "infant-enema":
            return TEMPLATES / f"combined-mobile-infant-enema.{lang}.html"
        raise ValueError(f"Unknown protocol for combined family: {protocol!r}")
    if protocol == "standard":
        return TEMPLATES / f"colonoscopy-mobile.{lang}.html"
    if protocol == "infant":
        return TEMPLATES / f"colonoscopy-mobile-infant.{lang}.html"
    if protocol == "infant-enema":
        return TEMPLATES / f"colonoscopy-mobile-infant-enema.{lang}.html"
    raise ValueError(f"Unknown protocol: {protocol!r}")


PDF_BUTTON_LABEL = {
    "en": "Download printable PDF",
    "es": "Descargar PDF imprimible",
}

# Short tokens used in the patient-facing download filename — chosen so the
# saved PDF is self-describing on a phone's downloads list. PMCH gets
# "StVincent" rather than "PMCH" because parents recognize the hospital name
# more easily than the abbreviation.
PDF_LOCATION_SHORT = {"scc": "SCC", "pmch": "StVincent"}


def pdf_download_name(family, band, location_id):
    """Build the descriptive filename a patient sees on download.

    Examples:
        family=colonoscopy  band=31-40kg  loc=scc   → Colonoscopy_Prep_31-40kg_SCC.pdf
        family=combined     band=over-50kg loc=pmch → EGD_Colonoscopy_Prep_over-50kg_StVincent.pdf
    """
    band_slug = band.get("filename_stem", band["id"])
    loc_short = PDF_LOCATION_SHORT.get(location_id, location_id.upper())
    prefix = "EGD_Colonoscopy_Prep" if family == "combined" else "Colonoscopy_Prep"
    return f"{prefix}_{band_slug}_{loc_short}.pdf"


def render_band_page(lang, band, location, practice_cfg, qr,
                     logo_src, lang_toggle_href, landing_href, html_title,
                     family="colonoscopy", handout_pdf_href="",
                     handout_pdf_download_name=""):
    """Render a single per-band page.

    Picks the template by protocol, then computes the dose placeholders
    from render.py so the page stays in lock-step with the print PDF.
    """
    protocol = band["protocol"]
    template_path = _band_template_for(protocol, lang, family=family)
    src = template_path.read_text(encoding="utf-8")

    # Inject partials first (e.g. {{PARTIAL_PERSONALIZE}} -> the personalize
    # CSS+JS partial body, which has the QR-code library inlined so it parses
    # with the page and the print-time QR is in the DOM by the time anyone
    # opens Cmd-P).
    for token, body in _load_partials(lang).items():
        src = src.replace(token, body)

    # Source-of-truth dose strings — same call render.render_band uses.
    if protocol == "standard":
        dose_replacements = build_strings(band, lang)
    else:  # infant or infant-enema
        dose_replacements = build_infant_strings(band, lang)

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
        **build_practice_placeholders(practice_cfg, lang),
        **build_location_placeholders(location, lang),
        **dose_replacements,
        "{{PZ_EVENTS_JSON}}":   build_calendar_events_json(band, lang, location, family=family),
        # render.build_strings populates {{HTML_TITLE}} and {{BAND_LABEL}}
        # already, but those values are tuned for the printed handout
        # (e.g. include the lb range). Override them with the
        # mobile-tuned versions used in BAND_LABELS/HTML_TITLE_BAND_*.
        "{{HTML_TITLE}}":         html_title,
        "{{BAND_LABEL}}":         BAND_LABELS[band["id"]][lang],
        "{{LOGO_SRC}}":           logo_src,
        "{{LANG_TOGGLE_HREF}}":   lang_toggle_href,
        "{{LANDING_HREF}}":       landing_href,
        "{{BAND_LB}}":            lb_phrase(band, lang, "bracket"),
        "{{BAND_NOTE}}":          BAND_NOTE[band["id"]][lang],
        "{{MAPS_URL}}":           maps_url,
        "{{YOUTUBE_URL}}":        youtube_url,
        "{{PORTAL_URL}}":         portal_url,
        "{{GIKIDS_URL}}":         gikids_url,
        "{{LOCATION_PHONE_TEL}}": location_phone_tel,
        "{{PDF_BUTTON_BLOCK}}":   pdf_button_block,
    }

    # Drop DOCX-only placeholders that the build dict still carries from
    # build_strings — they're not used in mobile templates and otherwise
    # would trip the "unreplaced placeholder" check vacuously. (They
    # *would* also pass through harmlessly since the templates don't
    # reference them, so this is just defensive cleanup.)
    return _do_replace(src, replacements, template_path.name)


def render_landing_page(template_path, lang, practice_cfg, bands_by_id, band_ids,
                        logo_src, lang_toggle_href, html_title):
    src = template_path.read_text(encoding="utf-8")
    replacements = {
        **build_practice_placeholders(practice_cfg, lang),
        "{{HTML_TITLE}}":       html_title,
        "{{LOGO_SRC}}":         logo_src,
        "{{LANG_TOGGLE_HREF}}": lang_toggle_href,
        "{{BAND_CARDS}}":       render_band_cards(bands_by_id, lang, band_ids),
    }
    return _do_replace(src, replacements, template_path.name)


# ---------------------------------------------------------------------------
# Build orchestration
# ---------------------------------------------------------------------------

def clean_repo(repo_dir, band_ids, bands_by_id):
    """Remove obsolete files from a previous build run.

    Removes top-level *.html files, the en+es per-band folders (whatever
    they may be named in the data), the legacy _redirects file, and the
    es/<band>/ folders. Preserves _headers, .gitignore, README.md,
    logo-pmch.png, and the .git directory.
    """
    if not repo_dir.exists():
        return
    # Remove top-level *.html
    for f in repo_dir.glob("*.html"):
        f.unlink()
    # Remove legacy _redirects
    redirects = repo_dir / "_redirects"
    if redirects.exists():
        redirects.unlink()
    # Remove per-band dirs at top level (only ones we know about — never
    # blindly remove anything else). The dirs include any handout.pdf the
    # previous build copied in.
    for bid in band_ids:
        path = bands_by_id[bid]["mobile_path"]
        d = repo_dir / path
        if d.is_dir():
            shutil.rmtree(d)
    # Remove es subdirectory contents (HTML + per-band folders) but keep
    # the dir itself.
    es_dir = repo_dir / "es"
    if es_dir.exists():
        for f in es_dir.glob("*.html"):
            f.unlink()
        for bid in band_ids:
            path = bands_by_id[bid]["mobile_path"]
            d = es_dir / path
            if d.is_dir():
                shutil.rmtree(d)


_ANALYTICS_SNIPPET = (
    '<meta name="giready:context" content=\'{ctx}\'>\n'
    '  <script defer src="https://analytics.giready.com/gi.js" data-site="{site}"></script>\n'
    '  <script defer src="https://analytics.giready.com/survey.js" data-site="{site}" data-survey-delay="90"></script>'
)

_ANALYTICS_SITE_BY_FAMILY_LOC = {
    ("colonoscopy", "scc"):  "prep",
    ("colonoscopy", "pmch"): "prep86",
    ("combined",    "scc"):  "egdcolon",
    ("combined",    "pmch"): "egdcolon86",
    # Hidden prep variants (lactulose / CLENPIQ / Suprep). Currently
    # near-zero traffic; analytics + survey ship for parity so the data
    # is in place when a variant graduates to public.
    ("lactulose",          "scc"):  "preplact",
    ("lactulose",          "pmch"): "preplact86",
    ("lactulose-combined", "scc"):  "egdcolonlact",
    ("lactulose-combined", "pmch"): "egdcolonlact86",
    ("clenpiq",            "scc"):  "prepclenpiq",
    ("clenpiq",            "pmch"): "prepclenpiq86",
    ("clenpiq-combined",   "scc"):  "egdcolonclenpiq",
    ("clenpiq-combined",   "pmch"): "egdcolonclenpiq86",
    ("suprep",             "scc"):  "prepsuprep",
    ("suprep",             "pmch"): "prepsuprep86",
    ("suprep-combined",    "scc"):  "egdcolonsuprep",
    ("suprep-combined",    "pmch"): "egdcolonsuprep86",
}

_PROCEDURE_BY_FAMILY = {
    "colonoscopy":        "bowel-prep",
    "combined":           "egd-colon",
    "lactulose":          "bowel-prep",
    "lactulose-combined": "egd-colon",
    "clenpiq":            "bowel-prep",
    "clenpiq-combined":   "egd-colon",
    "suprep":             "bowel-prep",
    "suprep-combined":    "egd-colon",
}


def _inject_analytics(html, family, location_id, lang, band_id=""):
    """Inject the giready analytics + survey embed snippets before </head>.

    Idempotent: if the snippets are already present, returns html unchanged.
    Skips silently for unknown family/location combos (no analytics hookup).
    """
    import json
    # Shared WCAG 2.1 AA base (focus, skip link, contrast, keyboard/ARIA) on
    # every mobile page — applied here because _inject_analytics is the single
    # last-mile transform before every write_text. Independent of analytics.
    html = _inject_shared_mobile_a11y(html)
    site = _ANALYTICS_SITE_BY_FAMILY_LOC.get((family, location_id))
    if not site:
        return html
    ctx = json.dumps({
        "procedure": _PROCEDURE_BY_FAMILY.get(family, family),
        "band": band_id,
        "location": location_id,
        "lang": lang,
        "source": "web",
    }, separators=(",", ":"))
    snippet = _ANALYTICS_SNIPPET.format(site=site, ctx=ctx)
    if 'giready:context' in html and f'data-site="{site}"' in html and 'survey.js' in html:
        return html
    return html.replace("</head>", f"  {snippet}\n</head>", 1)


def build_for_repo(repo_dir, location_id, location, practice_cfg, bands_by_id, band_ids,
                   landing_template_en, landing_template_es,
                   landing_title_en, landing_title_es,
                   band_title_en_fmt, band_title_es_fmt,
                   family="colonoscopy"):
    """Build all pages for a single repo (single location, single product).

    `family` selects the band template family ("colonoscopy" or
    "combined"). The landing page templates are chosen by the caller.
    """
    qr = practice_cfg["qr_targets"]

    repo_dir.mkdir(parents=True, exist_ok=True)
    clean_repo(repo_dir, band_ids, bands_by_id)
    (repo_dir / "es").mkdir(exist_ok=True)

    written = []

    # --- EN landing (root index.html) --------------------------------------
    en_landing_html = render_landing_page(
        landing_template_en, "en", practice_cfg, bands_by_id, band_ids,
        logo_src="logo-pmch.png",
        lang_toggle_href="es/",
        html_title=landing_title_en,
    )
    p = repo_dir / "index.html"
    p.write_text(_inject_analytics(en_landing_html, family, location_id, "en"), encoding="utf-8")
    written.append(p)

    # --- ES landing (es/index.html) ----------------------------------------
    es_landing_html = render_landing_page(
        landing_template_es, "es", practice_cfg, bands_by_id, band_ids,
        logo_src="../logo-pmch.png",
        lang_toggle_href="../",
        html_title=landing_title_es,
    )
    p = repo_dir / "es" / "index.html"
    p.write_text(_inject_analytics(es_landing_html, family, location_id, "es"), encoding="utf-8")
    written.append(p)

    # --- Per-band pages ----------------------------------------------------
    for bid in band_ids:
        band = bands_by_id[bid]
        path = band["mobile_path"]
        label_en = BAND_LABELS[bid]["en"]
        label_es = BAND_LABELS[bid]["es"]

        # EN: <repo>/<path>/index.html
        en_dir = repo_dir / path
        # Wipe first so overwrites don't trigger macOS NSFileVersion
        # side-write of "handout 2.pdf" / "index 2.html" duplicates.
        shutil.rmtree(en_dir, ignore_errors=True)
        en_dir.mkdir(parents=True, exist_ok=True)
        en_pdf_src = find_handout_pdf(band, location_id, "en", family)
        en_pdf_href = ""
        if en_pdf_src:
            shutil.copy(en_pdf_src, en_dir / "handout.pdf")
            en_pdf_href = "handout.pdf"
            written.append(en_dir / "handout.pdf")
        en_html = render_band_page(
            "en", band, location, practice_cfg, qr,
            logo_src="../logo-pmch.png",
            lang_toggle_href=f"../es/{path}/",
            landing_href="../",
            html_title=band_title_en_fmt.format(label=label_en),
            family=family,
            handout_pdf_href=en_pdf_href,
            handout_pdf_download_name=pdf_download_name(family, band, location_id),
        )
        p = en_dir / "index.html"
        p.write_text(_inject_analytics(en_html, family, location_id, "en", bid), encoding="utf-8")
        written.append(p)

        # ES: <repo>/es/<path>/index.html
        es_dir = repo_dir / "es" / path
        shutil.rmtree(es_dir, ignore_errors=True)
        es_dir.mkdir(parents=True, exist_ok=True)
        es_pdf_src = find_handout_pdf(band, location_id, "es", family)
        es_pdf_href = ""
        if es_pdf_src:
            shutil.copy(es_pdf_src, es_dir / "handout.pdf")
            es_pdf_href = "handout.pdf"
            written.append(es_dir / "handout.pdf")
        es_html = render_band_page(
            "es", band, location, practice_cfg, qr,
            logo_src="../../logo-pmch.png",
            lang_toggle_href=f"../../{path}/",
            landing_href="../",
            html_title=band_title_es_fmt.format(label=label_es),
            family=family,
            handout_pdf_href=es_pdf_href,
            handout_pdf_download_name=pdf_download_name(family, band, location_id),
        )
        p = es_dir / "index.html"
        p.write_text(_inject_analytics(es_html, family, location_id, "es", bid), encoding="utf-8")
        written.append(p)

    # Logo
    if LOGO_PATH.exists():
        shutil.copy(LOGO_PATH, repo_dir / "logo-pmch.png")
        written.append(repo_dir / "logo-pmch.png")

    return written


def write_repo_metadata(repo_dir, location, subdomain):
    """Create .gitignore/README.md if missing; always rewrite _headers.

    _headers is fully generator-owned for these Pages repos, so it is rewritten
    on every build — this is what makes a security-header change propagate to
    already-initialized repos instead of silently going stale.
    """
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
    practice_cfg = _load_yaml(PRACTICE_PATH)
    dosing_cfg   = _load_yaml(DOSING_PATH)
    locations    = dosing_cfg["locations"]
    bands_by_id  = {b["id"]: b for b in dosing_cfg["bands"]}

    # Sanity check that every BAND_ORDER entry exists in the data and is
    # marked public. Scheduler-only bands (e.g. lactulose) carry `public: false`
    # in dosing.yaml and must never be shipped to the public mobile sites.
    for bid in BAND_ORDER:
        if bid not in bands_by_id:
            sys.exit(f"band {bid!r} missing from data/dosing.yaml")
        if not bands_by_id[bid].get("public", True):
            sys.exit(f"band {bid!r} is marked `public: false` and must not appear in BAND_ORDER")
    # Reverse check: warn if a public band exists in dosing.yaml but isn't listed.
    for bid, band in bands_by_id.items():
        if band.get("public", True) and bid not in BAND_ORDER:
            print(f"  WARN: public band {bid!r} is in dosing.yaml but missing from BAND_ORDER")

    landing_template_en = TEMPLATES / "colonoscopy-mobile-landing.en.html"
    landing_template_es = TEMPLATES / "colonoscopy-mobile-landing.es.html"

    written_total = 0
    for location_id, repo_dir in SITES.items():
        if location_id not in locations:
            sys.exit(f"location {location_id!r} missing from data/dosing.yaml")
        location = locations[location_id]
        subdomain = location.get("mobile_subdomain", location_id)

        written = build_for_repo(
            repo_dir, location_id, location, practice_cfg, bands_by_id, BAND_ORDER,
            landing_template_en, landing_template_es,
            HTML_TITLE_LANDING_EN, HTML_TITLE_LANDING_ES,
            HTML_TITLE_BAND_EN, HTML_TITLE_BAND_ES,
            family="colonoscopy",
        )
        written += write_repo_metadata(repo_dir, location, subdomain)
        written_total += len(written)
        print(f"  built {repo_dir} ({location_id} -> {subdomain}.giready.com): "
              f"{len(written)} files")

    print(f"\n{written_total} files written across {len(SITES)} site repos.")


if __name__ == "__main__":
    main()
