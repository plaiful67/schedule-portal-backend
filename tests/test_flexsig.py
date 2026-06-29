"""Task 1: Registry flexsig base + FlexSigRequest.prep_type schema tests.

Task 2 (adapter/dispatch) adds the render endpoint tests below.
  pdfminer/pdfminer.six is NOT installed in the backend venv, so
  test_render_flexsig_pdf_says_flexible_sigmoidoscopy is SKIPPED (marked with
  pytest.skip / a note in the report). Only status/PDF/filename is asserted.

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


def test_flexsig_accepts_lactulose_on_small_band():
    from app.schemas import FlexSigRequest
    r = FlexSigRequest(procedure_type="flex_sig", weight_band="21-30", prep_type="lactulose", **BASE)
    assert r.prep_type == "lactulose"


def test_flexsig_rejects_lactulose_on_large_band():
    from app.schemas import FlexSigRequest
    try:
        FlexSigRequest(procedure_type="flex_sig", weight_band="over-50", prep_type="lactulose", **BASE)
    except pydantic.ValidationError:
        return
    raise AssertionError("lactulose on over-50 band should raise ValidationError")


def test_flexsig_rejects_unsupported_prep():
    # CLENPIQ/SUPREP/enema aren't valid for FlexSigRequest (clenpiq/suprep not
    # ordered for flex sig; enema is a separate render path).
    from app.schemas import FlexSigRequest
    try:
        FlexSigRequest(procedure_type="flex_sig", weight_band="31-40", prep_type="clenpiq", **BASE)
    except pydantic.ValidationError:
        return
    raise AssertionError("clenpiq prep on flex_sig should raise ValidationError")


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


# ---------------------------------------------------------------------------
# Task 2: render endpoint tests
# ---------------------------------------------------------------------------
# pdfminer.six is NOT installed in the backend venv.
# test_render_flexsig_pdf_says_flexible_sigmoidoscopy is therefore SKIPPED
# (see pytest.importorskip below and the report note).

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# BASE matches what the scheduler frontend sends; include_directions=False keeps
# the fixture fast (no directions page to generate).
_RENDER_BASE = dict(
    location_id="scc",
    language="en",
    physician_id="zavoian",
    appointment_date="2099-01-01",
    appointment_time="07:30",
    arrival_time="06:30",
    include_directions=False,
)


def test_render_flexsig_miralax_returns_pdf():
    p = dict(procedure_type="flex_sig", weight_band="31-40", prep_type="miralax",
             **_RENDER_BASE)
    r = client.post("/render", json=p)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    assert "FlexSig" in r.headers.get("content-disposition", "")


def test_render_flexsig_miralax_small_band_returns_pdf():
    # A second MiraLAX band to exercise the relabel path across bands.
    p = dict(procedure_type="flex_sig", weight_band="21-30", prep_type="miralax",
             **_RENDER_BASE)
    r = client.post("/render", json=p)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    assert "FlexSig" in r.headers.get("content-disposition", "")


def test_render_flexsig_lactulose_returns_pdf():
    # Lactulose flex sig: relabels the lactulose colonoscopy template (≤30 kg band).
    p = dict(procedure_type="flex_sig", weight_band="21-30", prep_type="lactulose",
             **_RENDER_BASE)
    r = client.post("/render", json=p)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    assert "FlexSig" in r.headers.get("content-disposition", "")


def test_render_flexsig_pdf_says_flexible_sigmoidoscopy():
    """PDF text-content test — requires pdfminer.six in the backend venv.
    SKIPPED: pdfminer.six is not installed; status/PDF/filename asserted instead
    by test_render_flexsig_miralax_returns_pdf."""
    try:
        import pytest
        pytest.importorskip("pdfminer",
            reason="pdfminer.six not in backend venv — pdf-text assertion skipped")
    except ImportError:
        # pytest not installed — skip gracefully when run via run_all.py
        print("SKIP test_render_flexsig_pdf_says_flexible_sigmoidoscopy "
              "(pdfminer.six not in venv)")
        return
    from pdfminer.high_level import extract_text
    import io
    p = dict(procedure_type="flex_sig", weight_band="31-40", prep_type="miralax",
             **_RENDER_BASE)
    r = client.post("/render", json=p)
    txt = extract_text(io.BytesIO(r.content))
    assert "Flexible Sigmoidoscopy" in txt
    assert "Colonoscopy" not in txt.replace("Flexible Sigmoidoscopy", "")


if __name__ == "__main__":
    for fn in [test_flexsig_schema_accepts_prep_type,
               test_flexsig_accepts_lactulose_on_small_band,
               test_flexsig_rejects_lactulose_on_large_band,
               test_flexsig_rejects_unsupported_prep,
               test_registry_has_flexsig_base,
               test_render_flexsig_miralax_returns_pdf,
               test_render_flexsig_miralax_small_band_returns_pdf,
               test_render_flexsig_lactulose_returns_pdf]:
        fn()
        print(f"PASS {fn.__name__}")
