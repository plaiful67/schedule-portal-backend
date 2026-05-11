#!/usr/bin/env python3
"""Copy the three production skills from ~/.claude/skills/ into vendor/.

The backend imports their pure functions at runtime. We vendor them rather
than git-submodule because the skills aren't standalone GitHub repos.

Excludes .venv/, __pycache__/, *.pyc, and anything in .gitignore.
"""
import shutil
import sys
from pathlib import Path

HOME_SKILLS = Path.home() / ".claude" / "skills"
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


if __name__ == "__main__":
    main()
