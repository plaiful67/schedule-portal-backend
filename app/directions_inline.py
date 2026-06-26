"""Build the driving-directions content as an injectable, fully-tagged section
of the personalized handout PDF.

Background: the scheduler used to render the directions as a separate PDF and
append it with pypdf, which discards the structure tree — so the directions
appendix was untagged (a documented PDF/UA residual). Instead we now inline the
directions *content* into the handout HTML and let the single
``write_pdf_tagged`` pass tag the whole document.

The two source documents (handout + directions templates) share class names
(``.step``, ``.location``, ``.band-label`` …), so the directions CSS is
namespaced: every class is prefixed ``da-`` and every selector is scoped under a
``.da-root`` wrapper, which both isolates the directions styles and (via higher
specificity) shields the directions elements from the handout's rules. Heading
levels are demoted one step (h1→h2 …) so the directions nest under the handout's
own h1. Map images are embedded as data URIs so they resolve no matter which
adapter's ``base_url`` is in effect (the EGD adapters don't share the bowel-prep
``templates/`` dir where the maps live). The directions' own ``@page`` footer is
dropped so the handout's unified footer/page-numbering applies throughout.

Public API: ``directions_section(location_id, lang) -> (css, html)`` — inject
``css`` before the handout's ``</head>`` and ``html`` before its ``</body>``.
"""
from __future__ import annotations

import base64
import importlib.util
import re
import sys
from functools import lru_cache
from pathlib import Path

from .adapters._paths import skill_dir

SKILL_ROOT = skill_dir("bowel-prep-generator")
TEMPLATES = SKILL_ROOT / "templates"
DOSING_PATH = SKILL_ROOT / "data" / "dosing.yaml"

_TEMPLATE_STEM = {"scc": "scc-directions-print", "pmch": "pmch-directions-print"}

# Reused from build_directions.py — the public-site legal footer doesn't belong
# on a clinical print artifact.
_LEGAL_FOOTER_CSS_RE = re.compile(
    r"\.medical-disclaimer \{.*?\.footer-copyright \{.*?\}\s*", re.DOTALL
)
_LEGAL_FOOTER_HTML_RE = re.compile(
    r"\s*(?:<!--[^>]*-->\s*)?"
    r'<p class="footer-copyright">.*?</p>\s*'
    r'<nav class="footer-policy-links".*?</nav>\s*'
    r'<aside class="medical-disclaimer".*?</aside>\s*',
    re.DOTALL,
)


