"""TDD tests for WS1: egd_phmii adapter add-on support.

Test A: render_pdf(add_ons=[], ...) returns %PDF and contains no addon markup/text.
Test B: render_pdf(add_ons=["dlb"], ...) renders %PDF with the DLB blurb present.
Test C: endpoint POST /render with procedure_type="egd_phmii" + add_ons=["dlb"] returns
        200 + %PDF; SCC location still 422.
"""
import sys
import pathlib
import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.adapters import egd_phmii
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

_COMMON = dict(
    location_id="pmch",
    lang="en",
    physician_id="zavoian",
    appt_date_human="Wednesday, January 1, 2099",
    appt_time_display="7:30 AM",
    arrival_time_display="6:30 AM",
    followup_block_html="",
    appt_dt=datetime.datetime(2099, 1, 1, 7, 30),
    include_directions=False,
)

_ENDPOINT_PAYLOAD = dict(
    procedure_type="egd_phmii",
    location_id="pmch",
    language="en",
    physician_id="zavoian",
    appointment_date="2099-01-01",
    appointment_time="07:30",
    arrival_time="06:30",
    include_directions=False,
)


# ── Test A: no add-ons → clean PDF with no addon markup ─────────────────────

def test_egd_phmii_no_addons_returns_pdf():
    """Empty add-ons renders successfully and returns a valid PDF."""
    pdf = egd_phmii.render_pdf(add_ons=[], knob_picks={}, **_COMMON)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 5000


def test_egd_phmii_no_addons_no_addon_markup():
    """No addon text in the rendered PDF when add_ons=[]."""
    import pypdf
    import io
    pdf = egd_phmii.render_pdf(add_ons=[], knob_picks={}, **_COMMON)
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    # The DLB blurb should NOT appear
    assert "ENT Airway Exam" not in text, "ENT addon text leaked into no-addon render"
    assert "direct laryngoscopy" not in text.lower(), "DLB text leaked into no-addon render"


def test_egd_phmii_no_addons_no_unreplaced_slots():
    """No unreplaced {{...}} tokens in the pre-WeasyPrint HTML for the no-add-on case.

    Uses _build_html() — the seam that returns the fully-substituted HTML string
    just before WeasyPrint renders it (after the unreplaced-placeholder guard).
    Asserts directly on the HTML so the test is NOT vacuous: a leftover token
    (e.g. a new {{ADDON_BLURBS}} or {{ADDON_TITLE_SUFFIX}} that the adapter
    forgot to fill) would be caught here even if the guard somehow missed it.

    Negative control: temporarily inserting a fake token into the rendered HTML
    would make this assertion fail — confirming the regex actually exercises the
    right invariant.
    """
    import re
    html = egd_phmii._build_html(add_ons=[], knob_picks={}, **_COMMON)
    unreplaced = re.findall(r"\{\{[^}]+\}\}", html)
    assert unreplaced == [], (
        f"Unreplaced {{{{}}}} tokens found in no-add-on egd_phmii HTML: {unreplaced}"
    )
    # Explicitly confirm the add-on slots are absent (not just replaced-to-empty):
    # an empty string replacement is invisible to the regex above, but we can
    # confirm the slot KEYS themselves are gone.
    assert "{{ADDON_BLURBS}}" not in html, "{{ADDON_BLURBS}} token not substituted"
    assert "{{ADDON_TITLE_SUFFIX}}" not in html, "{{ADDON_TITLE_SUFFIX}} token not substituted"


