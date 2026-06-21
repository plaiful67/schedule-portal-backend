"""Shared Calm <style>-swap for the scheduler's forked personalized templates.

Each personalized template carries a navy "color" <style>. Calm replaces that
whole block with the shared calm-print.css + calm-personalized.css (the
personalization classes calm-print.css lacks), and — for EGD / EGD+pH-MII —
calm-egd.css (the NPO / med-stops / trouble-table classes).

Read from vendor/shared (baked into the Cloud Run image by `make vendor-sync`),
with a dev fallback to ~/peds-gi-prep-system/shared. The Google-Fonts @import is
stripped: Newsreader + Hanken Grotesk are baked into the image, and a render-time
network fetch would be non-deterministic on Cloud Run.

If the Calm CSS is missing the swap is a no-op (keeps the old design) rather than
a 500 — the assert_calm smoke is what proves Calm actually applied in prod.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_SHARED_DIRS = (
    _BACKEND_DIR / "vendor" / "shared",
    Path.home() / "peds-gi-prep-system" / "shared",
)
_STYLE_RE = re.compile(r"<style>.*?</style>", re.S)
_IMPORT_RE = re.compile(r"@import\s+url\([^)]*\)\s*;", re.S)


def _read(name: str) -> str:
    for d in _SHARED_DIRS:
        p = d / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


@functools.lru_cache(maxsize=4)
def _calm_css(include_egd: bool) -> str:
    base = _read("calm-print.css")
    if not base:
        return ""
    css = _IMPORT_RE.sub("", base) + "\n" + _read("calm-personalized.css")
    if include_egd:
        css += "\n" + _read("calm-egd.css")
    return css


def swap_calm(html: str, *, include_egd: bool = False) -> str:
    """Replace the template's first <style> with the Calm stylesheet(s). Run on
    the RAW template before token substitution so calm-print.css's
    {{PRACTICE_FOOTER}}/{{BAND_LABEL}} tokens resolve in the normal pass."""
    css = _calm_css(include_egd)
    if not css:
        return html
    return _STYLE_RE.sub(lambda _: f"<style>\n{css}\n</style>", html, count=1)
