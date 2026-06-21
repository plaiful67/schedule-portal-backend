"""Deterministic PDF/UA (tagged PDF) output for WeasyPrint 68.1.

Passing ``write_pdf(..., pdf_variant='pdf/ua-1')`` makes WeasyPrint emit a
*tagged* PDF — a real document structure tree (headings, paragraphs, lists,
links, tables with TH/Headers associations, image alt text) plus the document
language and title. That is the single biggest accessibility win for the
handout PDFs: without it a screen reader sees an undifferentiated image of text.

One catch: WeasyPrint 68.1's ``pdf/tags.py`` uses ``id(box)`` / ``id(cell)``
(CPython object addresses) as the struct-element IDs and the TD->TH ``/Headers``
references. Object addresses vary per process, so tagged output is
non-deterministic across runs — which would break this system's
byte-deterministic-render invariant (a dirty site-repo tree is supposed to mean
a real content change, per the auto-render hook). We shadow ``id`` inside that
one module with a per-render counter, so the same document always serializes to
the same bytes while the cell<->header references stay internally consistent.

Usage: replace ``HTML(...).write_pdf(target)`` with
``write_pdf_tagged(HTML(...), target)``. Returns bytes when target is None
(used by the scheduler backend, which streams the PDF).

Kept byte-identical across the giready skills + the schedule-portal-backend.
"""
from __future__ import annotations

import builtins
import threading

import weasyprint.pdf.tags as _wp_tags

PDF_VARIANT = "pdf/ua-1"


class _StableId:
    """Maps each object to a small, render-local, monotonically increasing int,
    keyed by its real ``id()`` so the same object always gets the same value
    within one render. Reset before each render so every PDF is independently
    reproducible regardless of what else rendered in the same process.

    State is THREAD-LOCAL. ``_wp_tags.id`` is one module attribute shared by
    every thread, but the scheduler backend serves renders from a sync FastAPI
    handler — Starlette runs those on a thread pool, so two PDFs can render
    concurrently in one process. A single shared counter/map would let one
    render's ``reset()`` and id assignments interleave with another's, corrupting
    struct-element IDs and the TD->TH ``/Headers`` references (broken tags for a
    screen reader) and destroying byte-determinism. Per-thread state keeps each
    render fully isolated; a single render is synchronous within its own thread,
    so the same document still serializes identically every time."""

    def __init__(self) -> None:
        self._tl = threading.local()

    def _state(self) -> dict:
        s = getattr(self._tl, "state", None)
        if s is None:
            s = self._tl.state = {"m": {}, "n": 0}
        return s

    def reset(self) -> None:
        self._tl.state = {"m": {}, "n": 0}

    def __call__(self, obj) -> int:
        s = self._state()
        real = builtins.id(obj)
        val = s["m"].get(real)
        if val is None:
            s["n"] += 1
            val = s["n"]
            s["m"][real] = val
        return val


_stable_id = _StableId()
# Shadow the bare ``id`` name in tags.py's module namespace (it has no module
# global ``id``, so this wins over the builtin for that module only).
_wp_tags.id = _stable_id


def write_pdf_tagged(html_obj, target=None, **kwargs):
    """Drop-in for ``HTML(...).write_pdf(target)`` that emits a deterministic
    tagged PDF/UA-1. Resets the struct-id counter first. Returns bytes if
    ``target`` is None."""
    _stable_id.reset()
    return html_obj.write_pdf(target, pdf_variant=PDF_VARIANT, **kwargs)
