# Validation suite

`scripts/validate.py` catches the silent-failure classes of bug we've hit while iterating on this skill:

- **Unresolved `{{PLACEHOLDER}}` tokens** that slip through into rendered patient output (a leaked token is embarrassing at best, dangerous at worst if it's hiding a missing dose number)
- **render.py errors** for any band × language × variant combination
- **Orphan placeholders** — used in a template but never set anywhere in the scripts that render templates (usually a typo in either place)
- **Missing output files** after rendering

## How to run

```bash
# Full suite — renders all 28 band×lang×variant combos, ~2-3 minutes
.venv/bin/python scripts/validate.py

# Lint only — sub-second placeholder check, no rendering
.venv/bin/python scripts/validate.py --quick

# Limit to specific bands or variants
.venv/bin/python scripts/validate.py --bands 21-30,over-50
.venv/bin/python scripts/validate.py --variants standard
```

Exit code `0` = all passed; non-zero = at least one check failed (with a per-failure line listed at the end).

## Pre-commit hook

Optional but recommended. Installs a hook that runs `--quick` validation on commits that touch templates, dosing data, practice config, or scripts:

```bash
bash scripts/install_pre_commit.sh
```

Bypass for emergency commits:

```bash
git commit --no-verify
```

The hook only runs the quick lint — full render-checks would slow commits unacceptably. Run the full suite manually before pushing or merging significant changes.

## What it doesn't (yet) check

- **Visual / layout regressions** (fonts, page-breaks, spacing). Use eyeball review of the rendered PDFs.
- **Site-build outputs**: `build_websites.py` is not invoked. It writes to `~/Desktop/peds-gi-system/` so it'd pollute the user's Desktop. Run `make sites` from the meta repo (`~/peds-gi-prep-system`) manually before pushing site repos.
- **Mobile/print content drift**: not yet implemented. Could add a heuristic comparing rendered text content between the two formats for the same band.

## Why this exists

In the iteration that produced commits `8dd2667`, `10ab766`, `6e6b232`, and `1535676`, several silent-failure bugs slipped through:

- An unresolved `{{HTML_TWO_DAYS_BEFORE_BLOCK}}` placeholder appeared in early test renders before render.py was correctly merging the partial.
- A placeholder regex missed digits (`PRACTICE_STACK_LINE_1`), so an "unresolved placeholder" warning silently underreported.
- The `_redirects` file was deleted in two site repos by `clean_repo()` before the issue was noticed (deferred to a separate decision; see Wednesday checklist).

`validate.py` would have caught the first two pre-commit. The third is intentionally left as a manual check (the build scripts' "remove legacy `_redirects`" behavior is a deliberate-but-debatable design choice, not a code bug).
