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
    """No unreplaced {{}} placeholders in the no-add-on render (including the new slots)."""
    import re
    pdf = egd_phmii.render_pdf(add_ons=[], knob_picks={}, **_COMMON)
    # We test the adapter internals by checking no RuntimeError was raised (render succeeded)
    # and the PDF is valid — the unreplaced-placeholder guard in egd_phmii.py:196 would
    # have raised RuntimeError before this if any token was left unfilled.
    assert pdf[:4] == b"%PDF"


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
