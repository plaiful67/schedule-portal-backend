import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import compose


def test_ppi_knob_default_is_hold():
    out = compose.resolve_knobs(["ph_mii"], {}, "en")
    assert len(out) == 1
    assert out[0]["name"] == "ppi_handling"
    assert out[0]["value"] == "hold"
    assert "Hold your child's acid-reducing medicine" in out[0]["fragment"]


def test_ppi_knob_continue_pick():
    out = compose.resolve_knobs(["ph_mii"], {"ppi_handling": "continue"}, "en")
    assert out[0]["value"] == "continue"
    assert "may continue" in out[0]["fragment"]


def test_knob_skipped_when_owner_not_selected():
    # No ph_mii selected → ppi_handling not in scope.
    assert compose.resolve_knobs(["dise"], {"ppi_handling": "hold"}, "en") == []


def test_invalid_pick_raises():
    try:
        compose.resolve_knobs(["ph_mii"], {"ppi_handling": "maybe"}, "en")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


if __name__ == "__main__":
    fns = [test_ppi_knob_default_is_hold, test_ppi_knob_continue_pick,
           test_knob_skipped_when_owner_not_selected, test_invalid_pick_raises]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
