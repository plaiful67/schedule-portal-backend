# egd-handout-generator

Source-of-truth Python skill for the giready EGD-only patient handouts. Renders PDFs and feeds the mobile sites at `egd.giready.com` and `egd86.giready.com`. See `README.md` for the full skill spec.

## Agents available locally

Picked up automatically by Claude Code (CLI, web, iOS) via this repo's `.claude/agents/`:

- `giready-validate-render-gate` — runs `scripts/validate.py`, re-renders all PDFs to `/tmp/giready-gate/`, audits page counts. Pass/fail gate.
- `giready-live-site-smoke` — fetches the live `egd*.giready.com` pages and checks for required strings.

## Standard chain (skill repo scope)

1. Edit templates / `data/procedure.yaml` / `practice.yaml`.
2. Run `giready-validate-render-gate` — must pass before commit.
3. Commit and push to `main`.
4. Site rebuild + deploy is driven from the meta repo (`~/peds-gi-prep-system/`): `make sites` → `make deploy`. Re-run `giready-live-site-smoke` after deploy to confirm Cloudflare picked it up.
