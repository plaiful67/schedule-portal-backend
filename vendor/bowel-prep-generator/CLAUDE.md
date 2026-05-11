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
