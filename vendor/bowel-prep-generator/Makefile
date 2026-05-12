# Bowel-prep handout generator. Common workflows.
#
# First time on a new machine:
#   1. Install Pango/Cairo (WeasyPrint dependency):
#        brew install pango   # macOS
#   2. make install
#   3. make render-all  (or any of the targets below)
#
# Day-to-day:
#   - Edit data/dosing.yaml for clinical changes (bands, dosing, locations).
#   - Edit practice.yaml for branding/contact/QR-target changes.
#   - make render-all  →  regenerates the full set into ~/Desktop/bowel-prep-pdf-review.

PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
RENDER := $(PYTHON) scripts/render.py

# Output roots
SCC_OUT       := $(HOME)/Desktop/peds-gi-system/bowel-prep-handouts
PMCH_OUT      := $(HOME)/Desktop/peds-gi-system/bowel-prep-handouts-pmch
REVIEW_OUT    := $(HOME)/Desktop/peds-gi-system/bowel-prep-pdf-review

.PHONY: help install render-scc render-pmch render-pdf render-pdf-light \
        render-combined render-directions sites-colonoscopy sites-combined sites \
        render render-all clean-pdf-review

help:
	@echo "Targets:"
	@echo "  install            Create .venv and install dependencies (run once)."
	@echo "  render-scc         Render DOCX + HTML for SCC into $(SCC_OUT)"
	@echo "  render-pmch        Render DOCX + HTML for PMCH into $(PMCH_OUT)"
	@echo "  render-pdf         Render colonoscopy print PDFs (color) for both locations -> $(REVIEW_OUT)"
	@echo "  render-pdf-light   Render colonoscopy print PDFs (toner-friendly) -> $(REVIEW_OUT)"
	@echo "  render-combined    Render combined EGD+colonoscopy print PDFs (color) -> $(REVIEW_OUT)/combined"
	@echo "  render-directions  Render driving-directions PDFs for SCC and PMCH -> ~/Desktop/{loc}-directions.pdf"
	@echo "  sites-colonoscopy  Build prep / prep86 mobile-site repos"
	@echo "  sites-combined     Build egdcolon / egdcolon86 mobile-site repos"
	@echo "  sites              sites-colonoscopy + sites-combined"
	@echo "  render-all         Everything: DOCX + HTML for both locations + all PDF variants"
	@echo "  clean-pdf-review   Remove only the PDF review folder (does NOT touch SCC/PMCH dirs)"

install:
	@if [ ! -d .venv ]; then python3 -m venv .venv; fi
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo
	@echo "Done. If WeasyPrint complains about Pango/Cairo at runtime, run: brew install pango"

render-scc:
	$(RENDER) --out "$(SCC_OUT)" --location scc

render-pmch:
	$(RENDER) --out "$(PMCH_OUT)" --location pmch

render-pdf:
	$(RENDER) --out "$(REVIEW_OUT)/SCC-color"  --format pdf-print --location scc  --theme color
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-color" --format pdf-print --location pmch --theme color

render-pdf-light:
	$(RENDER) --out "$(REVIEW_OUT)/SCC-print-light"  --format pdf-print --location scc  --theme print-light
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-print-light" --format pdf-print --location pmch --theme print-light

render-combined:
	$(RENDER) --out "$(REVIEW_OUT)/SCC-combined-color"  --format pdf-print --location scc  --theme color --variant combined
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-combined-color" --format pdf-print --location pmch --theme color --variant combined

render-directions:
	$(PYTHON) scripts/render_directions.py --location all

sites-colonoscopy:
	$(PYTHON) scripts/build_colonoscopy_websites.py

sites-combined:
	$(PYTHON) scripts/build_combined_websites.py

sites: sites-colonoscopy sites-combined

render-all: render-scc render-pmch render-pdf render-pdf-light render-combined render-directions

# Alias so the meta repo's `render-bowel-prep` (which calls `$(MAKE) -C
# $(BP_SKILL) render`) finds a target. Keeps the per-skill makefiles
# uniform — every skill exposes `render` as its deploy-ready render entry.
render: render-all

# Cleanup target: ONLY removes the PDF review folder. Never touches the SCC/PMCH
# handout folders, which may contain hand-exported PDFs you don't want to lose.
clean-pdf-review:
	rm -rf "$(REVIEW_OUT)"
