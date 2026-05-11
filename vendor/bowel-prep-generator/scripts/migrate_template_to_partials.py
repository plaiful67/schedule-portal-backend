#!/usr/bin/env python3
"""Identify which existing partial bodies are inlined in a given template.

Usage:
    python scripts/migrate_template_to_partials.py templates/combined-print.en.html

For each partial under templates/partials/_*.{lang}.html (matching the
template's language suffix, inferred from the filename), the script reports
whether the partial's exact body is present in the target template, and if
so at what character offset.

This is a *report-only* tool — it does NOT modify files. It exists to make
the next propagation pass (extending the partials architecture from
standard-print.{en,es}.html to the other 24 templates) auditable: a maintainer
runs this on each template, manually inspects the matches, and then
hand-substitutes the placeholder tokens.

Limitations:
- Reports only EXACT (byte-for-byte) substring matches. If the candidate
  template has even a single character of drift (e.g. a different inline
  heading or a renamed icon), the partial will not match — that's a feature,
  not a bug, since silent partial-substitution drift is exactly what we want
  to surface during migration.
- Does not handle "almost-matches" with whitespace differences. If you want
  fuzzy matching, diff the candidate's section against the partial body
  manually.
"""
from __future__ import annotations
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
PARTIALS = SKILL_DIR / "templates" / "partials"


def lang_from_template_filename(name: str) -> str:
    """Infer 'en' or 'es' from a filename like 'combined-print.en.html'."""
    if name.endswith(".en.html"):
        return "en"
    if name.endswith(".es.html"):
        return "es"
    raise SystemExit(f"Cannot infer language suffix from: {name}")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip())
    target = Path(sys.argv[1]).resolve()
    if not target.exists():
        sys.exit(f"Template not found: {target}")
    lang = lang_from_template_filename(target.name)
    body = target.read_text(encoding="utf-8")

    suffix = f".{lang}.html"
    matches = []
    misses = []
    if not PARTIALS.is_dir():
        sys.exit(f"Partials directory not found: {PARTIALS}")
    for p in sorted(PARTIALS.glob(f"_*{suffix}")):
        name = p.name[1:-len(suffix)]
        token = "{{PARTIAL_" + name.upper() + "}}"
        partial_body = p.read_text(encoding="utf-8")
        idx = body.find(partial_body)
        if idx >= 0:
            extra = body.find(partial_body, idx + 1)
            multi = " (also at later offsets — partial appears more than once)" if extra >= 0 else ""
            matches.append((name, token, idx, len(partial_body), multi))
        else:
            misses.append((name, len(partial_body)))

    print(f"Target:   {target}")
    print(f"Language: {lang}")
    print(f"Partials directory: {PARTIALS}")
    print()
    print(f"== {len(matches)} partial(s) found inline in target ==")
    for name, token, idx, n, multi in matches:
        print(f"  + _{name}.{lang}.html  ({n} chars)  at byte {idx}  → replace with {token}{multi}")
    print()
    print(f"== {len(misses)} partial(s) NOT found inline ==")
    for name, n in misses:
        print(f"  - _{name}.{lang}.html  ({n} chars)  — section absent or has drifted from the standard-print body")
    print()
    print("Next steps (manual):")
    print("  1. For each match above, replace the corresponding section of the")
    print("     target template with the {{PARTIAL_<NAME>}} token.")
    print("  2. For each miss, decide: (a) extract a new partial (variant-specific),")
    print("     or (b) leave inline because the section genuinely differs across templates.")
    print("  3. Run the smoke test (render PDF before & after, diff HTML) to confirm")
    print("     byte-identical output.")


if __name__ == "__main__":
    main()
