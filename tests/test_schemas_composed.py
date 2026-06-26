import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app.schemas import RenderRequest
from pydantic import TypeAdapter

ADAPTER = TypeAdapter(RenderRequest)

BASE = dict(location_id="scc", language="en", physician_id="zavoian",
            appointment_date="2099-01-01", appointment_time="07:30", arrival_time="06:30")


def test_composed_parses_with_addons():
    req = ADAPTER.validate_python({**BASE, "procedure_type": "composed",
                                   "add_ons": ["dise"], "knob_picks": {}})
    assert req.procedure_type == "composed"
    assert req.add_ons == ["dise"]
    assert req.knob_picks == {}


def test_composed_requires_at_least_one_addon():
    try:
        ADAPTER.validate_python({**BASE, "procedure_type": "composed", "add_ons": []})
    except Exception:
        return
    raise AssertionError("empty add_ons should fail validation")


def test_composed_knob_picks_optional():
    req = ADAPTER.validate_python({**BASE, "procedure_type": "composed", "add_ons": ["dlb"]})
    assert req.knob_picks == {}


def test_composed_defaults_base_egd():
    req = ADAPTER.validate_python({**BASE, "procedure_type": "composed", "add_ons": ["dise"]})
    assert req.base == "egd"
    assert req.weight_band is None


def test_composed_colonoscopy_requires_band():
    try:
        ADAPTER.validate_python({**BASE, "procedure_type": "composed",
                                 "base": "colonoscopy", "add_ons": ["dlb"]})
    except Exception:
        return
    raise AssertionError("colonoscopy base without weight_band should fail")


def test_composed_combined_with_band_parses():
    req = ADAPTER.validate_python({**BASE, "procedure_type": "composed",
                                   "base": "combined", "weight_band": "31-40",
                                   "prep_type": "miralax", "add_ons": ["dlb"]})
    assert req.base == "combined"
    assert req.weight_band == "31-40"
    assert req.prep_type == "miralax"


def test_composed_egd_base_rejects_band():
    try:
        ADAPTER.validate_python({**BASE, "procedure_type": "composed",
                                 "base": "egd", "weight_band": "31-40", "add_ons": ["dise"]})
    except Exception:
        return
    raise AssertionError("egd base with weight_band should fail")


def test_composed_lactulose_band_guard():
    try:
        ADAPTER.validate_python({**BASE, "procedure_type": "composed",
                                 "base": "colonoscopy", "weight_band": "over-50",
                                 "prep_type": "lactulose", "add_ons": ["dlb"]})
    except Exception:
        return
    raise AssertionError("lactulose on over-50 should fail (small-kid-only)")


if __name__ == "__main__":
    for fn in [test_composed_parses_with_addons, test_composed_requires_at_least_one_addon,
               test_composed_knob_picks_optional, test_composed_defaults_base_egd,
               test_composed_colonoscopy_requires_band, test_composed_combined_with_band_parses,
               test_composed_egd_base_rejects_band, test_composed_lactulose_band_guard]:
        fn(); print(f"PASS {fn.__name__}")
