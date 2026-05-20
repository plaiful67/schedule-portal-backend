#!/usr/bin/env python3
"""
Build the static-site repos that back the mobile QR codes:
  ~/Desktop/peds-gi-system/egd-giready/    → egd.giready.com    (EGD, SCC)
  ~/Desktop/peds-gi-system/egd86-giready/  → egd86.giready.com  (EGD, PMCH)
  ~/Desktop/peds-gi-system/egdph-giready/  → egdph.giready.com  (EGD + pH-MII, PMCH-only)

For each (procedure × location × language), substitute placeholders into the
corresponding mobile template and write to the appropriate path. Also writes
_headers, .gitignore, README, and copies the practice logo into each repo.

Usage:
    python scripts/build_websites.py
"""

import os
import re
import shutil
import sys
from pathlib import Path

# Reuse placeholder builders from the renderer
SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
import render  # noqa: E402

TEMPLATES = SKILL_DIR / "templates"
LOGO_PATH = TEMPLATES / "logo-pmch.png"

# Where `make render` writes the print PDFs we copy into each site repo.
# Mirrors the bowel-prep skill's PDF_REVIEW_DIR convention.
PDF_REVIEW_DIR = Path.home() / "Desktop" / "peds-gi-system" / "egd-pdf-review"

# Per-procedure × per-location target repo. egdph is PMCH-only.
SITE_OUT_BY_PROCEDURE = {
    "egd": {
        "scc":  Path.home() / "Desktop" / "peds-gi-system" / "egd-giready",
        "pmch": Path.home() / "Desktop" / "peds-gi-system" / "egd86-giready",
    },
    "egdph": {
        "pmch": Path.home() / "Desktop" / "peds-gi-system" / "egdph-giready",
    },
}

# Per-procedure × per-location analytics site key. Matches the subdomain.
ANALYTICS_SITE_BY_PROCEDURE = {
    "egd":   {"scc": "egd",   "pmch": "egd86"},
    "egdph": {"pmch": "egdph"},
}

# Patient-facing download filename prefix per procedure.
PDF_DOWNLOAD_PREFIX = {
    "egd":   "EGD_Prep",
    "egdph": "EGD_pH_Impedance",
}

PDF_BUTTON_LABEL = {
    "en": "Download printable PDF",
    "es": "Descargar PDF imprimible",
}


def find_handout_pdf(procedure_id: str, location_id: str, lang: str) -> Path | None:
    """Locate the rendered print PDF for this procedure/location/lang.

    Returns the source Path, or None if `make render` hasn't run yet (no hard
    fail — the build keeps going and just omits the download button).
    """
    loc_upper = location_id.upper()
    lang_dir = "English" if lang == "en" else "Spanish"
    lang_suffix = "" if lang == "en" else "-es"
    pdf_name = f"{procedure_id}-{loc_upper}{lang_suffix}-print.pdf"
    candidate = PDF_REVIEW_DIR / loc_upper / lang_dir / pdf_name
    return candidate if candidate.exists() else None


# Short tokens used in the patient-facing download filename — chosen so the
# saved PDF is self-describing on a phone's downloads list. PMCH gets
# "StVincent" rather than "PMCH" because parents recognize the hospital name
# more easily than the abbreviation.
PDF_LOCATION_SHORT = {"scc": "SCC", "pmch": "StVincent"}


def pdf_download_name(procedure_id: str, location_id: str) -> str:
    """Build the descriptive filename a patient sees on download.

    Example: procedure_id=egdph, location_id=pmch → EGD_pH_Impedance_StVincent.pdf
    """
    prefix = PDF_DOWNLOAD_PREFIX.get(procedure_id, procedure_id.upper())
    loc_short = PDF_LOCATION_SHORT.get(location_id, location_id.upper())
    return f"{prefix}_{loc_short}.pdf"


def build_pdf_button_block(href: str, lang: str, download_name: str = "") -> str:
    """Emit the `<a class="pdf-download">` markup, or empty string if no PDF."""
    if not href:
        return ""
    download_attr = f' download="{download_name}"' if download_name else ""
    return (
        f'<a class="pdf-download" href="{href}"{download_attr} target="_blank" rel="noopener">'
        f'<span aria-hidden="true">\U0001F4C4</span> '
        f'{PDF_BUTTON_LABEL[lang]}</a>'
    )

