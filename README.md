# schedule.giready.com backend

FastAPI service that renders personalized pre-procedure PDFs for the
`schedule.giready.com` portal. Imports rendering functions from the three
production skills (vendored under `vendor/`) and adds an appointment-callout
block plus a stop-meds list to each handout.

## Architecture

```
~/.claude/skills/{bowel-prep,egd-handout,flex-sig-handout}-generator/
                       │
                       │ (one-way copy)
                       ▼
              vendor/ ───────────────────┐
                                         │
                  ┌──────────────────────┴────────────┐
                  │  FastAPI                          │
                  │  POST /render → WeasyPrint → PDF  │
                  │  GET /medications                 │
                  └───────────────────────────────────┘
                                         ▲
                                         │ JSON
              schedule-giready/  (Cloudflare Pages frontend)
```

The skill modules are imported at runtime via `importlib` under unique
names (so two different `render.py` files can co-exist in one process).
Their internal path constants (`SKILL_DIR`, `TEMPLATES`, `PARTIALS_DIR`, …)
are rebound at import time to either:

- **`~/.claude/skills/<skill>/`** — when that directory exists. Used for
  local dev so edits to the production skill land in the next portal
  render without any sync step. Practice-yaml and partial caches are
  also reset per request so live YAML edits land too.
- **`vendor/<skill>/`** — fallback. Used inside the Docker image where
  the home dir doesn't carry the skills.

Override with `PORTAL_SKILL_SOURCE=vendor make dev` to force the
production-style behavior locally. `GET /healthz` returns the resolved
source per skill so you can verify at a glance.

## Local development

```bash
make install         # one-time: create .venv, install deps
make vendor-sync     # re-copy skills from ~/.claude/skills/ (do this after a skill edit)
make dev             # uvicorn on :8000
make smoke           # POST scripts/smoke-payload.json → /tmp/smoke.pdf
```

WeasyPrint needs Pango/Cairo: `brew install pango`.

## API

### `POST /render`

Request body (Pydantic-validated):

```json
{
  "procedure_type": "bowel_prep",
  "location_id": "scc",
  "language": "en",
  "appointment_date": "2026-06-15",
  "appointment_time": "08:30",
  "arrival_time": "07:30",
  "weight_band": "31-40",
  "stop_meds": ["ibuprofen", "semaglutide"]
}
```

Response: `application/pdf` with `Content-Disposition: inline`.

Phase 1 implements `bowel_prep` only; other `procedure_type` values 501.

### `GET /medications?lang=en`

Returns the parsed `data/medications.yaml`, collapsed to the requested
language. Feeds the frontend autocomplete + always-visible cheat sheet.

## Constraints

- **No PHI**: no name fields are accepted; nothing is persisted; the deep-link
  token in the QR encodes only the request payload.
- **Production skills are not modified.** The personalized template lives in
  `app/templates/`, not in the skill, so vendor re-syncs are safe.
- Cloud Run target image: `gcr.io/$PROJECT/schedule-portal`. See plan doc
  for deploy commands.
