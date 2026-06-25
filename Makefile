SHELL := /bin/bash

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn

.PHONY: install vendor-sync sync-directions dev test drift-check deploy build-image clean

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

# Cloud Run deploy. Vendor the skills first so the container has them baked in.
deploy: vendor-sync
	./scripts/deploy.sh

# Local docker build smoke (optional; needs docker installed).
build-image: vendor-sync
	docker build -t schedule-portal:local .

clean:
	rm -rf $(VENV) __pycache__ app/__pycache__ app/adapters/__pycache__
