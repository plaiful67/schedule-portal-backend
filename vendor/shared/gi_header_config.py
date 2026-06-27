"""Single source of truth for the Cloudflare ``_headers`` security block.

CANONICAL location, shared by all giready handout skills. The bowel-prep skill
keeps a thin shim (``scripts/header_config.py``) that re-exports from here so its
builders + ``render.py`` import unchanged; the egd-handout and flex-sig skills
import this module directly (via a ``sys.path`` insert of the shared dir). This
ends the former byte-for-byte CSP mirror across three files.

INVARIANT (R4): this module is imported into each skill's INDEPENDENTLY-PINNED
``.venv`` via ``sys.path``. It MUST stay **standard-library only** (no third-party
imports) or it breaks under whichever venv didn't pin the dependency. Today:
base64, hashlib, re, pathlib — all stdlib. Keep it that way.

CSP posture
-----------
``script-src`` does NOT use ``'unsafe-inline'``. The handout pages carry a few
executable inline scripts (calm checklist, feedback FAB, a bundled-for-offline QR
library, and the personalization engine); they are allowed via per-page
``'sha256-...'`` source-expressions computed at build time from the rendered HTML
(see :func:`csp_script_hashes`). External scripts are pinned to ``'self'`` +
analytics. The ``<script type="application/json">`` calendar-data block is not
executable and is intentionally not hashed.

``style-src`` DELIBERATELY keeps ``'unsafe-inline'``. The handouts use one large
inline ``<style>`` block plus pervasive inline ``style=""`` attributes (some
JS-injected); externalizing them is a disproportionate template refactor for a
low-severity style-injection risk. This is an accepted risk, not an oversight.

The CSP value MUST stay on one physical line — the ``_headers`` format is one
header per line and does not allow a wrapped value.
"""

import base64
import hashlib
import re
from pathlib import Path

# Default per-tenant origins. giready's production values; passing these
# explicitly is a no-op (byte-identical _headers). A second tenant passes its
# own analytics/api/asset origins down from the builder (which resolves them
# from tenant.yaml) — this module stays stdlib-only and never imports a
# resolver (R4 invariant), so the CALLER resolves and passes origins in.
DEFAULT_ANALYTICS_ORIGIN = "https://analytics.giready.com"
DEFAULT_API_ORIGIN = "https://api-schedule.giready.com"
DEFAULT_ASSET_ORIGIN = "https://giready.com"

_CSP_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self'{script_hashes} {analytics_origin}; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: {asset_origin} {analytics_origin}; "
    "connect-src 'self' {analytics_origin} {api_origin}; "
    "manifest-src 'self' {asset_origin}; "
    "frame-src https://calendar.google.com; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)

_HEADERS_TEMPLATE = """/*
  Content-Security-Policy: {csp}
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  X-Robots-Tag: noindex, nofollow, noarchive, nosnippet
  Referrer-Policy: no-referrer
  Permissions-Policy: geolocation=(), microphone=(), camera=()
"""

# --- inline-script hashing -------------------------------------------------
# Browsers compute a CSP script hash over the exact UTF-8 bytes between the
# opening <script> tag and </script>. We extract that same substring and hash it.
_SCRIPT_BLOCK = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.DOTALL | re.IGNORECASE)
_ATTR = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')
_EXECUTABLE_TYPES = {"", "text/javascript", "application/javascript", "module"}


def _hashes_in_html(html):
    found = set()
    for match in _SCRIPT_BLOCK.finditer(html):
        attrs = {k.lower(): v for k, v in _ATTR.findall(match.group(1))}
        if "src" in attrs:
            continue  # external script — governed by the source list, not a hash
        if attrs.get("type", "").lower().strip() not in _EXECUTABLE_TYPES:
            continue  # e.g. <script type="application/json"> data — not executed
        inner = match.group(2)
        if not inner.strip():
            continue
        digest = hashlib.sha256(inner.encode("utf-8")).digest()
        found.add("'sha256-" + base64.b64encode(digest).decode() + "'")
    return found


def csp_script_hashes(repo_dir):
    """Sorted union of ``'sha256-...'`` source-expressions for every executable
    inline ``<script>`` across the repo's rendered HTML.

    Stable because renders are byte-deterministic (SOURCE_DATE_EPOCH pinned), and
    self-maintaining: if an inline script changes, its hash is recomputed on the
    next build so the CSP never goes stale.
    """
    repo_dir = Path(repo_dir)
    found = set()
    for html_file in repo_dir.rglob("*.html"):
        found |= _hashes_in_html(html_file.read_text(encoding="utf-8"))
    return sorted(found)


def build_csp(script_hashes=(), analytics_origin=DEFAULT_ANALYTICS_ORIGIN,
              api_origin=DEFAULT_API_ORIGIN, asset_origin=DEFAULT_ASSET_ORIGIN):
    joined = "".join(" " + h for h in script_hashes)
    return _CSP_TEMPLATE.format(script_hashes=joined, analytics_origin=analytics_origin,
                                api_origin=api_origin, asset_origin=asset_origin)


def build_headers_content(script_hashes=(), analytics_origin=DEFAULT_ANALYTICS_ORIGIN,
                          api_origin=DEFAULT_API_ORIGIN, asset_origin=DEFAULT_ASSET_ORIGIN):
    return _HEADERS_TEMPLATE.format(csp=build_csp(
        script_hashes, analytics_origin, api_origin, asset_origin))


def write_headers(repo_dir, analytics_origin=DEFAULT_ANALYTICS_ORIGIN,
                  api_origin=DEFAULT_API_ORIGIN, asset_origin=DEFAULT_ASSET_ORIGIN):
    """Rewrite ``repo_dir/_headers`` with a CSP whose ``script-src`` lists the
    hashes of that repo's own inline scripts. Origins default to giready's
    production values (byte-identical); a second tenant passes its own. Returns
    ``[path]`` if the file changed, else ``[]`` (matches the builders'
    ``written`` accounting)."""
    repo_dir = Path(repo_dir)
    content = build_headers_content(csp_script_hashes(repo_dir),
                                    analytics_origin, api_origin, asset_origin)
    path = repo_dir / "_headers"
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current != content:
        path.write_text(content, encoding="utf-8")
        return [path]
    return []


# Hash-free baseline (``script-src 'self' https://analytics.giready.com`` only,
# no inline scripts allowed). Builders call write_headers(repo_dir) for the
# per-repo hashed version; this constant remains for callers that want the
# baseline or to diff against.
HEADERS_CONTENT = build_headers_content()
