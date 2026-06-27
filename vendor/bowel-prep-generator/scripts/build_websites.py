#!/usr/bin/env python3
"""
Manifest-driven website builder for the bowel-prep skill.

Replaces the 8 separate build_*_websites.py scripts with a single entry-point
driven by data/sites.yaml via _sites_manifest.py.

Families wired:
  colonoscopy         -> prep.giready.com / prep86.giready.com        (public)
  combined            -> egdcolon.giready.com / egdcolon86.giready.com (public)
  lactulose           -> preplact.giready.com / preplact86.giready.com (hidden)
  lactulose-combined  -> egdcolonlact.giready.com / …86               (hidden)
  clenpiq             -> prepclenpiq.giready.com / …86                 (hidden)
  clenpiq-combined    -> egdcolonclenpiq.giready.com / …86             (hidden)
  suprep              -> prepsuprep.giready.com / …86                  (hidden)
  suprep-combined     -> egdcolonsuprep.giready.com / …86              (hidden)

Usage:
    # Build all wired families:
    python scripts/build_websites.py

    # Build only specific manifest ids:
    python scripts/build_websites.py colonoscopy combined
"""

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

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

# Default delivery target: the per-variant `*-giready` site repos on the Desktop.
# When the giready-sites monorepo drives the build it sets GIREADY_SITES_OUT to
# its `sites/` root; then output goes to `<root>/<subdomain>/` (bare subdomain,
# one dir per subdomain) instead of `<repo_name>/`. Content is identical either
# way — only the destination path changes. See giready-sites/data/sites.yaml.
_SITES_OUT_ROOT = os.environ.get("GIREADY_SITES_OUT", "").strip()


def _repo_out_dir(repo_name: str, subdomain: str) -> Path:
    # A non-giready tenant NEVER writes into giready's Desktop site repos or a
    # real deploy target. It renders to a local, tenant-namespaced PREVIEW root
    # (no DNS, no wrangler — plan guardrail "local/preview artifacts only").
    # Repo dirs are namespaced `{tenant}-{subdomain}` to avoid collisions.
    preview_root = _TENANT.get("preview_root")
    if preview_root:
        return Path(preview_root) / f'{_TENANT["id"]}-{subdomain}'
    if _SITES_OUT_ROOT:
        return Path(_SITES_OUT_ROOT) / subdomain
    return Path.home() / "Desktop" / "peds-gi-system" / repo_name

# Pre-rendered print PDFs to copy alongside each band's mobile page so users
# can print the canonical handout from the website. Populated by
# scripts/render.py; if missing the build still succeeds but the PDF link is
# dropped from that band.
PDF_REVIEW_DIR = Path.home() / "Desktop" / "peds-gi-system" / "bowel-prep-pdf-review"
# Which rendered print theme the website download PDFs use. Calm is the default
# (the live download handouts are the Calm design); override with
# BOWEL_PREP_PDF_THEME=color to fall back to the legacy navy "color" renders.
# Picks the matching review folder ({LOC}[-combined][-calm]-color) in
# find_handout_pdf below.
PDF_THEME = os.environ.get("BOWEL_PREP_PDF_THEME", "calm").strip().lower()

# Pull the single-source-of-truth render helpers from render.py so the mobile
# pages are guaranteed to use the same dose phrasing and the same
# pre-rendered "2 Days Before" HTML block as the print PDF.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import build_strings, build_infant_strings, _load_partials, build_calendar_events_json, lb_phrase, _inject_shared_mobile_a11y, build_clenpiq_strings, build_suprep_strings, build_lactulose_strings, _apply_identity as _render_apply_identity  # noqa: E402

from header_config import (  # noqa: E402  (single source of truth)
    write_headers,
    DEFAULT_ANALYTICS_ORIGIN,
    DEFAULT_API_ORIGIN,
    DEFAULT_ASSET_ORIGIN,
)

from _sites_manifest import load_sites, SiteRow  # noqa: E402

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

GITIGNORE_CONTENT = """.DS_Store
*.swp
.idea/
.vscode/
"""