@lru_cache(maxsize=1)
def _skill():
    """Load the bowel-prep render module (the directions templates + map/QR
    helpers live there), with the PMCH logo override the scheduler uses."""
    name = "_bowel_prep_render_for_directions_inline"
    spec = importlib.util.spec_from_file_location(name, SKILL_ROOT / "scripts" / "render.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    original_practice = mod._practice

    def _practice_with_pmch_override():
        data = original_practice()
        data["practice"]["logo_filename"] = "logo-pmch.png"
        return data

    mod._practice = _practice_with_pmch_override
    mod._PRACTICE_CACHE = None
    return mod


def _maps_url_for(location_id: str, lang: str) -> str:
    import yaml
    data = yaml.safe_load(DOSING_PATH.read_text(encoding="utf-8"))
    loc = data["locations"][location_id]
    return loc.get(f"maps_url_{lang}") or loc["maps_url_en"]


def _embed_maps(html: str) -> str:
    """Replace src="maps/<file>" with a base64 data URI so the image resolves
    independent of the render base_url."""
    def repl(m):
        rel = m.group(1)
        path = TEMPLATES / rel
        if not path.exists():
            return m.group(0)
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        ext = path.suffix.lstrip(".").lower()
        mime = "jpeg" if ext in ("jpg", "jpeg") else ext
        return f'src="data:image/{mime};base64,{b64}"'
    return re.sub(r'src="(maps/[^"]+)"', repl, html)


def _demote_headings_html(body: str) -> str:
    """h1→h2, h2→h3, … in tag positions only (single pass)."""
    return re.sub(r"<(/?)h([1-5])\b",
                  lambda m: f"<{m.group(1)}h{int(m.group(2)) + 1}", body)


def _demote_headings_css(css: str) -> str:
    return re.sub(r"\bh([1-5])\b", lambda m: "h" + str(int(m.group(1)) + 1), css)


def _prefix_body_classes(body: str) -> str:
    """Prefix every class token in the directions body with ``da-``."""
    def repl(m):
        toks = m.group(1).split()
        return 'class="' + " ".join("da-" + t for t in toks) + '"'
    return re.sub(r'class="([^"]*)"', repl, body)


def _scope_css(css: str) -> str:
    """Namespace the directions stylesheet: drop @import/@page, prefix every
    class selector with ``da-``, demote headings, and scope each rule under
    ``.da-root`` (so it can't touch the handout, and outscopes the handout's
    rules on the directions elements)."""
    css = re.sub(r"@import[^;]*;", "", css)
    # @page has nested margin boxes (@bottom-left { … }) — match one nesting level.
    css = re.sub(r"@page\s*\{(?:[^{}]|\{[^{}]*\})*\}", "", css, flags=re.DOTALL)
    css = re.sub(r"\.([A-Za-z][\w-]*)", r".da-\1", css)   # .step -> .da-step
    css = _demote_headings_css(css)
    # Scope every (now brace-free) rule's selector list under .da-root.
    def scope_rule(m):
        selectors = ", ".join(".da-root " + s.strip() for s in m.group(1).split(","))
        return selectors + " {" + m.group(2) + "}"
    css = re.sub(r"([^{}]+)\{([^{}]*)\}", scope_rule, css)
    return css.strip()


@lru_cache(maxsize=8)
def directions_section(location_id: str, lang: str) -> tuple[str, str]:
    """Return (scoped_css, section_html) for the {location, lang} directions,
    ready to inject into a handout render. Cached per (location, lang)."""
    stem = _TEMPLATE_STEM[location_id]
    suffix = ".html" if lang == "en" else f".{lang}.html"
    template_path = TEMPLATES / f"{stem}{suffix}"
    if not template_path.exists():
        raise FileNotFoundError(f"directions template missing: {template_path}")

    skill = _skill()
    html = template_path.read_text(encoding="utf-8")
    html = _LEGAL_FOOTER_CSS_RE.subn("", html, count=1)[0]
    # Footer is single-sourced as {{PARTIAL_FOOTER_LEGAL}} (Phase A T7); strip the
    # token (the public-site legal footer doesn't belong in the inlined directions
    # appendix). Fall back to the legacy expanded-HTML regex for safety.
    html, _n = re.subn(r"\s*\{\{PARTIAL_FOOTER_LEGAL\}\}\s*", "\n", html, count=1)
    if _n == 0:
        html = _LEGAL_FOOTER_HTML_RE.subn("\n", html, count=1)[0]

    # Swap the Maps QR placeholder for a data URI (same helper as build_directions).
    data_uri = skill._png_to_data_uri(skill._generate_maps_qr(_maps_url_for(location_id, lang)))
    html = re.sub(r'<img id="qr-maps"[^>]*>',
                  f'<img id="qr-maps" src="{data_uri}" alt="Google Maps QR">', html, count=1)
    for placeholder, value in skill.build_practice_placeholders(lang).items():
        html = html.replace(placeholder, value)
    html = _embed_maps(html)

    style = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    body = re.search(r"<body>(.*?)</body>", html, re.DOTALL)
    css = _scope_css(style.group(1)) if style else ""
    inner = body.group(1) if body else ""
    inner = _prefix_body_classes(inner)
    inner = _demote_headings_html(inner)
    section = f'<section class="da-root" style="break-before: page;">{inner}</section>'
    return css, section


def inject_into_handout(html: str, location_id: str, lang: str) -> str:
    """Inline the directions section into a fully-built handout HTML string,
    before the single tagged render. No-op-safe if anchors are missing."""
    css, section = directions_section(location_id, lang)
    if css and "</head>" in html:
        html = html.replace("</head>", f"<style>{css}</style>\n</head>", 1)
    if "</body>" in html:
        html = html.replace("</body>", f"{section}\n</body>", 1)
    return html