# Each repo's site title for the README and the `lang_toggle` href is identical
HEADERS_CONTENT = """/*
  X-Robots-Tag: noindex, nofollow
  X-Frame-Options: SAMEORIGIN
"""

GITIGNORE_CONTENT = """.DS_Store
*.swp
.idea/
.vscode/
"""

README_TEMPLATE = """# {repo_name}

Mobile-friendly website for the **{location_name}** {procedure_label} handout.

- Live at: **https://{subdomain}.giready.com/**
- Spanish version: **https://{subdomain}.giready.com/es/**

The HTML is generated from the [`egd-handout-generator` skill](../../.claude/skills/egd-handout-generator/) — edit `data/procedure.yaml` or `practice.yaml` there, then re-run `python scripts/build_websites.py` from the skill folder. Don't hand-edit the HTML in this repo; changes will be overwritten.

## Deploy
Cloudflare Pages or Cloudflare Worker (per `reference_base_site_deploy_pattern.md`), connected to this GitHub repo. Build settings: framework = None, build command = (empty), output directory = `/`.
"""


def render_mobile(procedure_id, template_name, lang, procedure, location, pdf_button_block=""):
    """Substitute placeholders into a mobile template."""
    src = (TEMPLATES / template_name).read_text(encoding="utf-8")
    youtube_url = render.procedure_qr_target(procedure, "youtube_url", lang)
    portal_url = render._qr_target("portal_url")
    gikids_url = render.procedure_qr_target(procedure, "gikids_url")
    location_phone_tel = re.sub(r"\D", "", location.get("phone", ""))
    maps_url = location.get(f"maps_url_{lang}") or location.get("maps_url_en") or ""

    # Variant-specific placeholder set (egdph adds {{MED_STOPS_TBODY}}).
    if procedure_id == "egdph":
        procedure_placeholders = render.build_egdph_placeholders(procedure, lang, location=location, procedure_id=procedure_id)
    else:
        procedure_placeholders = render.build_egd_placeholders(procedure, lang, location=location)

    replacements = {
        **render.build_practice_placeholders(lang),
        **render.build_location_placeholders(location, lang),
        **procedure_placeholders,
        "{{MAPS_URL}}":            maps_url,
        "{{YOUTUBE_URL}}":         youtube_url,
        "{{PORTAL_URL}}":          portal_url,
        "{{GIKIDS_URL}}":          gikids_url,
        "{{LOCATION_PHONE_TEL}}":  location_phone_tel,
        "{{PDF_BUTTON_BLOCK}}":    pdf_button_block,
    }
    out = src
    for token, value in replacements.items():
        out = out.replace(token, value)
    unreplaced = re.findall(r"\{\{[A-Z_]+\}\}", out)
    if unreplaced:
        raise RuntimeError(f"Unreplaced placeholders in {template_name}: {sorted(set(unreplaced))}")
    return out


_ANALYTICS_SNIPPET = (
    '<meta name="giready:context" content=\'{ctx}\'>\n'
    '  <script defer src="https://analytics.giready.com/gi.js" data-site="{site}"></script>\n'
    '  <script defer src="https://analytics.giready.com/survey.js" data-site="{site}" data-survey-delay="90"></script>'
)


def _inject_analytics(html, procedure_id, location_id, lang):
    """Inject the giready analytics + survey embed snippets before </head>. Idempotent."""
    import json
    site = ANALYTICS_SITE_BY_PROCEDURE.get(procedure_id, {}).get(location_id)
    if not site:
        return html
    ctx = json.dumps({
        "procedure": procedure_id,
        "band": "",
        "location": location_id,
        "lang": lang,
        "source": "web",
    }, separators=(",", ":"))
    snippet = _ANALYTICS_SNIPPET.format(site=site, ctx=ctx)
    if 'giready:context' in html and f'data-site="{site}"' in html and 'survey.js' in html:
        return html
    return html.replace("</head>", f"  {snippet}\n</head>", 1)