README_TEMPLATE = """# {repo_name}

Mobile-friendly website for the **{location_name}** colonoscopy bowel-prep handout.

- Live at: **https://{subdomain}.giready.com/**
- Spanish version: **https://{subdomain}.giready.com/es/**

The HTML is generated from the [`bowel-prep-generator` skill](../../.claude/skills/bowel-prep-generator/) — edit `templates/colonoscopy-mobile*.html`, `data/dosing.yaml`, or `practice.yaml`, then re-run `python scripts/build_websites.py` from the skill folder. Don't hand-edit the HTML in this repo; changes will be overwritten.

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
            f'    <div class="info"><h2>{lb}</h2><div class="kg">{label}</div>{tag_html}</div>\n'
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
    # PDF_REVIEW_DIR holds GIREADY's pre-rendered download PDFs (giready
    # branding/phone/address). A non-giready tenant must NOT ship them — its
    # download PDFs would have to be re-rendered for that tenant. In the
    # prototype we omit the download link (the build handles None gracefully).
    if _TENANT.get("id", "giready") != "giready":
        return None
    stem = band["filename_stem"]  # e.g. "31-40kg", "under-15kg-enema"
    loc_upper = location_id.upper()
    lang_dir = "English" if lang == "en" else "Spanish"
    fam_seg = "-combined" if family == "combined" else ""
    theme_seg = "-calm" if PDF_THEME == "calm" else ""
    variant = f"{loc_upper}{fam_seg}{theme_seg}-color"

    base = PDF_REVIEW_DIR / variant / lang_dir
    if not base.exists():
        return None

    es_suffix = "-es" if lang == "es" else ""
    calm_suffix = "-calm" if PDF_THEME == "calm" else ""
    family_suffix = "-combined" if family == "combined" else ""
    # Calm renders insert "-calm" between "-print" and the combined suffix:
    #   color    -> bowel-prep-31-40kg-SCC-print.pdf / ...-print-combined.pdf
    #   calm     -> bowel-prep-31-40kg-SCC-print-calm.pdf / ...-print-calm-combined.pdf
    pdf_name = f"bowel-prep-{stem}-{loc_upper}{es_suffix}-print{calm_suffix}{family_suffix}.pdf"

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


# Per-tenant build context. Set once in main() (mirrors render.py's _TENANT_ID
# idiom) so the deep build call-chain doesn't grow a tenant param on every
# signature. Defaults are giready's PRODUCTION values, so a giready build is
# byte-identical; a second tenant overrides them (resolved from tenant.yaml).
#
# beacon_origin: the analytics platform Worker the page beacons to. Per the
#   entity-neutral spine (plan §"Layer 2"), a NON-giready tenant points at a
#   DISTINCT platform origin (one shared, tenant-tagged Worker), NOT
#   analytics.giready.com. giready stays on its own beacon for byte-identity.
# context_marker: the <meta> marker name used for the analytics context blob +
#   the idempotency guard. Kept as "giready:context" for giready (byte-
#   identical); a second tenant uses a neutral "platform:context".
_TENANT = {
    "id": "giready",
    "apex": "giready.com",           # host literal rewrite target (identity for giready)
    "beacon_origin": "https://analytics.giready.com",
    "context_marker": "giready:context",
    # data-tenant attribute: EMPTY for giready (so its deployed snippet stays
    # byte-identical — no new attribute), ' data-tenant="<id>"' for any other
    # tenant. The platform Worker reads this to slice events by tenant.
    "data_tenant_attr": "",
}

_ANALYTICS_SNIPPET = (
    '<meta name="{marker}" content=\'{ctx}\'>\n'
    '  <script defer src="{beacon}/gi.js" data-site="{site}"{dt}></script>\n'
    '  <script defer src="{beacon}/survey.js" data-site="{site}"{dt} data-survey-delay="90"></script>'
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
    # Tenant apex pass: build_websites' page renderers emit the raw template
    # 'giready.com' host literals (favicon/apple-touch/legal/meds/breadcrumb/
    # logo/calendar) directly — they don't go through render.py's _apply_apex.
    # Rewrite them here, the single last-mile transform before every write_text.
    # Identity for the giready tenant (apex == 'giready.com'); 'giready.com' is
    # only ever a hostname in our templates, never the 'GI Ready' brand string.
    apex = _TENANT.get("apex", "giready.com")
    if apex != "giready.com":
        html = html.replace("giready.com", apex)
    # Tenant identity pass: build_websites' renderers also emit the giready
    # office/location phone + street-address literals (no token). Swap them to
    # the active tenant's via the skill's _apply_identity (reads the render
    # tenant set by _configure_tenant). Identity for the giready tenant.
    html = _render_apply_identity(html)
    # Draft-preview guard: a content unit that hasn't been signed off but is
    # being rendered with --allow-draft-preview gets a noindex meta + a visible
    # "DRAFT — NOT FOR PATIENTS" watermark banner, so unsigned content can never
    # be mistaken for published, clinically-owned content.
    if _TENANT.get("draft_preview"):
        html = html.replace(
            "</head>",
            '<meta name="robots" content="noindex, nofollow, noarchive">\n</head>', 1)
        banner = ('<div style="position:fixed;top:0;left:0;right:0;z-index:99999;'
                  'background:#b00020;color:#fff;text-align:center;font:700 14px/2.2 sans-serif;'
                  'letter-spacing:.04em">DRAFT — NOT SIGNED OFF — NOT FOR PATIENTS</div>')
        html = html.replace("<body>", "<body>\n" + banner, 1)
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
    marker = _TENANT["context_marker"]
    snippet = _ANALYTICS_SNIPPET.format(
        marker=marker, site=site, ctx=ctx,
        beacon=_TENANT["beacon_origin"], dt=_TENANT["data_tenant_attr"])
    if f'name="{marker}"' in html and f'data-site="{site}"' in html and 'survey.js' in html:
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
    # CSP origins are per-tenant: giready uses the defaults (byte-identical
    # _headers); a second tenant passes its beacon + api + asset origins so its
    # CSP allow-list matches its own hosts (resolved into _TENANT in main()).
    written += write_headers(
        repo_dir,
        analytics_origin=_TENANT.get("csp_analytics_origin", DEFAULT_ANALYTICS_ORIGIN),
        api_origin=_TENANT.get("csp_api_origin", DEFAULT_API_ORIGIN),
        asset_origin=_TENANT.get("csp_asset_origin", DEFAULT_ASSET_ORIGIN),
    )

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


# ---------------------------------------------------------------------------
# Strategy registry (picker families)
# ---------------------------------------------------------------------------

@dataclass
class Strategy:
    # Pick the per-band template for a protocol+lang. For picker families this
    # is exactly _band_template_for(protocol, lang, family).
    band_template: Callable           # (protocol, lang) -> Path
    # Source-of-truth dose strings for a band+lang. Standard colon/combined use
    # render.build_strings / build_infant_strings (already wired in render_band_page).
    # Variant families supply their own dose_builder; None => built-in logic.
    dose_builder: object = None
    landing_template: Callable = None  # (lang) -> Path, or None for `single`


# Picker families reuse the existing _band_template_for + landing templates.
def _colon_landing(lang):    return TEMPLATES / f"colonoscopy-mobile-landing.{lang}.html"
def _combined_landing(lang): return TEMPLATES / f"combined-mobile-landing.{lang}.html"


# ---------------------------------------------------------------------------
# Variant-family builders — consolidated from the per-variant
# build_<variant>_websites.py scripts. Output is byte-identical to the
# originals (verified by tests/snapshot_sites.sh).
# ---------------------------------------------------------------------------

# Base family (colonoscopy / combined) for a variant family — used for the
# template prefix, the calendar-events family arg, and the per-band template.
def _base_family(family):
    return "combined" if family.endswith("combined") else "colonoscopy"


# --- SINGLE families (clenpiq / suprep): one band at /<mobile_path>/, no
#     landing. Ported from build_{clenpiq,suprep}[_combined]_websites.py.

# Mobile-tuned hero labels + protocol note for the single-band variants.
# Keyed by the band id (which equals the mobile_path: "clenpiq" / "suprep").
# Ported verbatim from build_clenpiq_websites.py / build_suprep_websites.py.
_SINGLE_BAND_LABELS = {
    "clenpiq": {"en": "31 kg and up", "es": "31 kg en adelante"},
    "suprep":  {"en": "51 kg and up", "es": "51 kg en adelante"},
}
_SINGLE_BAND_NOTE = {
    "clenpiq": {"en": "CLENPIQ option (oral)",
                "es": "Opción CLENPIQ (oral)"},
    "suprep":  {"en": "SUPREP option (oral, Rx)",
                "es": "Opción SUPREP (oral, con receta)"},
}


def _single_band_template(variant, family, lang):
    base = _base_family(family)
    return TEMPLATES / f"{base}-mobile-{variant}-standard.{lang}.html"


def render_single_band_page(lang, band, location, practice_cfg, qr, strat,
                            family, logo_src, lang_toggle_href, html_title):
    """Render the single CLENPIQ/SUPREP band page.

    Ported from build_{clenpiq,suprep}[_combined]_websites.py's render_band_page:
    no landing href, no PDF download, dose strings from strat.dose_builder,
    lb_phrase in "plus" form, variant-local label/note maps.
    """
    template_path = strat.band_template(band["protocol"], lang)
    src = template_path.read_text(encoding="utf-8")

    # Inject partials first ({{PARTIAL_PERSONALIZE}} for the date/time picker).
    for token, body in _load_partials(lang).items():
        src = src.replace(token, body)

    dose_replacements = strat.dose_builder(band, lang, location=location)

    location_phone_tel = re.sub(r"\D", "", location.get("phone", ""))
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""
    youtube_url = qr["youtube_url_es" if lang == "es" else "youtube_url_en"]
    portal_url = qr["portal_url"]
    gikids_url = qr["gikids_url"]

    replacements = {
        **build_practice_placeholders(practice_cfg, lang),
        **build_location_placeholders(location, lang),
        **dose_replacements,
        "{{PZ_EVENTS_JSON}}":   build_calendar_events_json(band, lang, location, family=_base_family(family)),
        "{{HTML_TITLE}}":         html_title,
        "{{BAND_LABEL}}":         _SINGLE_BAND_LABELS[band["id"]][lang],
        "{{LOGO_SRC}}":           logo_src,
        "{{LANG_TOGGLE_HREF}}":   lang_toggle_href,
        "{{BAND_LB}}":            lb_phrase(band, lang, "plus"),
        "{{BAND_NOTE}}":          _SINGLE_BAND_NOTE[band["id"]][lang],
        "{{MAPS_URL}}":           maps_url,
        "{{YOUTUBE_URL}}":        youtube_url,
        "{{PORTAL_URL}}":         portal_url,
        "{{GIKIDS_URL}}":         gikids_url,
        "{{LOCATION_PHONE_TEL}}": location_phone_tel,
    }
    return _do_replace(src, replacements, template_path.name)


def build_single_site(row, repo_dir, loc_id, location, practice_cfg, bands_by_id, strat):
    """Single-band variants (clenpiq/suprep): one band page at /<mobile_path>/, no landing."""
    qr = practice_cfg["qr_targets"]
    repo_dir.mkdir(parents=True, exist_ok=True)
    clean_repo(repo_dir, row.bands, bands_by_id)
    (repo_dir / "es").mkdir(exist_ok=True)
    written = []
    band = bands_by_id[row.bands[0]]
    path = band["mobile_path"]  # "clenpiq" / "suprep"
    family = row.family

    # EN page at /<path>/index.html
    en_dir = repo_dir / path
    en_dir.mkdir(parents=True, exist_ok=True)
    en_html = render_single_band_page(
        "en", band, location, practice_cfg, qr, strat, family,
        logo_src="../logo-pmch.png",
        lang_toggle_href=f"../es/{path}/",
        html_title=row.titles["band_en"],
    )
    p = en_dir / "index.html"
    p.write_text(_inject_analytics(en_html, family, loc_id, "en", band["id"]), encoding="utf-8")
    written.append(p)

    # ES page at /es/<path>/index.html
    es_dir = repo_dir / "es" / path
    es_dir.mkdir(parents=True, exist_ok=True)
    es_html = render_single_band_page(
        "es", band, location, practice_cfg, qr, strat, family,
        logo_src="../../logo-pmch.png",
        lang_toggle_href=f"../../{path}/",
        html_title=row.titles["band_es"],
    )
    p = es_dir / "index.html"
    p.write_text(_inject_analytics(es_html, family, loc_id, "es", band["id"]), encoding="utf-8")
    written.append(p)

    if LOGO_PATH.exists():
        shutil.copy(LOGO_PATH, repo_dir / "logo-pmch.png")
        written.append(repo_dir / "logo-pmch.png")
    return written


# --- PICKER-BANNER family (lactulose): band-picker landing + internal-only
#     banner, 3-band set, no PDF. Ported from
#     build_lactulose[_combined]_websites.py.

# Variant-local label/note maps (keyed by lactulose band id). Both the
# colonoscopy and combined lactulose builders use identical maps.
_LACT_BAND_LABELS = {
    "under-15-lact": {"en": "Under 15 kg",  "es": "Menos de 15 kg"},
    "15-20-lact":    {"en": "15–20 kg",     "es": "15–20 kg"},
    "21-30-lact":    {"en": "21–30 kg",     "es": "21–30 kg"},
}
_LACT_BAND_NOTE = {
    "under-15-lact": {"en": "Lactulose option (oral)", "es": "Opción Lactulosa (oral)"},
    "15-20-lact":    {"en": "Lactulose option (oral)", "es": "Opción Lactulosa (oral)"},
    "21-30-lact":    {"en": "Lactulose option (oral)", "es": "Opción Lactulosa (oral)"},
}

# Internal-only banner injected above the band picker. Wording differs per
# family (lactulose vs lactulose-combined) and per lang — ported verbatim.
_LACT_BANNER = {
    "lactulose": {
        "en": (
            '<div style="background:#fff8e1;border:2px solid #f57c00;border-radius:6px;'
            'padding:14px 18px;margin:16px auto;max-width:720px;font-size:15px;line-height:1.45;">'
            '<strong>Internal — not for browsing.</strong><br>'
            'This is the lactulose backup prep. Use the personalized link given to you by the office. '
            'If you reached this page by accident, the standard MiraLAX prep is at '
            '<a href="https://prep.giready.com/">prep.giready.com</a>.</div>'
        ),
        "es": (
            '<div style="background:#fff8e1;border:2px solid #f57c00;border-radius:6px;'
            'padding:14px 18px;margin:16px auto;max-width:720px;font-size:15px;line-height:1.45;">'
            '<strong>Interno — no para navegación.</strong><br>'
            'Esta es la preparación de respaldo con lactulosa. Use el enlace personalizado que le dio el consultorio. '
            'Si llegó aquí por accidente, la preparación estándar con MiraLAX está en '
            '<a href="https://prep.giready.com/es/">prep.giready.com/es/</a>.</div>'
        ),
    },
    "lactulose-combined": {
        "en": (
            '<div style="background:#fff8e1;border:2px solid #f57c00;border-radius:6px;'
            'padding:14px 18px;margin:16px auto;max-width:720px;font-size:15px;line-height:1.45;">'
            '<strong>Internal — not for browsing.</strong><br>'
            'This is the lactulose backup prep for combined EGD + colonoscopy. Use the personalized link given to you by the office. '
            'If you reached this page by accident, the standard MiraLAX combined prep is at '
            '<a href="https://egdcolon.giready.com/">egdcolon.giready.com</a>.</div>'
        ),
        "es": (
            '<div style="background:#fff8e1;border:2px solid #f57c00;border-radius:6px;'
            'padding:14px 18px;margin:16px auto;max-width:720px;font-size:15px;line-height:1.45;">'
            '<strong>Interno — no para navegación.</strong><br>'
            'Esta es la preparación de respaldo con lactulosa para EGD y colonoscopia combinados. Use el enlace personalizado que le dio el consultorio. '
            'Si llegó aquí por accidente, la preparación estándar con MiraLAX está en '
            '<a href="https://egdcolon.giready.com/es/">egdcolon.giready.com/es/</a>.</div>'
        ),
    },
}


def _band_template_for_lact(family, protocol, lang):
    """Pick the lactulose mobile template by protocol (ported from the
    _band_template_for_lact[_combined] helpers in the lactulose builders)."""
    base = _base_family(family)
    if protocol == "lactulose-infant":
        return TEMPLATES / f"{base}-mobile-lactulose-infant.{lang}.html"
    if protocol == "lactulose-standard":
        return TEMPLATES / f"{base}-mobile-lactulose-standard.{lang}.html"
    raise ValueError(f"Unknown lactulose protocol: {protocol!r}")


def render_lact_band_page(lang, band, location, practice_cfg, qr, strat, family,
                          logo_src, lang_toggle_href, landing_href, html_title):
    """Render a single lactulose per-band page (ported from
    build_lactulose[_combined]_websites.py render_band_page)."""
    template_path = strat.band_template(band["protocol"], lang)
    src = template_path.read_text(encoding="utf-8")

    for token, body in _load_partials(lang).items():
        src = src.replace(token, body)

    dose_replacements = strat.dose_builder(band, lang, location=location)

    location_phone_tel = re.sub(r"\D", "", location.get("phone", ""))
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""
    youtube_url = qr["youtube_url_es" if lang == "es" else "youtube_url_en"]
    portal_url = qr["portal_url"]
    gikids_url = qr["gikids_url"]

    replacements = {
        **build_practice_placeholders(practice_cfg, lang),
        **build_location_placeholders(location, lang),
        **dose_replacements,
        "{{PZ_EVENTS_JSON}}":   build_calendar_events_json(band, lang, location, family=_base_family(family)),
        "{{HTML_TITLE}}":         html_title,
        "{{BAND_LABEL}}":         _LACT_BAND_LABELS[band["id"]][lang],
        "{{LOGO_SRC}}":           logo_src,
        "{{LANG_TOGGLE_HREF}}":   lang_toggle_href,
        "{{LANDING_HREF}}":       landing_href,
        "{{BAND_LB}}":            lb_phrase(band, lang, "bracket"),
        "{{BAND_NOTE}}":          _LACT_BAND_NOTE[band["id"]][lang],
        "{{MAPS_URL}}":           maps_url,
        "{{YOUTUBE_URL}}":        youtube_url,
        "{{PORTAL_URL}}":         portal_url,
        "{{GIKIDS_URL}}":         gikids_url,
        "{{LOCATION_PHONE_TEL}}": location_phone_tel,
        "{{PDF_BUTTON_BLOCK}}":   "",
        "{{WARNING_WEIGHT}}":     band.get(f"warning_weight_{lang}",
                                           band.get("warning_weight_en", "15 kg")),
    }
    return _do_replace(src, replacements, template_path.name)


def render_lact_band_cards(bands_by_id, lang, band_ids):
    """Lactulose band-picker grid — identical markup to render_band_cards but
    sourced from the lactulose-local label/note maps (ported verbatim from the
    lactulose builders' render_band_cards)."""
    arrow_svg = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                 'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
                 '<path d="M5 12h14M13 6l6 6-6 6"/></svg>')
    n = len(band_ids)
    cards = []
    for i, bid in enumerate(band_ids):
        path = bands_by_id[bid]["mobile_path"]
        label = _LACT_BAND_LABELS[bid][lang]
        lb = lb_phrase(bands_by_id[bid], lang)
        note = _LACT_BAND_NOTE[bid][lang]
        tag_html = f'<span class="tag">{note}</span>' if note else ""
        wide = " wide" if (n % 2 == 1 and i == n - 1) else ""
        cards.append(
            f'  <a class="band{wide}" href="{path}/">\n'
            f'    <div class="info"><h2>{lb}</h2><div class="kg">{label}</div>{tag_html}</div>\n'
            f'    <span class="arr">{arrow_svg}</span>\n'
            f'  </a>'
        )
    return "\n".join(cards)


