import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import importlib.util

# Load the vendored resolver directly (same mechanism the composed adapter uses).
# NOTE: module must be registered in sys.modules BEFORE exec_module — required
# for Python 3.14 dataclass compatibility (dataclass field resolution looks up
# the class __module__ in sys.modules at class-definition time).
COMPOSE_PATH = (pathlib.Path(__file__).resolve().parent.parent
                / "vendor" / "egd-handout-generator" / "scripts" / "compose.py")
_MOD_NAME = "_test_compose_registry_compose"
spec = importlib.util.spec_from_file_location(_MOD_NAME, COMPOSE_PATH)
compose_mod = importlib.util.module_from_spec(spec)
sys.modules[_MOD_NAME] = compose_mod
spec.loader.exec_module(compose_mod)


def test_dise_is_pulm_and_sleep_specialty_removed():
    reg = compose_mod.load_registry()
    assert reg["add_ons"]["dise"]["specialty"] == "pulm"
    assert "sleep" not in reg["specialties"]
    assert "sleep_generic" not in reg["add_ons"]


def test_pulm_generic_blurb_is_no_wash_airway_line():
    blurb = compose_mod.compose_blurbs(["pulm_generic"], {}, "en")
    low = blurb.lower()
    assert "airway" in low
    assert "wash" not in low and "lavage" not in low and "fluid" not in low


def test_bal_and_dise_blurbs_never_mention_the_wash():
    for addon in ("bal", "dise"):
        blurb = compose_mod.compose_blurbs([addon], {}, "en")
        low = blurb.lower()
        assert "wash" not in low and "lavage" not in low and "fluid" not in low, addon


if __name__ == "__main__":
    test_dise_is_pulm_and_sleep_specialty_removed()
    print("PASS test_dise_is_pulm_and_sleep_specialty_removed")
    test_pulm_generic_blurb_is_no_wash_airway_line()
    print("PASS test_pulm_generic_blurb_is_no_wash_airway_line")
    test_bal_and_dise_blurbs_never_mention_the_wash()
    print("PASS test_bal_and_dise_blurbs_never_mention_the_wash")
