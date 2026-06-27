import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import compose


def test_no_addons_is_empty():
    assert compose.compose_blurbs([], {}, "en") == ""


def test_single_addon_blurb():
    out = compose.compose_blurbs(["dise"], {}, "en")
    assert out.count("<p class=\"addon-blurb\">") == 1
    assert "Sleep team will examine" in out
    assert "addon-knob" not in out  # dise owns no knob


def test_generic_addon_uses_specialty_blurb():
    out = compose.compose_blurbs(["ent_generic"], {}, "en")
    assert "ENT (ear, nose & throat) team will also be present" in out


def test_ph_mii_appends_ppi_knob_fragment():
    out = compose.compose_blurbs(["ph_mii"], {"ppi_handling": "continue"}, "en")
    assert "<p class=\"addon-blurb\">" in out          # the pH catheter blurb
    assert "<p class=\"addon-knob\">" in out           # the PPI line
    assert "may continue" in out


def test_blurb_order_is_registry_order():
    out = compose.compose_blurbs(["dlb", "ph_mii"], {}, "en")
    # Registry order: ph_mii precedes dlb, so its blurb comes first regardless
    # of the order the ids were passed in.
    assert out.index("pH-impedance catheter") < out.index("direct laryngoscopy")


if __name__ == "__main__":
    fns = [test_no_addons_is_empty, test_single_addon_blurb, test_generic_addon_uses_specialty_blurb,
           test_ph_mii_appends_ppi_knob_fragment, test_blurb_order_is_registry_order]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