def render_lact_landing_page(template_path, lang, practice_cfg, bands_by_id, band_ids,
                             family, logo_src, lang_toggle_href, html_title):
    """Render the lactulose landing page with the internal-only banner
    prepended above the band picker (ported from the lactulose builders)."""
    src = template_path.read_text(encoding="utf-8")
    replacements = {
        **build_practice_placeholders(practice_cfg, lang),
        "{{HTML_TITLE}}":       html_title,
        "{{LOGO_SRC}}":         logo_src,
        "{{LANG_TOGGLE_HREF}}": lang_toggle_href,
        "{{BAND_CARDS}}":       render_lact_band_cards(bands_by_id, lang, band_ids),
    }
    out = _do_replace(src, replacements, template_path.name)
    banner = _LACT_BANNER[family][lang]
    return out.replace("<body>", f"<body>\n{banner}", 1)


def build_picker_banner_site(row, repo_dir, loc_id, location, practice_cfg, bands_by_id, strat):
    """Picker-banner variants (lactulose): band-picker landing with an
    internal-only banner + per-band pages, no PDF download. Ported from
    build_lactulose[_combined]_websites.py build_for_repo."""
    qr = practice_cfg["qr_targets"]
    repo_dir.mkdir(parents=True, exist_ok=True)
    clean_repo(repo_dir, row.bands, bands_by_id)
    (repo_dir / "es").mkdir(exist_ok=True)
    written = []
    family = row.family

    # EN landing
    en_landing_html = render_lact_landing_page(
        strat.landing_template("en"), "en", practice_cfg, bands_by_id, row.bands,
        family, logo_src="logo-pmch.png", lang_toggle_href="es/",
        html_title=row.titles["landing_en"],
    )
    p = repo_dir / "index.html"
    p.write_text(_inject_analytics(en_landing_html, family, loc_id, "en"), encoding="utf-8")
    written.append(p)

    # ES landing
    es_landing_html = render_lact_landing_page(
        strat.landing_template("es"), "es", practice_cfg, bands_by_id, row.bands,
        family, logo_src="../logo-pmch.png", lang_toggle_href="../",
        html_title=row.titles["landing_es"],
    )
    p = repo_dir / "es" / "index.html"
    p.write_text(_inject_analytics(es_landing_html, family, loc_id, "es"), encoding="utf-8")
    written.append(p)

    # Per-band pages
    for bid in row.bands:
        band = bands_by_id[bid]
        path = band["mobile_path"]

        en_dir = repo_dir / path
        shutil.rmtree(en_dir, ignore_errors=True)
        en_dir.mkdir(parents=True, exist_ok=True)
        en_html = render_lact_band_page(
            "en", band, location, practice_cfg, qr, strat, family,
            logo_src="../logo-pmch.png",
            lang_toggle_href=f"../es/{path}/",
            landing_href="../",
            html_title=row.titles["band_en"].format(label=_LACT_BAND_LABELS[bid]["en"]),
        )
        p = en_dir / "index.html"
        p.write_text(_inject_analytics(en_html, family, loc_id, "en", bid), encoding="utf-8")
        written.append(p)

        es_dir = repo_dir / "es" / path
        shutil.rmtree(es_dir, ignore_errors=True)
        es_dir.mkdir(parents=True, exist_ok=True)
        es_html = render_lact_band_page(
            "es", band, location, practice_cfg, qr, strat, family,
            logo_src="../../logo-pmch.png",
            lang_toggle_href=f"../../{path}/",
            landing_href="../",
            html_title=row.titles["band_es"].format(label=_LACT_BAND_LABELS[bid]["es"]),
        )
        p = es_dir / "index.html"
        p.write_text(_inject_analytics(es_html, family, loc_id, "es", bid), encoding="utf-8")
        written.append(p)

    if LOGO_PATH.exists():
        shutil.copy(LOGO_PATH, repo_dir / "logo-pmch.png")
        written.append(repo_dir / "logo-pmch.png")
    return written


