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
SCC_OUT        := $(HOME)/Desktop/peds-gi-system/bowel-prep-handouts
PMCH_OUT       := $(HOME)/Desktop/peds-gi-system/bowel-prep-handouts-pmch
REVIEW_OUT     := $(HOME)/Desktop/peds-gi-system/bowel-prep-pdf-review
CHEATSHEET_OUT := $(HOME)/Desktop/peds-gi-system/doses-giready
STAFF_OUT      := $(HOME)/Desktop/peds-gi-system/staff

.PHONY: help install render-scc render-pmch render-pdf render-pdf-light \
        render-pdf-calm render-combined render-combined-calm render-directions render-cheatsheet \
        sites \
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
	@echo "  render-cheatsheet  Render doses.giready.com index.html + staff cheat-sheet PDF (copies to staff/)"
	@echo "  sites              Build all 16 mobile-site repos via manifest-driven build_websites.py"
	@echo "  render-all         Everything: DOCX + HTML for both locations + all PDF variants"
	@echo "  clean-pdf-review   Remove only the PDF review folder (does NOT touch SCC/PMCH dirs)"

install:
	@if [ ! -d .venv ]; then python3.12 -m venv .venv 2>/dev/null || { echo "ERROR: python3.12 required — 'brew install python@3.12'. WeasyPrint segfaults on Python 3.14."; exit 1; }; fi
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

# Calm visual language — colonoscopy family (standard + infant bands). This is
# the design the website download PDFs now ship: build_websites.py reads
# {LOC}-calm-color by default (BOWEL_PREP_PDF_THEME=calm). In render-all
# so `make sites` always has fresh Calm PDFs to copy in.
render-pdf-calm:
	$(RENDER) --out "$(REVIEW_OUT)/SCC-calm-color"  --format pdf-print --location scc  --theme calm
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-calm-color" --format pdf-print --location pmch --theme calm

# Calm visual language — combined EGD+colonoscopy family. Feeds the
# egdcolon / egdcolon86 download PDFs ({LOC}-combined-calm-color).
render-combined-calm:
	$(RENDER) --out "$(REVIEW_OUT)/SCC-combined-calm-color"  --format pdf-print --location scc  --theme calm --variant combined
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-combined-calm-color" --format pdf-print --location pmch --theme calm --variant combined

render-combined:
	$(RENDER) --out "$(REVIEW_OUT)/SCC-combined-color"  --format pdf-print --location scc  --theme color --variant combined
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-combined-color" --format pdf-print --location pmch --theme color --variant combined

# Static SUPREP PDFs for the patient handout binder. The dedicated
# suprep-standard-print.{en,es}.html templates carry the letter-paper
# layout (copied from the scheduler backend, de-personalized).
render-pdf-suprep:
	$(RENDER) --out "$(REVIEW_OUT)/SCC-suprep-color"  --format pdf-print --location scc  --theme color --band suprep
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-suprep-color" --format pdf-print --location pmch --theme color --band suprep

# Static lactulose PDFs for the patient handout binder. Three bands —
# under-15-lact (infant protocol, daily dose), 15-20-lact and 21-30-lact
# (standard protocol, big-prep evening + Dulcolax). All written into the
# same per-location review folder so consolidate picks them up together.
render-pdf-lactulose:
	$(RENDER) --out "$(REVIEW_OUT)/SCC-lactulose-color"  --format pdf-print --location scc  --theme color --band under-15-lact
	$(RENDER) --out "$(REVIEW_OUT)/SCC-lactulose-color"  --format pdf-print --location scc  --theme color --band 15-20-lact
	$(RENDER) --out "$(REVIEW_OUT)/SCC-lactulose-color"  --format pdf-print --location scc  --theme color --band 21-30-lact
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-lactulose-color" --format pdf-print --location pmch --theme color --band under-15-lact
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-lactulose-color" --format pdf-print --location pmch --theme color --band 15-20-lact
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-lactulose-color" --format pdf-print --location pmch --theme color --band 21-30-lact

# Static CLENPIQ PDFs for the patient handout binder. Single unified band
# (eligible 31 kg and up — Rx required, FDA age 9+). Mirrors the SUPREP
# Makefile target shape (one band per location).
render-pdf-clenpiq:
	$(RENDER) --out "$(REVIEW_OUT)/SCC-clenpiq-color"  --format pdf-print --location scc  --theme color --band clenpiq
	$(RENDER) --out "$(REVIEW_OUT)/PMCH-clenpiq-color" --format pdf-print --location pmch --theme color --band clenpiq

render-directions:
	$(PYTHON) scripts/render_directions.py --location all

# Internal staff cheat-sheet. Renders templates/cheatsheet.html in two passes
# (screen → doses.giready.com/index.html ; print → bowel-prep-cheatsheet.pdf)
# and also drops the PDF into staff/ for Google Drive upload. Replaces the
# legacy doses-giready/scripts/build.py — the cheat-sheet now reads
# dosing.yaml directly so its numbers can't drift from the patient handouts.
render-cheatsheet:
	$(RENDER) --format cheatsheet --out "$(CHEATSHEET_OUT)"
	@if [ -d "$(STAFF_OUT)" ]; then \
		cp "$(CHEATSHEET_OUT)/bowel-prep-cheatsheet.pdf" "$(STAFF_OUT)/bowel-prep-cheatsheet.pdf"; \
		echo "  copied  $(CHEATSHEET_OUT)/bowel-prep-cheatsheet.pdf -> $(STAFF_OUT)/bowel-prep-cheatsheet.pdf"; \
	else \
		echo "  WARNING: $(STAFF_OUT) missing — skipped Drive-staging copy."; \
	fi

# One-time-ish: shrink the four map PNGs in templates/maps/ to JPEG so the
# rendered directions PDFs are scheduler-portal-friendly. Idempotent —
# always reads from *-original.png snapshots, so rerunning after a quality
# tweak is safe.
shrink-maps:
	$(PYTHON) scripts/shrink_map_images.py

sites:
	$(PYTHON) scripts/build_websites.py

render-all: render-scc render-pmch render-pdf render-pdf-light render-pdf-calm render-combined render-combined-calm render-pdf-suprep render-pdf-lactulose render-pdf-clenpiq render-directions render-cheatsheet

# Alias so the meta repo's `render-bowel-prep` (which calls `$(MAKE) -C
# $(BP_SKILL) render`) finds a target. Keeps the per-skill makefiles
# uniform — every skill exposes `render` as its deploy-ready render entry.
render: render-all

# Cleanup target: ONLY removes the PDF review folder. Never touches the SCC/PMCH
# handout folders, which may contain hand-exported PDFs you don't want to lose.
clean-pdf-review:
	rm -rf "$(REVIEW_OUT)"
