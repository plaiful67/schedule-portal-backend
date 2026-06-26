import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

PAYLOAD = dict(procedure_type="composed", add_ons=["dise"], knob_picks={},
               location_id="scc", language="en", physician_id="zavoian",
               appointment_date="2099-01-01", appointment_time="07:30",
               arrival_time="06:30", include_directions=False)


def test_render_composed_returns_pdf():
    r = client.post("/render", json=PAYLOAD)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    assert "Composed" in r.headers.get("content-disposition", "")


def test_render_composed_colonoscopy():
    p = dict(procedure_type="composed", base="colonoscopy", weight_band="31-40",
             prep_type="miralax", add_ons=["dlb"], knob_picks={},
             location_id="scc", language="en", physician_id="zavoian",
             appointment_date="2099-01-01", appointment_time="07:30",
             arrival_time="06:30", include_directions=False)
    r = client.post("/render", json=p)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"


def test_render_composed_slotless_returns_422():
    """A slot-less base/prep combo (suprep has no {{ADDON_BLURBS}} slot) must
    return 422 with a descriptive detail, not propagate as a 500."""
    p = dict(procedure_type="composed", base="colonoscopy", weight_band="over-50",
             prep_type="suprep", add_ons=["dlb"], knob_picks={},
             location_id="scc", language="en", physician_id="zavoian",
             appointment_date="2099-01-01", appointment_time="07:30",
             arrival_time="06:30", include_directions=False)
    r = client.post("/render", json=p)
    assert r.status_code == 422, r.text
    assert "ADDON_BLURBS" in r.text or "slot" in r.text.lower()


if __name__ == "__main__":
    test_render_composed_returns_pdf(); print("PASS test_render_composed_returns_pdf")
    test_render_composed_colonoscopy(); print("PASS test_render_composed_colonoscopy")
    test_render_composed_slotless_returns_422(); print("PASS test_render_composed_slotless_returns_422")