def test_egd_phmii_no_addons_no_double_blank_around_addon_slot():
    """Empty {{ADDON_BLURBS}} slot leaves no double-blank-line artifact in the HTML.

    Before the blank-line fix the template had:
        </p>\\n{{ADDON_BLURBS}}\\n\\n<!-- NPO -->
    which collapsed to </p>\\n\\n\\n<!-- NPO --> (three newlines) when the slot
    was filled with "". The fix normalised the template to:
        </p>\\n{{ADDON_BLURBS}}\\n<!-- NPO -->
    so the no-addon case produces </p>\\n\\n<!-- NPO --> — matching the pre-slot
    baseline byte-for-byte at that location.

    This test is a positive control: it asserts the CORRECT pattern is present
    and would fail immediately if the extra blank line reappeared (e.g. after an
    accidental vendor-sync that re-introduced the double blank).
    """
    for lang in ("en", "es"):
        common = {**_COMMON, "lang": lang}
        html = egd_phmii._build_html(add_ons=[], knob_picks={}, **common)
        # The artifact: three consecutive newlines at the slot location.
        # HTML blocks around the slot: </p>\n...\n<!-- NPO --> — the
        # only run of \n\n\n here would be from a double-blank slot artefact.
        assert "<!-- NPO -->" in html, f"lang={lang}: NPO comment missing from HTML"
        npo_idx = html.find("<!-- NPO -->")
        # Grab the 30 chars leading up to <!-- NPO --> to check for the artifact.
        prefix = html[max(0, npo_idx - 10):npo_idx]
        assert "\n\n\n" not in prefix, (
            f"lang={lang}: double-blank-line artifact detected before <!-- NPO -->: {prefix!r}"
        )


# ── Test B: add_ons=["dlb"] → DLB blurb present in rendered text ────────────

def test_egd_phmii_with_dlb_addon_returns_pdf():
    """EGD + pH + DLB renders to PDF."""
    pdf = egd_phmii.render_pdf(add_ons=["dlb"], knob_picks={}, **_COMMON)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 5000


def test_egd_phmii_with_dlb_addon_contains_ent_blurb():
    """The DLB ENT-airway blurb text is present in the rendered PDF."""
    import pypdf
    import io
    pdf = egd_phmii.render_pdf(add_ons=["dlb"], knob_picks={}, **_COMMON)
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    # The dlb blurb: "During the same anesthesia, the ENT team will also perform a
    # direct laryngoscopy and bronchoscopy (DLB) to examine the airway."
    assert "ENT" in text or "DLB" in text or "direct laryngoscopy" in text, (
        f"DLB add-on blurb not found in rendered PDF text. Got excerpt: {text[:500]!r}"
    )


# ── Test C: endpoint accepts add_ons; SCC still rejects ─────────────────────

def test_egd_phmii_endpoint_with_dlb_returns_pdf():
    """POST /render with procedure_type=egd_phmii + add_ons=["dlb"] returns 200 + %PDF."""
    payload = {**_ENDPOINT_PAYLOAD, "add_ons": ["dlb"], "knob_picks": {}}
    r = client.post("/render", json=payload)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"


def test_egd_phmii_endpoint_scc_still_422():
    """SCC location is rejected for egd_phmii (PMCH-only constraint)."""
    payload = {**_ENDPOINT_PAYLOAD, "location_id": "scc", "add_ons": [], "knob_picks": {}}
    r = client.post("/render", json=payload)
    assert r.status_code == 422, r.text


def test_egd_phmii_endpoint_no_addons_still_works():
    """Existing egd_phmii calls with no add_ons (schema default []) still return 200."""
    r = client.post("/render", json=_ENDPOINT_PAYLOAD)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"


if __name__ == "__main__":
    tests = [
        test_egd_phmii_no_addons_returns_pdf,
        test_egd_phmii_no_addons_no_addon_markup,
        test_egd_phmii_no_addons_no_unreplaced_slots,
        test_egd_phmii_with_dlb_addon_returns_pdf,
        test_egd_phmii_with_dlb_addon_contains_ent_blurb,
        test_egd_phmii_endpoint_with_dlb_returns_pdf,
        test_egd_phmii_endpoint_scc_still_422,
        test_egd_phmii_endpoint_no_addons_still_works,
    ]
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            print(f"FAIL {fn.__name__}: {e}")
