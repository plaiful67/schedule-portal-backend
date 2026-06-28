SHELL := /bin/bash

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn

.PHONY: install vendor-sync sync-directions dev test drift-check deploy build-image clean rollback revisions vendor-check

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

vendor-sync: sync-directions
	$(PY) scripts/vendor_sync.py
	$(PY) scripts/build_personalized_templates.py

# Re-render the four directions PDFs that get stitched onto every prep
# handout. Portal-local build_directions.py renders them with the public-
# site footer (privacy/terms/disclaimer) stripped — those don't belong on
# a clinical print artifact. The skill's render_directions.py is also
# invoked so ~/Desktop/peds-gi-system/ stays current for the public-site
# variants, but the PDFs baked into the Cloud Run image come from
# build_directions.py.
SKILL_DIR := $(HOME)/.claude/skills/bowel-prep-generator
DESKTOP   := $(HOME)/Desktop/peds-gi-system
DIR_OUT   := app/static/directions

sync-directions:
	mkdir -p $(DIR_OUT)
	cd $(SKILL_DIR) && .venv/bin/python scripts/render_directions.py --location all --lang both
	$(PY) scripts/build_directions.py
	@ls -lh $(DIR_OUT)/*.pdf

dev:
	$(UVICORN) app.main:app --reload --host 127.0.0.1 --port 8000

drift-check:
	$(PY) scripts/check_template_drift.py
	$(PY) scripts/check_personalized_drift.py

smoke:
	curl -sS -X POST http://127.0.0.1:8000/render \
		-H "Content-Type: application/json" \
		-d @scripts/smoke-payload.json \
		-o /tmp/smoke.pdf
	@echo "Wrote /tmp/smoke.pdf ($$(stat -f%z /tmp/smoke.pdf) bytes)"

# Cloud Run deploy. Manual escape hatch — the PRIMARY path is CI
# (.github/workflows/deploy.yml: no-traffic candidate → smoke → migrate). Vendor
# the skills first so the container has them baked in.
deploy: vendor-sync
	./scripts/deploy.sh

# LOCAL pre-push guard: CI builds from the COMMITTED vendor/, so a forgotten
# vendor-sync would ship stale skill code. This re-runs the copy and FAILS if
# vendor/ changed (= you forgot to commit a sync). Run before pushing.
vendor-check:
	@$(PY) scripts/vendor_sync.py >/dev/null
	@if ! git diff --quiet -- vendor/; then \
		echo "❌ vendor/ is STALE — run 'make vendor-sync' and commit it before pushing."; \
		git --no-pager diff --stat -- vendor/; exit 1; \
	else echo "✓ vendor/ is in sync with the skills."; fi
	@# The CI image builds from git, so a present-but-gitignored template is MISSING
	@# from the image → a clean-build 500 (as combined-print-personalized.* was).
	@ignored=$$(git status --ignored --porcelain app/templates/ 2>/dev/null | grep '^!!' || true); \
	if [ -n "$$ignored" ]; then \
		echo "❌ gitignored template(s) under app/templates/ — they will be MISSING from the CI image:"; \
		echo "$$ignored"; \
		echo "   commit them (the image builds from git), or the deploy will 500 on those procedures."; exit 1; \
	else echo "✓ no app/templates file is gitignored (all present templates are tracked)."; fi

# Traffic ops (CI deploys are smoke-gated, but a post-migration regression the
# smoke can't catch is rolled back here):
#   make revisions               list recent revisions + current traffic split
#   make rollback REV=<revision> route 100% traffic to a prior revision
revisions:
	@gcloud run revisions list --service schedule-portal --region us-central1 \
		--format='table(metadata.name, status.conditions[0].status, metadata.creationTimestamp)' | head -12
	@echo "--- current traffic split ---"
	@gcloud run services describe schedule-portal --region us-central1 --format='value(status.traffic)'

rollback:
	@test -n "$(REV)" || { echo "usage: make rollback REV=<revision-name>  (see 'make revisions')"; exit 2; }
	gcloud run services update-traffic schedule-portal --region us-central1 --to-revisions "$(REV)=100"
	@echo "✓ traffic routed to $(REV)"

# Local docker build smoke (optional; needs docker installed).
build-image: vendor-sync
	docker build -t schedule-portal:local .

clean:
	rm -rf $(VENV) __pycache__ app/__pycache__ app/adapters/__pycache__
