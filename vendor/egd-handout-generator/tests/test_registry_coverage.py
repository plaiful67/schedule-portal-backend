import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import check_registry_coverage as cov


def test_shipped_registry_is_clean():
    assert cov.audit() == []


def test_audit_flags_missing_blurb():
    reg = {
        "bases": {"egd": {"title_fragment_en": "EGD", "title_fragment_es": "EGD"}},
        "add_ons": {"broken": {"title_fragment_en": "X", "title_fragment_es": "X"}},  # no blurb
        "specialties": {},
        "knobs": {},
    }
    problems = cov.audit(reg)
    assert any("broken" in p for p in problems)


if __name__ == "__main__":
    fns = [test_shipped_registry_is_clean, test_audit_flags_missing_blurb]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