# For the full list of touch-points required when adding a NEW family, see
# the comment block at the top of data/sites.yaml.
FAMILY_STRATEGY = {
    "colonoscopy": Strategy(
        band_template=lambda p, l: _band_template_for(p, l, family="colonoscopy"),
        landing_template=_colon_landing),
    "combined": Strategy(
        band_template=lambda p, l: _band_template_for(p, l, family="combined"),
        landing_template=_combined_landing),
    "clenpiq": Strategy(
        band_template=lambda p, l: _single_band_template("clenpiq", "clenpiq", l),
        dose_builder=build_clenpiq_strings),
    "clenpiq-combined": Strategy(
        band_template=lambda p, l: _single_band_template("clenpiq", "clenpiq-combined", l),
        dose_builder=build_clenpiq_strings),
    "suprep": Strategy(
        band_template=lambda p, l: _single_band_template("suprep", "suprep", l),
        dose_builder=build_suprep_strings),
    "suprep-combined": Strategy(
        band_template=lambda p, l: _single_band_template("suprep", "suprep-combined", l),
        dose_builder=build_suprep_strings),
    "lactulose": Strategy(
        band_template=lambda p, l: _band_template_for_lact("lactulose", p, l),
        dose_builder=build_lactulose_strings,
        landing_template=_colon_landing),
    "lactulose-combined": Strategy(
        band_template=lambda p, l: _band_template_for_lact("lactulose-combined", p, l),
        dose_builder=build_lactulose_strings,
        landing_template=_combined_landing),
}


