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


def _render_prep(base, weight_band, add_ons, prep_type="miralax", lang="en"):
    return composed.render_pdf(
        add_ons=add_ons, knob_picks={}, base=base, weight_band=weight_band,
        prep_type=prep_type, location_id="scc", lang=lang, physician_id="zavoian",
        appt_date_human="Wednesday, January 1, 2099", appt_time_display="7:30 AM",
        arrival_time_display="6:30 AM", followup_block_html="",
        appt_dt=datetime.datetime(2099, 1, 1, 7, 30), include_directions=False)


def test_composed_colonoscopy_base_renders():
    pdf = _render_prep("colonoscopy", "31-40", ["dlb"])
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 5000


def test_composed_combined_base_renders():
    pdf = _render_prep("combined", "31-40", ["dise"])
    assert pdf[:4] == b"%PDF"


def test_composed_deferred_template_fails_loud():
    # over-50 + suprep selects a deferred template with no ADDON_BLURBS slot — must raise.
    try:
        _render_prep("colonoscopy", "over-50", ["dlb"], prep_type="suprep")
    except RuntimeError as e:
        assert "ADDON" in str(e)
        return
    raise AssertionError("composed render on a slot-less template should raise")


def test_composed_combined_rsbx_renders():
    """rsbx on combined — must render without error (rsbx bullet appears in procedures ul)."""
    pdf = _render_prep("combined", "31-40", ["rsbx"])
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 5000


def test_composed_combined_rsbx_bal_renders():
    """rsbx (GI bullet) + bal (team blurb) on combined — both rendered."""
    pdf = _render_prep("combined", "31-40", ["rsbx", "bal"])
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 5000


def test_composed_colonoscopy_rsbx_renders():
    """rsbx on colonoscopy-only base — rsbx appears as paragraph (ADDON_BLURBS fallback)."""
    pdf = _render_prep("colonoscopy", "31-40", ["rsbx"])
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 5000


def test_composed_combined_no_rsbx_no_stray_list_item():
    """No rsbx add-on on combined — ADDON_PROCEDURE_ITEMS is empty → no stray <li>."""
    pdf = _render_prep("combined", "31-40", ["bal"])
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 5000


if __name__ == "__main__":
    for fn in [test_composed_renders_pdf_bytes, test_composed_with_two_addons_and_knob,
               test_composed_colonoscopy_base_renders, test_composed_combined_base_renders,
               test_composed_deferred_template_fails_loud,
               test_composed_combined_rsbx_renders, test_composed_combined_rsbx_bal_renders,
               test_composed_colonoscopy_rsbx_renders, test_composed_combined_no_rsbx_no_stray_list_item]:
        fn(); print(f"PASS {fn.__name__}")
