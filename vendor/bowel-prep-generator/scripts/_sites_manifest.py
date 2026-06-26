"""Load + validate the site build manifest (data/sites.yaml) into typed rows."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import sys

try:
    import yaml
except ImportError:
    sys.exit("ERROR: PyYAML required: pip install pyyaml")

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = SKILL_DIR / "data" / "sites.yaml"

_LANDING = {"picker", "picker-banner", "single"}


@dataclass
class SiteRow:
    id: str
    family: str
    landing: str
    bands: list[str]
    repos: dict[str, str]
    subdomains: dict[str, str]
    titles: dict[str, str] = field(default_factory=dict)


def load_sites(path: Path | str | None = None) -> list[SiteRow]:
    path = Path(path) if path else DEFAULT_MANIFEST
    if not path.exists():
        sys.exit(f"ERROR: manifest not found: {path}")
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    rows: list[SiteRow] = []
    seen_repos: set[str] = set()
    for r in raw["sites"]:
        row = SiteRow(
            id=r["id"], family=r["family"], landing=r["landing"],
            bands=list(r["bands"]), repos=dict(r["repos"]),
            subdomains=dict(r["subdomains"]), titles=dict(r.get("titles", {})),
        )
        if row.landing not in _LANDING:
            sys.exit(f"{row.id}: bad landing {row.landing!r}; expected {_LANDING}")
        for d in row.repos.values():
            if d in seen_repos:
                sys.exit(f"{row.id}: duplicate repo dir {d!r}")
            seen_repos.add(d)
        rows.append(row)
    return rows