# ---------------------------------------------------------------------------
# Manifest-driven build_site + main
# ---------------------------------------------------------------------------

# Families whose bands must be PUBLIC (public: true, which is the default when
# the flag is absent). All other families are scheduler-only (public: false).
_PUBLIC_FAMILIES = {"colonoscopy", "combined"}


def _assert_band_publicness(row, bands_by_id):
    """Abort if any band's `public` flag contradicts its family's expectation.

    colonoscopy + combined expect public: true (or absent — default true).
    All variant families (lactulose*, clenpiq*, suprep*) expect public: false.
    This mirrors the per-family assertions the original per-variant builders
    each enforced and ensures the manifest can't silently mis-route a band.
    """
    expected_public = row.family in _PUBLIC_FAMILIES
    for bid in row.bands:
        band = bands_by_id.get(bid)
        if band is None:
            sys.exit(
                f"ERROR: band '{bid}' in manifest row '{row.id}' not found in dosing.yaml"
            )
        actual_public = band.get("public", True)
        if bool(actual_public) != expected_public:
            direction = "public: true" if expected_public else "public: false"
            found = "public: true (default)" if actual_public else "public: false"
            sys.exit(
                f"ERROR: band '{bid}' in manifest row '{row.id}' (family '{row.family}') "
                f"must be {direction}, but dosing.yaml has {found}. "
                f"Correct the band's `public:` flag or the manifest family assignment."
            )

