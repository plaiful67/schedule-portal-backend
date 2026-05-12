SHELL := /bin/bash

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn

.PHONY: install vendor-sync dev test drift-check deploy build-image clean

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

vendor-sync:
	$(PY) scripts/vendor_sync.py
	$(PY) scripts/build_personalized_templates.py

dev:
	$(UVICORN) app.main:app --reload --host 127.0.0.1 --port 8000

drift-check:
	$(PY) scripts/check_template_drift.py

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
