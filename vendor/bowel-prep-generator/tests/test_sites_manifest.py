import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from _sites_manifest import load_sites


def test_loads_all_eight_variants():
    rows = load_sites()
    ids = {r.id for r in rows}
    assert ids == {
        "colonoscopy", "combined",
        "lactulose", "lactulose_combined",
        "clenpiq", "clenpiq_combined",
        "suprep", "suprep_combined",
    }


def test_colonoscopy_row_shape():
    row = next(r for r in load_sites() if r.id == "colonoscopy")
    assert row.family == "colonoscopy"
    assert row.landing == "picker"
    assert row.repos == {"scc": "prep-giready", "pmch": "prep86-giready"}
    assert row.subdomains == {"scc": "prep", "pmch": "prep86"}


def test_clenpiq_is_single_landing():
    row = next(r for r in load_sites() if r.id == "clenpiq")
    assert row.landing == "single"
    assert row.bands == ["clenpiq"]
    assert row.repos == {"scc": "prepclenpiq-giready", "pmch": "prepclenpiq86-giready"}


def test_every_repo_dir_is_unique():
    dirs = [d for r in load_sites() for d in r.repos.values()]
    assert len(dirs) == len(set(dirs)) == 16


if __name__ == "__main__":
    tests = [
        test_loads_all_eight_variants,
        test_colonoscopy_row_shape,
        test_clenpiq_is_single_landing,
        test_every_repo_dir_is_unique,
    ]
    failed = False
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            failed = True
    if failed:
        sys.exit(1)