def build_site(row: SiteRow, locations, bands_by_id, practice_cfg) -> int:
    strat = FAMILY_STRATEGY[row.family]
    written = 0
    # A non-giready tenant builds ONLY the locations it explicitly declares in
    # its tenant.yaml `locations` overlay — it must not inherit (and ship a site
    # for) giready's other location (e.g. demo has no PMCH, so no prep86 site
    # carrying St. Vincent's address/phone). giready builds every location.
    tenant_locs = _TENANT.get("declared_locations")
    for loc_id, repo_name in row.repos.items():
        if tenant_locs is not None and loc_id not in tenant_locs:
            continue
        repo_dir = _repo_out_dir(repo_name, row.subdomains[loc_id])
        location = locations[loc_id]
        if row.landing == "picker":
            files = build_for_repo(
                repo_dir, loc_id, location, practice_cfg, bands_by_id, row.bands,
                strat.landing_template("en"), strat.landing_template("es"),
                row.titles["landing_en"], row.titles["landing_es"],
                row.titles["band_en"], row.titles["band_es"],
                family=row.family,
            )
        elif row.landing == "picker-banner":  # lactulose: band-picker + internal-only banner
            files = build_picker_banner_site(row, repo_dir, loc_id, location,
                                             practice_cfg, bands_by_id, strat)
        else:  # "single": one-band page, no landing (clenpiq/suprep)
            files = build_single_site(row, repo_dir, loc_id, location,
                                      practice_cfg, bands_by_id, strat)
        files += write_repo_metadata(repo_dir, location, row.subdomains[loc_id])
        written += len(files)
        print(f"  built {repo_dir} ({loc_id} -> {row.subdomains[loc_id]}.giready.com): {len(files)} files")
    return written


