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


if __name__ == "__main__":
    test_render_composed_returns_pdf(); print("PASS test_render_composed_returns_pdf")
