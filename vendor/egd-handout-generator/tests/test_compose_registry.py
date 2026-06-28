import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import compose


def test_registry_loads_expected_top_level_keys():
    reg = compose.load_registry()
    assert set(reg) >= {"bases", "add_ons", "specialties", "knobs"}


def test_registry_has_phase1_addons():
    reg = compose.load_registry()
    assert set(reg["add_ons"]) >= {
        "ph_mii", "rsbx", "bal", "dise", "dlb", "ent_ta", "ent_tubes",
        "pulm_generic", "ent_generic",
    }
    assert reg["add_ons"]["dise"]["specialty"] == "pulm"


def test_ppi_knob_owned_by_ph_mii():
    reg = compose.load_registry()
    assert reg["knobs"]["ppi_handling"]["owned_by_addon"] == "ph_mii"
    assert reg["knobs"]["ppi_handling"]["default"] == "hold"
    assert "ph_mii" in reg["add_ons"] and reg["add_ons"]["ph_mii"]["knobs"] == ["ppi_handling"]


def test_default_path_is_skill_data():
    assert compose.REGISTRY_PATH.name == "procedures.yaml"
    assert compose.REGISTRY_PATH.parent.name == "data"


if __name__ == "__main__":
    fns = [test_registry_loads_expected_top_level_keys, test_registry_has_phase1_addons,
           test_ppi_knob_owned_by_ph_mii, test_default_path_is_skill_data]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
