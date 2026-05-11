# Flexible-sigmoidoscopy handout generator. Common workflows.
#
# First time on a new machine:
#   1. brew install pango   # macOS — WeasyPrint dep
#   2. make install
#   3. make render
#
# Day-to-day:
#   - Edit data/procedure.yaml for clinical changes (bands, enema dosing,
#     locations, NPO).
#   - Edit practice.yaml for branding/contact/QR-target changes.
#   - make render  →  regenerates the full set into ~/Desktop/flex-sig-pdf-review.

PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
RENDER := $(PYTHON) scripts/render.py

REVIEW_OUT := $(HOME)/Desktop/peds-gi-system/flex-sig-pdf-review

.PHONY: help install render render-color render-light render-scc render-pmch sites clean-review

help:
	@echo "Targets:"
	@echo "  install         Create .venv and install dependencies (run once)."
	@echo "  render          All PDFs (both locations × EN/ES × 3 bands, color theme) → $(REVIEW_OUT)"
	@echo "  render-color    Color-theme PDFs only"
	@echo "  render-light    Toner-friendly PDFs only"
	@echo "  render-scc      SCC PDFs only"
	@echo "  render-pmch     PMCH PDFs only"
	@echo "  sites           Build flexsig + flexsig86 mobile site repos in ~/Desktop/peds-gi-system/"
	@echo "  clean-review    Remove only the review folder"

install:
	@if [ ! -d .venv ]; then python3 -m venv .venv; fi
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo
	@echo "Done. If WeasyPrint complains about Pango/Cairo at runtime, run: brew install pango"

render:
	$(RENDER) --out "$(REVIEW_OUT)" --theme color

render-color:
	$(RENDER) --out "$(REVIEW_OUT)" --theme color

render-light:
	$(RENDER) --out "$(REVIEW_OUT)" --theme print-light

render-scc:
	$(RENDER) --out "$(REVIEW_OUT)" --location scc --theme color

render-pmch:
	$(RENDER) --out "$(REVIEW_OUT)" --location pmch --theme color

sites:
	$(PYTHON) scripts/build_websites.py

clean-review:
	rm -rf "$(REVIEW_OUT)"
