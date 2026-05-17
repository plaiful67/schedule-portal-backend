# Bowel-Prep Handout Generator

Generates the full set of pediatric (or adult) **colonoscopy** and **combined
EGD + colonoscopy** bowel-prep handouts — print-ready PDF, mobile-friendly
HTML, and editable DOCX — from a single source of clinical truth.
English + Spanish, multiple weight bands, multiple procedure locations,
location-aware QR codes, all from one render command.

It also builds the four mobile-site repos (`prep`, `prep86`, `egdcolon`,
`egdcolon86` for `giready.com`) and renders the standalone driving-directions
PDFs for each procedure location.

## Layout

```
bowel-prep-generator/
  data/
    dosing.yaml                     Clinical protocol: bands, dosing, locations
  practice.yaml                     Practice branding, contact, QR target URLs
  templates/
    {protocol}.{lang}.{html,docx}   Mobile + DOCX templates
    {protocol}-print.{lang}.html    Colonoscopy print PDFs
    combined-print.{lang}.html      Combined EGD + colonoscopy print PDFs
    combined-mobile.{lang}.html     Combined mobile per-band pages
    combined-mobile-landing.{lang}.html  Combined mobile landing
    {scc,pmch}-directions-print.html     Standalone directions PDFs
    maps/                                Saved map images for the directions PDFs
    logo-pmch.png                        Practice logo
  scripts/
    render.py                       Main renderer (DOCX + HTML + PDF, both variants)
    render_directions.py            Standalone directions-PDF renderer
    build_colonoscopy_websites.py   Builds prep / prep86 site repos
    build_combined_websites.py      Builds egdcolon / egdcolon86 site repos
  Makefile                          Common workflows (install, render, sites)
  requirements.txt
```

## First-time setup (any machine)

```bash
brew install pango              # macOS — WeasyPrint dep
make install                    # creates .venv, installs Python deps
make render-all                 # smoke test — produces every PDF variant
```

## Day-to-day editing

| To change... | Edit |
|---|---|
| Dosing numbers, weight bands, timing | `data/dosing.yaml` |
| Procedure-location address / phone / arrival / Maps URL | `data/dosing.yaml` (`locations:` block) |
| Practice name, phone, logo, footer | `practice.yaml` |
| QR codes (YouTube video, patient portal) | `practice.yaml` (`qr_targets:` block) |
| Colonoscopy print PDF visual design | `templates/*-print.{en,es}.html` |
| Combined EGD+colonoscopy print design | `templates/combined-print.{en,es}.html` |
| Mobile per-band design | `templates/{protocol}.{en,es}.html` and `combined-mobile.*` |
| Mobile landing-page design | `templates/combined-mobile-landing.*` |
| DOCX layout | `templates/{protocol}.{en,es}.docx` |
| Directions PDF | `templates/{scc,pmch}-directions-print.html` + `templates/maps/` |

## Common make targets

```bash
make render-scc          # DOCX + mobile HTML for SCC
make render-pmch         # DOCX + mobile HTML for PMCH
make render-pdf          # Colonoscopy print PDFs (color)
make render-pdf-light    # Colonoscopy print PDFs (toner-friendly)
make render-combined     # Combined EGD + colonoscopy print PDFs (5 standard bands)
make render-directions   # SCC + PMCH directions PDFs to ~/Desktop/
make sites               # Build all 4 mobile-site repos
make render-all          # Everything: DOCX + HTML + all PDF variants + directions
```

Direct CLI (more control):

```bash
.venv/bin/python scripts/render.py \
    --out ~/Desktop/test --location pmch --band 21-30 \
    --lang en --format pdf-print --theme print-light

.venv/bin/python scripts/render.py \
    --out ~/Desktop/test-combined --location scc \
    --format pdf-print --variant combined

.venv/bin/python scripts/render_directions.py --location pmch
```

## Output structure

```
bowel-prep-handouts/                         # SCC: DOCX + mobile HTML
bowel-prep-handouts-pmch/                    # PMCH: DOCX + mobile HTML
bowel-prep-pdf-review/
  SCC-color/, SCC-print-light/               # Colonoscopy print PDFs
  PMCH-color/, PMCH-print-light/
  SCC-combined-color/, PMCH-combined-color/  # Combined EGD + colonoscopy
~/Desktop/scc-directions.pdf
~/Desktop/pmch-directions.pdf
```

Each review folder is split by `English/` and `Spanish/`, then by weight band.

The `make sites` target writes directly into the four website repos at
`~/Desktop/{prep,prep86,egdcolon,egdcolon86}-giready/`. Cloudflare Pages
auto-deploys when those repos are pushed.

> **Don't `rm -rf` the SCC/PMCH handout folders.** Hand-exported PDFs may
> live there alongside the DOCX files. The Makefile deliberately exposes
> only `clean-pdf-review` (review folder only) and never offers a destructive
> "clean everything" target.

## Adapting for a different practice

To use this for a different GI practice (peds or adult):

1. Replace `templates/logo-pmch.png` with your practice's logo (PNG, ~400×120 px).
2. Edit `practice.yaml` — `cover_stack_*`, `footer_*`, `logo_alt`, `qr_targets.*`.
3. Edit `data/dosing.yaml` — `locations:` block and `bands:` array.
4. Update `templates/maps/` with map screenshots for your locations and edit
   `templates/{loc}-directions-print.html` accordingly.
5. Run `make render-all`.

The template layout, page-break behavior, and bilingual rendering all stay
the same — same machinery, driven by your YAML.

## Migrating to a new machine

```bash
git clone https://github.com/plaiful67/bowel-prep-generator-skill \
    ~/.claude/skills/bowel-prep-generator
cd ~/.claude/skills/bowel-prep-generator
brew install pango
make install
make render-all
```

That's it. (Or use the top-level `peds-gi-prep-system` meta repo's
`scripts/setup_new_machine.sh` to clone all 9 repos at once.)

## Validation

`scripts/validate.py` runs a full lint + render-check across every
band × language × variant combination. Use it before committing
significant template or dosing changes:

```bash
.venv/bin/python scripts/validate.py            # full suite (~2-3 min)
.venv/bin/python scripts/validate.py --quick    # lint only (<1 sec)
```

Optional pre-commit hook (runs `--quick` automatically on relevant
file changes):

```bash
bash scripts/install_pre_commit.sh
```

See [docs/VALIDATION.md](docs/VALIDATION.md) for what gets checked
and when to skip the hook.
