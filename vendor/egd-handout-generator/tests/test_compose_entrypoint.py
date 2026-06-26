import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import compose


def test_plain_egd_composition_is_inert():
    c = compose.compose("egd", [], {}, "en")
    assert c.title == "Upper Endoscopy (EGD)"
    assert c.blurbs_html == ""
    assert c.knob_values == {}


def test_full_combo_composition():
    c = compose.compose("egd", ["dise", "ph_mii"], {"ppi_handling": "hold"}, "en")
    assert c.title == "Upper Endoscopy (EGD) + pH Impedance Study + Drug-Induced Sleep Endoscopy"
    assert "Sleep team will examine" in c.blurbs_html
    assert "pH-impedance catheter" in c.blurbs_html
    assert "Hold your child's acid-reducing medicine" in c.blurbs_html
    assert c.knob_values == {"ppi_handling": "hold"}


if __name__ == "__main__":
    fns = [test_plain_egd_composition_is_inert, test_full_combo_composition]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
