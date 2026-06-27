#!/usr/bin/env python3
"""Copy the three production skills from ~/.claude/skills/ into vendor/, plus
the shared meta-repo layer from ~/peds-gi-prep-system/shared/ into vendor/shared/.

The backend imports the skills' pure functions at runtime. We vendor them rather
than git-submodule because the skills aren't standalone GitHub repos. The skills'
render.py resolves the shared layer as `SKILL_DIR.parent / "shared"` first
(= vendor/shared inside the Cloud Run image, where ~/peds-gi-prep-system does
not exist), so vendor/shared must be kept in sync here — it carries
practice-core.yaml (and the shared CSS/JS) the renders depend on.

Excludes .venv/, __pycache__/, *.pyc, and anything in .gitignore.
"""
import shutil
import sys
from pathlib import Path

HOME_SKILLS = Path.home() / ".claude" / "skills"
SHARED_SRC = Path.home() / "peds-gi-prep-system" / "shared"
TENANTS_SRC = Path.home() / "peds-gi-prep-system" / "tenants"
VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"

SKILLS = [
    "bowel-prep-generator",
    "egd-handout-generator",
    "flex-sig-handout-generator",
]

EXCLUDES = {".venv", "__pycache__", ".pytest_cache", "node_modules", ".git", ".pre-commit-config.yaml"}


def ignore(_, names):
    return [n for n in names if n in EXCLUDES or n.endswith(".pyc")]


def main():
    if not HOME_SKILLS.exists():
        print(f"FATAL: {HOME_SKILLS} not found.", file=sys.stderr)
        sys.exit(1)

    VENDOR_DIR.mkdir(exist_ok=True)
    for name in SKILLS:
        src = HOME_SKILLS / name
        dst = VENDOR_DIR / name
        if not src.exists():
            print(f"SKIP {name}: source not at {src}")
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=ignore)
        print(f"OK   {name} → {dst}")

    # Shared meta-repo layer (practice-core.yaml, print/mobile CSS, a11y JS).
    # Resolved by the skills as SKILL_DIR.parent/"shared" → vendor/shared.
    if SHARED_SRC.exists():
        shared_dst = VENDOR_DIR / "shared"
        if shared_dst.exists():
            shutil.rmtree(shared_dst)
        shutil.copytree(SHARED_SRC, shared_dst, ignore=ignore)
        print(f"OK   shared → {shared_dst}")
    else:
        print(f"SKIP shared: source not at {SHARED_SRC}")

    # Tenant records (tenants/<id>/tenant.yaml). The resolver
    # (vendor/shared/tenant_resolver.py) looks for tenants/ as a sibling of
    # shared/ first — so vendor/tenants must be a sibling of vendor/shared.
    # Without this the backend's /config + per-tenant roster fall back to the
    # giready default on Cloud Run (where ~/peds-gi-prep-system doesn't exist).
    if TENANTS_SRC.exists():
        tenants_dst = VENDOR_DIR / "tenants"
        if tenants_dst.exists():
            shutil.rmtree(tenants_dst)
        shutil.copytree(TENANTS_SRC, tenants_dst, ignore=ignore)
        print(f"OK   tenants → {tenants_dst}")
    else:
        print(f"SKIP tenants: source not at {TENANTS_SRC}")


if __name__ == "__main__":
    main()
