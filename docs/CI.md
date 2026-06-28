# Backend CI — auto-deploy, smoke-gated, keyless

`.github/workflows/deploy.yml` deploys `schedule-portal` to Cloud Run so a bad
revision **never serves a user**: it deploys a `--no-traffic` candidate, smokes
it via its tagged URL, and migrates 100% traffic **only if the smoke passes**. A
failing smoke leaves the prior revision serving (that is the rollback).

## Flow
1. `actions/checkout` → keyless GCP auth via **Workload Identity Federation** (no
   service-account key) → `setup-gcloud`.
2. `gcloud run deploy schedule-portal --source . --no-traffic --tag candidate …`
   (flags mirror `scripts/deploy.sh`). Builds from the **committed `vendor/`** —
   the runner has no `~/.claude/skills` to vendor-sync from.
3. Smoke the candidate: `python scripts/smoke_scheduler_pdf.py --base-url <candidate>`
   (Calm fonts embedded + PDF/UA + no leaked tokens, on the real generated PDF).
4. Pass → `update-traffic --to-tags candidate=100`. Fail → job fails, traffic
   stays put, candidate URL printed for triage.

## Trigger state
**`workflow_dispatch` only** right now (Actions → Run workflow). The `push:
[main]` trigger is **commented out** in deploy.yml — uncommenting it is the
auto-deploy **cutover** (Sebastian's explicit go), the one "could break prod"
moment.

## Local responsibilities (NOT done by CI)
- **`make vendor-check` before pushing.** CI builds the committed `vendor/`, so a
  forgotten `make vendor-sync` ships stale skill code. `vendor-check` re-syncs and
  fails if `vendor/` changed.
- `scripts/_ci/calm_assert.py` is a CI-vendored copy of the meta repo's
  `calm_assert.py` (the runner has no meta checkout). It rarely changes; if you
  edit the meta original, refresh this copy.

## One-time setup (the checkpoint executions)
1. **`bash scripts/setup_wif.sh`** — provisions the WIF pool/provider + the
   least-privilege deploy SA (`gha-deploy-schedule@giready-portal…`). Mutates GCP
   IAM; idempotent; run once, intentionally.
2. **First manual run** — Actions → "deploy" → Run workflow. Validates
   deploy→smoke→migrate against a real `--no-traffic` candidate (safe: 0% traffic
   until smoke-green).
3. **Cutover** — uncomment the `push: [main]` trigger in deploy.yml.

## Manual fallbacks
- `make deploy` — the old manual path (still works).
- `make revisions` / `make rollback REV=<rev>` — inspect + roll back traffic.
