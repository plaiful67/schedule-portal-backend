#!/usr/bin/env python3
"""Verify the committed personalized templates match what
`build_personalized_templates.py` produces from the current vendored
canonical templates.

Fails (non-zero exit + unified diff to stderr) if:
  - the build script crashes
  - any output file is missing
  - the on-disk personalized template differs from the freshly built version

Wired into `make drift-check` and is intended for CI so a stale committed
copy can't get deployed silently.
"""
from __future__ import annotations

import difflib
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
OUT_DIR = BACKEND_DIR / "app" / "templates" / "bowel_prep"


def _load_builder():
    """Import build_personalized_templates as a module so we can call its
    patch functions directly without spawning a subprocess.
    """
    spec = importlib.util.spec_from_file_location(
        "_build_personalized_templates",
        SCRIPTS_DIR / "build_personalized_templates.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError("Could not load build_personalized_templates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    builder = _load_builder()

    failures: list[str] = []
    diffs_printed = False

    for canonical_name, out_name, patch_fn in builder.VARIANTS:
        canonical_path = builder.VENDOR_TEMPLATES / canonical_name
        committed_path = OUT_DIR / out_name
        if not canonical_path.exists():
            failures.append(
                f"{canonical_name}: vendor copy missing at {canonical_path}. "
                f"Run `make vendor-sync` first."
            )
            continue
        if not committed_path.exists():
            failures.append(
                f"{out_name}: committed personalized template missing at {committed_path}."
            )
            continue
        try:
            expected = patch_fn(canonical_path.read_text(encoding="utf-8"))
        except RuntimeError as e:
            failures.append(f"{canonical_name}: patch failed → {e}")
            continue
        actual = committed_path.read_text(encoding="utf-8")
        if expected != actual:
            failures.append(f"{out_name}: drift detected")
            diff = difflib.unified_diff(
                actual.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=f"{out_name} (on disk)",
                tofile=f"{out_name} (rebuilt from canonical)",
                n=3,
            )
            sys.stderr.writelines(diff)
            sys.stderr.write("\n")
            diffs_printed = True

    if failures:
        if not diffs_printed:
            print("\nFAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print("\nFix: run `make vendor-sync` (regenerates personalized templates).", file=sys.stderr)
        return 1

    print("OK: all personalized templates match their canonical sources.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
