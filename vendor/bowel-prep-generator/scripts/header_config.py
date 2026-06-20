"""Compatibility shim — the canonical security-header source moved to
``~/peds-gi-prep-system/shared/gi_header_config.py`` (single source for all
skills; ends the former 3-way CSP mirror).

This shim keeps bowel-prep's builders + ``render.py`` importing ``header_config``
unchanged. It resolves the shared dir vendored-first (``vendor/shared`` inside the
Cloud Run image) then the local meta-repo checkout, and re-exports everything.
"""
import sys
from pathlib import Path

for _cand in (Path(__file__).resolve().parent.parent.parent / "shared",
              Path.home() / "peds-gi-prep-system" / "shared"):
    if (_cand / "gi_header_config.py").exists():
        sys.path.insert(0, str(_cand))
        break

from gi_header_config import (  # noqa: E402,F401
    _CSP_TEMPLATE,
    _HEADERS_TEMPLATE,
    build_csp,
    build_headers_content,
    csp_script_hashes,
    write_headers,
    HEADERS_CONTENT,
)
