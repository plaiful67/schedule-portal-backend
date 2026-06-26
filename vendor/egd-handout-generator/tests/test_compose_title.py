import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import compose


def test_plain_egd_title():
    assert compose.compose_title("egd", [], "en") == "Upper Endoscopy (EGD)"


def test_egd_plus_one_addon():
    assert compose.compose_title("egd", ["dise"], "en") == \
        "Upper Endoscopy (EGD) + Drug-Induced Sleep Endoscopy"


def test_title_is_registry_order_not_input_order():
    # ph_mii appears before dlb in the registry; input order is reversed.
    out = compose.compose_title("egd", ["dlb", "ph_mii"], "en")
    assert out == "Upper Endoscopy (EGD) + pH Impedance Study + ENT Airway Exam (DLB)"


def test_spanish_title():
    assert compose.compose_title("egd", ["bal"], "es") == \
        "Endoscopia Superior (EGD) + Lavado Broncoalveolar"


def test_unknown_addon_raises():
    try:
        compose.compose_title("egd", ["nope"], "en")
    except KeyError:
        return
    raise AssertionError("expected KeyError")


if __name__ == "__main__":
    fns = [test_plain_egd_title, test_egd_plus_one_addon, test_title_is_registry_order_not_input_order,
           test_spanish_title, test_unknown_addon_raises]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
