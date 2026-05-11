# EGD Handout Generator

Generates print-ready PDF handouts for **upper endoscopy (EGD)** procedures, in
English and Spanish, for both procedure locations (Surgery Center of Carmel and
Peyton Manning Children's Hospital / 86th St). Built on the same design system
as the bowel-prep handout generator.

## Layout

```
egd-handout-generator/
  data/procedure.yaml     # NPO timing, locations, mobile site
  practice.yaml           # Practice branding, contact, QR target URLs
  templates/
    egd-print.en.html     # English print template
    egd-print.es.html     # Spanish print template
    logo-pmch.png         # Practice logo
  scripts/render.py       # The renderer (PDF only)
  Makefile
  requirements.txt
```

## First-time setup

```bash
brew install pango        # macOS — WeasyPrint dep
make install              # creates .venv, installs Python deps
make render               # smoke test — produces 8 PDFs into ~/Desktop/egd-pdf-review/
```

## Day-to-day editing

| To change... | Edit |
|---|---|
| NPO timing (hours-stop) | `data/procedure.yaml` (`npo:` block) |
| Procedure description / duration | `data/procedure.yaml` (`procedures.egd`) |
| Procedure location address / phone / arrival time | `data/procedure.yaml` (`locations:` block) |
| Practice name, phone, logo, footer | `practice.yaml` |
| QR codes (YouTube video, patient portal) | `practice.yaml` (`qr_targets:`) |
| Visual design / typography | `templates/egd-print.{en,es}.html` |

After edits: `make render`.

## Output structure

```
~/Desktop/egd-pdf-review/
  SCC/
    English/  egd-SCC-print.pdf  egd-SCC-print-print-light.pdf
    Spanish/  egd-SCC-es-print.pdf  egd-SCC-es-print-print-light.pdf
  PMCH/
    English/  egd-PMCH-print.pdf  egd-PMCH-print-print-light.pdf
    Spanish/  egd-PMCH-es-print.pdf  egd-PMCH-es-print-print-light.pdf
```

Color theme = full visual hierarchy (navy step bands). Print-light = thin borders
+ white fills for toner-friendly printing.

## Future procedures

The `procedures:` block in `data/procedure.yaml` is keyed by procedure id.
Adding flexible sigmoidoscopy, capsule endoscopy, or any other procedure later
means:

1. Add a new entry under `procedures:` with the same shape (label, NPO timing,
   variants).
2. Author `templates/{procedure_id}-print.{en,es}.html` (start by copying
   `egd-print.en.html` and trimming).
3. Pass `--procedure {id}` to `render.py`.

A future refactor could unify this with the bowel-prep skill into a single
"procedure-handout-generator." For now, separate skills are simpler to evolve.

## Mobile sites

This skill also publishes static content to the EGD-only mobile site repos:

- `~/Desktop/egd-giready/`   →  egd.giready.com (SCC)
- `~/Desktop/egd86-giready/` →  egd86.giready.com (PMCH)

`scripts/build_websites.py` writes EGD-specific landing pages into those repos.
QR codes printed on the PDFs link to those subdomains.

## Migration to a new machine

```bash
git clone <your-repo> ~/.claude/skills/egd-handout-generator
cd ~/.claude/skills/egd-handout-generator
brew install pango
make install
make render
```

(Or use the top-level `peds-gi-prep-system` meta repo's
`scripts/setup_new_machine.sh` to clone all 9 repos at once.)
