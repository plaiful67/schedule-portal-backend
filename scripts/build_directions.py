#!/usr/bin/env python3
"""Render the driving-directions PDFs the portal stitches into every prep
handout, with the public-site footer (privacy / terms / generic disclaimer)
stripped out.

Mirrors the skill's `scripts/render_directions.py` exactly except for the
strip step. Output goes directly to `app/static/directions/`, replacing the
copies that `make sync-directions` would otherwise pull in from
~/Desktop/peds-gi-system/.

Why portal-local rather than a flag on the skill script: keeps the skill
unchanged (its outputs at ~/Desktop/peds-gi-system/{loc}-directions[-es].pdf
still carry the footer, matching the rest of the public-site surface), and
avoids any chance of the strip leaking into a public artifact.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import yaml

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from app.adapters._paths import skill_dir  # noqa: E402

SKILL_ROOT = skill_dir("bowel-prep-generator")
TEMPLATES = SKILL_ROOT / "templates"
DOSING_PATH = SKILL_ROOT / "data" / "dosing.yaml"
OUT_DIR = BACKEND_DIR / "app" / "static" / "directions"

TEMPLATE_STEM_BY_LOCATION = {
    "scc":  "scc-directions-print",
    "pmch": "pmch-directions-print",
}


_LEGAL_FOOTER_CSS_RE = re.compile(
    r"\.medical-disclaimer \{.*?"
    r"\.footer-copyright \{.*?\}\s*",
    re.DOTALL,
)

_LEGAL_FOOTER_HTML_RE = re.compile(
    r"\s*(?:<!--[^>]*-->\s*)?"
    r'<p class="footer-copyright">.*?</p>\s*'
    r'<nav class="footer-policy-links".*?</nav>\s*'
    r'<aside class="medical-disclaimer".*?</aside>\s*',
    re.DOTALL,
)


def _strip_legal_footer(html: str, *, template_name: str) -> str:
    new_html, css_count = _LEGAL_FOOTER_CSS_RE.subn("", html, count=1)
    if css_count != 1:
        raise RuntimeError(f"{template_name}: legal-footer CSS block not found")
    new_html, html_count = _LEGAL_FOOTER_HTML_RE.subn("\n", new_html, count=1)
    if html_count != 1:
        raise RuntimeError(f"{template_name}: legal-footer HTML block not found")
    return new_html


def _load_skill_render():
    name = "_bowel_prep_render_for_directions"
    spec = importlib.util.spec_from_file_location(name, SKILL_ROOT / "scripts" / "render.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    # Match the adapters (egd.py, bowel_prep.py): scheduler-generated PDFs use the
    # PMCH/Ascension logo instead of the GI Ready logo that public-site handouts use.
    original_practice = mod._practice
    def _practice_with_pmch_override():
        data = original_practice()
        data["practice"]["logo_filename"] = "logo-pmch.png"
        return data
    mod._practice = _practice_with_pmch_override
    mod._PRACTICE_CACHE = None
    return mod


def _maps_url_for(location_id: str, lang: str) -> str:
    with open(DOSING_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    loc = data["locations"][location_id]
    return loc.get(f"maps_url_{lang}") or loc["maps_url_en"]


def _template_path(location_id: str, lang: str) -> Path:
    stem = TEMPLATE_STEM_BY_LOCATION[location_id]
    suffix = ".html" if lang == "en" else f".{lang}.html"
    return TEMPLATES / f"{stem}{suffix}"


def _out_path(location_id: str, lang: str) -> Path:
    suffix = "" if lang == "en" else f"-{lang}"
    return OUT_DIR / f"{location_id}-directions{suffix}.pdf"


def render_one(skill, location_id: str, lang: str) -> Path:
    template_path = _template_path(location_id, lang)
    if not template_path.exists():
        raise SystemExit(f"missing template: {template_path}")

    maps_url = _maps_url_for(location_id, lang)
    qr_bytes = skill._generate_maps_qr(maps_url)
    data_uri = skill._png_to_data_uri(qr_bytes)

    html = template_path.read_text(encoding="utf-8")
    html = _strip_legal_footer(html, template_name=template_path.name)
    html = re.sub(
        r'<img id="qr-maps"[^>]*>',
        f'<img id="qr-maps" src="{data_uri}" alt="Google Maps QR">',
        html, count=1,
    )
    for placeholder, value in skill.build_practice_placeholders(lang).items():
        html = html.replace(placeholder, value)

    skill._ensure_weasyprint_libpath()
    from weasyprint import HTML
    out = _out_path(location_id, lang)
    out.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(template_path.parent)).write_pdf(str(out))
    return out


def main() -> int:
    skill = _load_skill_render()
    for loc in ("scc", "pmch"):
        for lang in ("en", "es"):
            out = render_one(skill, loc, lang)
            size_kb = out.stat().st_size // 1024
            print(f"OK   {out.relative_to(BACKEND_DIR)}  ({size_kb} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
