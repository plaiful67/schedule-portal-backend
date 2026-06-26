import sys, pathlib, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app.adapters import composed


def _render(add_ons, knob_picks=None, lang="en"):
    return composed.render_pdf(
        add_ons=add_ons, knob_picks=knob_picks or {},
        location_id="scc", lang=lang, physician_id="zavoian",
        appt_date_human="Wednesday, January 1, 2099", appt_time_display="7:30 AM",
        arrival_time_display="6:30 AM", followup_block_html="",
        appt_dt=datetime.datetime(2099, 1, 1, 7, 30), include_directions=False)


def test_composed_renders_pdf_bytes():
    pdf = _render(["dise"])
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 5000


def test_composed_with_two_addons_and_knob():
    pdf = _render(["dlb", "ph_mii"], {"ppi_handling": "hold"})
    assert pdf[:4] == b"%PDF"


if __name__ == "__main__":
    for fn in [test_composed_renders_pdf_bytes, test_composed_with_two_addons_and_knob]:
        fn(); print(f"PASS {fn.__name__}")