def _content_status_module():
    """Import the shared content_status module (publish/approval gate) via the
    shared dir resolver. Returns None if absent (then the build is ungated, as
    before content-status existed — fail-open is acceptable for a tooling import
    error but the gate is present in every supported layout)."""
    import render
    try:
        sd = str(render._shared_dir())
        if sd not in sys.path:
            sys.path.insert(0, sd)
        import content_status
        return content_status
    except Exception:
        return None


def _configure_tenant(tenant_id, preview_out):
    """Resolve the tenant overlay and populate the module-level _TENANT build
    context + render's tenant. For giready this leaves every value at its
    production default (byte-identical build); a second tenant gets its own
    apex-derived beacon/api/asset/preview origins."""
    import render
    render._set_tenant(tenant_id)
    practice_cfg = render._practice()        # triggers the overlay merge
    tcfg = practice_cfg.get("tenant", {}) or {}
    _TENANT["id"] = tenant_id

    if tenant_id == "giready":
        # giready: keep production beacon/marker/CSP origins verbatim.
        return practice_cfg

    apex = tcfg.get("apex", "giready.com")
    _TENANT["apex"] = apex
    analytics = tcfg.get("analytics", {}) or {}
    # Entity-neutral spine: a non-giready tenant beacons to the SHARED platform
    # Worker (distinct origin, tenant-tagged), NOT analytics.<its apex>. For the
    # prototype this is a documented placeholder platform origin; pointing it at
    # a real platform domain is a later config flip.
    platform_origin = analytics.get(
        "platform_origin", "https://analytics.giready-platform.example")
    _TENANT["beacon_origin"] = platform_origin
    _TENANT["context_marker"] = "platform:context"
    _TENANT["data_tenant_attr"] = f' data-tenant="{tenant_id}"'
    # CSP allow-list for the tenant's own pages: platform beacon + tenant api +
    # tenant asset origin (its apex).
    _TENANT["csp_analytics_origin"] = platform_origin
    _TENANT["csp_api_origin"] = analytics.get("api_origin", f"https://api-schedule.{apex}")
    _TENANT["csp_asset_origin"] = f"https://{apex}"
    _TENANT["preview_root"] = preview_out
    # Locations the tenant EXPLICITLY declares in its own overlay (not the
    # inherited dosing.yaml ones) — build_site uses this to skip inherited
    # giready locations. Read the raw overlay so inherited keys don't appear.
    try:
        sd = str(render._shared_dir())
        if sd not in sys.path:
            sys.path.insert(0, sd)
        import tenant_resolver
        overlay = tenant_resolver.resolve(tenant_id) or {}
        _TENANT["declared_locations"] = set((overlay.get("locations") or {}).keys())
    except Exception:
        _TENANT["declared_locations"] = None
    return practice_cfg


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Build the per-tenant mobile handout sites.")
    ap.add_argument("--tenant", default="giready",
                    help="Tenant id (default 'giready' — byte-identical production build). "
                         "A non-giready tenant renders to a local preview root "
                         "(--preview-out), namespaced, with a distinct platform beacon.")
    ap.add_argument("--preview-out", default=None,
                    help="Local preview root for a non-giready tenant build (required "
                         "for --tenant != giready). No DNS/wrangler — local artifacts only.")
    ap.add_argument("--allow-draft-preview", action="store_true",
                    help="Build DRAFT (unsigned/sha-mismatched) content units to a "
                         "WATERMARKED, noindex preview instead of refusing them. Draft "
                         "content is NEVER published to a tenant's real apex.")
    ap.add_argument("only", nargs="*", help="Optional: build only these manifest ids.")
    args = ap.parse_args()

    if args.tenant != "giready" and not args.preview_out:
        sys.exit("ERROR: --tenant <non-giready> requires --preview-out <dir> "
                 "(prototype renders to local preview only — no real deploy target).")

    practice_cfg = _configure_tenant(args.tenant, args.preview_out)
    dosing_cfg = _load_yaml(DOSING_PATH)
    locations = dosing_cfg["locations"]
    # Tenant locations overlay (mirror render.main): a tenant supplies its own
    # facility roster on top of dosing.yaml. Identity for giready.
    _tloc = (practice_cfg.get("tenant", {}) or {}).get("locations")
    if _tloc:
        import render as _r
        for _lid, _lblock in _tloc.items():
            base = locations.get(_lid, {})
            locations[_lid] = _r._deep_merge_under(base, _lblock) if isinstance(base, dict) else _lblock
    bands_by_id = {b["id"]: b for b in dosing_cfg["bands"]}
    only = set(args.only)  # optional: build only these manifest ids

    # Content-ownership gate (plan Layer 4): a content unit (= site family)
    # publishes ONLY if the tenant has signed off on it (approved + the approval
    # still matches the current content sha). A draft / sha-mismatched unit is
    # refused — skipped from the real build (with --allow-draft-preview it builds
    # to a watermarked, noindex preview instead, never the tenant's real apex).
    _cs = _content_status_module()
    total = 0
    skipped = []
    for row in load_sites():
        if only and row.id not in only:
            continue
        if row.family not in FAMILY_STRATEGY:
            continue  # unknown family — skip
        _assert_band_publicness(row, bands_by_id)
        if _cs is not None and not _cs.is_publishable(args.tenant, row.family):
            st = _cs.unit_status(args.tenant, row.family)
            reason = "auto-reverted (content changed since sign-off)" \
                if st.get("auto_reverted") else st.get("state", "draft")
            if not args.allow_draft_preview:
                skipped.append((row.id, row.family, reason))
                print(f"  REFUSED {row.id} (family={row.family}): not approved — {reason}")
                continue
            _TENANT["draft_preview"] = True  # build watermarked, noindex (build_for_repo honors it)
            print(f"  DRAFT-PREVIEW {row.id} (family={row.family}): {reason} → watermarked/noindex")
        else:
            _TENANT["draft_preview"] = False
        total += build_site(row, locations, bands_by_id, practice_cfg)
    if skipped:
        print(f"\n{len(skipped)} content unit(s) REFUSED (not signed off): "
              + ", ".join(f"{i}:{r}" for i, f, r in skipped))
    print(f"\n{total} files written.")


if __name__ == "__main__":
    main()
