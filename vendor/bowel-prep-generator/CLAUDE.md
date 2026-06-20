# bowel-prep-generator

Source-of-truth Python skill for the giready bowel-prep + combined EGD+colonoscopy handouts. Renders PDFs and feeds the mobile sites at `prep.giready.com`, `prep86.giready.com`, `egdcolon.giready.com`, and `egdcolon86.giready.com`. See `SKILL.md` and `README.md` for the full skill spec.

## Agents available locally

Picked up automatically by Claude Code (CLI, web, iOS) via this repo's `.claude/agents/`:

- `giready-validate-render-gate` — runs `scripts/validate.py`, re-renders all PDFs to `/tmp/giready-gate/`, audits page counts. Pass/fail gate.
- `giready-live-site-smoke` — fetches the live `prep*.giready.com` and `egdcolon*.giready.com` pages and checks for required strings.

## Standard chain (skill repo scope)

1. Edit templates / `data/dosing.yaml` / `practice.yaml`.
2. Run `giready-validate-render-gate` — must pass before commit.
3. Commit and push to `main`.
4. Site rebuild + deploy is driven from the meta repo (`~/peds-gi-prep-system/`): `make sites` → `make deploy`. Re-run `giready-live-site-smoke` after deploy to confirm Cloudflare picked it up.

## Calendar export — boundaries that bind (2026-06-11)

The mobile "Add this schedule to your calendar" feature is **timing-only and
client-side-only by design**:

- `render.build_calendar_events()` (in `scripts/render.py`) emits structured
  milestone events into each page as `{{PZ_EVENTS_JSON}}`; browser JS in
  `templates/partials/_personalize.{en,es}.html` turns them into a `.ics`
  Blob + Google Calendar links once the parent personalizes the page. The
  entered procedure date **never leaves the device** — no fetch, no query
  params; `gi.track()` calls are event-name-only.
- **Never compute a dose in JS.** Dose amounts appear in event descriptions
  only as verbatim build-time handout strings. This is the documented
  non-SaMD boundary — timing arithmetic only.
- `.ics` uses **floating local times** (no TZID) so events match the paper
  handout's wall-clock times across DST.
- Timing twins in `dosing.yaml` (`calendar:` block, `dose1_window_*_hhmm`)
  are cross-checked against prose by `validate.py` check [3e]; the golden
  snapshot is `tests/golden/calendar_events.json` (`validate.py --quick
  --update-golden` after intentional changes — review the diff).
- JS regression harness: `scripts/build_dev_test_page.py`, then headless
  Chrome with `--virtual-time-budget` (see the script docstring).
- Handout pages deliberately ship **no web-app manifest** (the apex
  manifest would hijack Add-to-Home-Screen — see DEVELOPER_HANDOFF §6) and
  no `apple-mobile-web-app-capable`.