def build_procedure_sites(procedure_id, data, written):
    """Build all site repos for a single procedure id."""
    if procedure_id not in data["procedures"]:
        sys.exit(f"procedure {procedure_id!r} missing from procedure.yaml")
    procedure = data["procedures"][procedure_id]
    site_out = SITE_OUT_BY_PROCEDURE.get(procedure_id, {})
    if not site_out:
        sys.exit(f"no SITE_OUT entry for procedure {procedure_id!r}")

    for location_id, repo_dir in site_out.items():
        if location_id not in data["locations"]:
            sys.exit(f"location {location_id!r} missing from procedure.yaml")
        location = data["locations"][location_id]
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "es").mkdir(exist_ok=True)

        # Copy the rendered print PDF into the site repo if present, so the
        # mobile page can link to it via <a href="handout.pdf">. If `make
        # render` hasn't run yet, the button block stays empty.
        en_pdf_src = find_handout_pdf(procedure_id, location_id, "en")
        en_pdf_href = ""
        if en_pdf_src is not None:
            shutil.copy(en_pdf_src, repo_dir / "handout.pdf")
            en_pdf_href = "handout.pdf"
            written.append(repo_dir / "handout.pdf")

        es_pdf_src = find_handout_pdf(procedure_id, location_id, "es")
        es_pdf_href = ""
        if es_pdf_src is not None:
            shutil.copy(es_pdf_src, repo_dir / "es" / "handout.pdf")
            es_pdf_href = "handout.pdf"
            written.append(repo_dir / "es" / "handout.pdf")

        dl_name = pdf_download_name(procedure_id, location_id)

        en_template = f"{procedure_id}-mobile.en.html"
        es_template = f"{procedure_id}-mobile.es.html"

        # English page → repo_dir/index.html
        en = render_mobile(procedure_id, en_template, "en", procedure, location,
                           pdf_button_block=build_pdf_button_block(en_pdf_href, "en", dl_name))
        (repo_dir / "index.html").write_text(_inject_analytics(en, procedure_id, location_id, "en"), encoding="utf-8")
        written.append(repo_dir / "index.html")

        # Spanish page → repo_dir/es/index.html
        es = render_mobile(procedure_id, es_template, "es", procedure, location,
                           pdf_button_block=build_pdf_button_block(es_pdf_href, "es", dl_name))
        (repo_dir / "es" / "index.html").write_text(_inject_analytics(es, procedure_id, location_id, "es"), encoding="utf-8")
        written.append(repo_dir / "es" / "index.html")

        # Logo (referenced by both EN and ES pages — the ES page uses ../logo-pmch.png)
        shutil.copy(LOGO_PATH, repo_dir / "logo-pmch.png")
        written.append(repo_dir / "logo-pmch.png")

        # _headers, .gitignore, README — only write if missing (don't clobber edits)
        headers_path = repo_dir / "_headers"
        if not headers_path.exists():
            headers_path.write_text(HEADERS_CONTENT, encoding="utf-8")
            written.append(headers_path)

        gitignore_path = repo_dir / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text(GITIGNORE_CONTENT, encoding="utf-8")
            written.append(gitignore_path)

        # README subdomain comes from procedure override (egdph) or location.
        subdomain = procedure.get("mobile_subdomain") or location.get("mobile_subdomain", "")
        readme_path = repo_dir / "README.md"
        if not readme_path.exists():
            readme_path.write_text(README_TEMPLATE.format(
                repo_name=repo_dir.name,
                location_name=location["name_en"],
                subdomain=subdomain,
                procedure_label=procedure.get("label_en", procedure_id),
            ), encoding="utf-8")
            written.append(readme_path)

        print(f"  built {repo_dir} ({procedure_id}/{location_id})")


def main():
    data = render._procedure_data()
    written = []
    for procedure_id in SITE_OUT_BY_PROCEDURE.keys():
        build_procedure_sites(procedure_id, data, written)
    total_sites = sum(len(v) for v in SITE_OUT_BY_PROCEDURE.values())
    print(f"\n{len(written)} files written across {total_sites} site repos.")


if __name__ == "__main__":
    main()
