"""Task 1: Registry flexsig base + FlexSigRequest.prep_type schema tests.

Task 2 (adapter/dispatch) adds the render endpoint tests — test_render_flexsig_*
will be added there once the 501 route exists.

Runnable two ways:
  python tests/test_flexsig.py          (via run_all.py, no pytest required)
  python -m pytest tests/test_flexsig.py -v  (when pytest is installed in venv)
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pydantic

BASE = dict(location_id="scc", language="en", physician_id="zavoian",
            appointment_date="2099-01-01", appointment_time="07:30",
            arrival_time="06:30", include_directions=False)


def test_flexsig_schema_accepts_prep_type():
    from app.schemas import FlexSigRequest
    r = FlexSigRequest(procedure_type="flex_sig", weight_band="31-40", prep_type="miralax", **BASE)
    assert r.prep_type == "miralax"


def test_flexsig_lactulose_band_gating():
    from app.schemas import FlexSigRequest
    try:
        FlexSigRequest(procedure_type="flex_sig", weight_band="over-50", prep_type="lactulose", **BASE)
    except pydantic.ValidationError:
        return
    raise AssertionError("lactulose on over-50 band should raise ValidationError")


def test_registry_has_flexsig_base():
    import importlib.util
    p = pathlib.Path(__file__).resolve().parent.parent / "vendor" / "egd-handout-generator" / "scripts" / "compose.py"
    _MOD_NAME = "vc_flexsig"
    spec = importlib.util.spec_from_file_location(_MOD_NAME, p)
    m = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = m
    spec.loader.exec_module(m)
    reg = m.load_registry()
    assert reg["bases"]["flexsig"]["title_fragment_en"] == "Flexible Sigmoidoscopy"


if __name__ == "__main__":
    for fn in [test_flexsig_schema_accepts_prep_type,
               test_flexsig_lactulose_band_gating,
               test_registry_has_flexsig_base]:
        fn()
        print(f"PASS {fn.__name__}")
