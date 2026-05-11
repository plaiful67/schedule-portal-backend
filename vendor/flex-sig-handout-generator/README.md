# Flex Sig Handout Generator

Generates print-ready PDF handouts for **flexible sigmoidoscopy** bowel prep, in
English and Spanish, for both procedure locations (Surgery Center of Carmel and
Peyton Manning Children's Hospital / 86th St). Built on the same design system
as the bowel-prep and EGD handout generators.

Three weight bands (saline-enema-only protocol):

- `under-15kg` — Under 15 kg / Under 33 lb (staff- or syringe-administered NS,
  ~5 mL/kg)
- `20-40kg` — 20–40 kg / 44–88 lb (one regular adult saline enema, ~240 mL)
- `over-40kg` — Over 40 kg / Over 88 lb (one large adult saline enema ~480 mL,
  or 2 × 240 mL)

Phosphate (phospho-soda) enemas are explicitly excluded across all bands.

## Layout

```
flex-sig-handout-generator/
  data/procedure.yaml       # Bands, enema dosing, locations, NPO
  practice.yaml             # Practice branding, contact, QR target URLs
  templates/
    flex-sig-print.en.html  # English print template (PDF source)
    flex-sig-print.es.html  # Spanish print template
    logo-pmch.png           # Practice logo
  scripts/render.py         # Renderer (PDF only — no DOCX, no mobile HTML)
  Makefile
  requirements.txt
```

## First-time setup

```bash
brew install pango        # macOS — WeasyPrint dep
make install              # creates .venv, installs Python deps
make render               # smoke test — produces 12 PDFs into ~/Desktop/flex-sig-pdf-review/
```

## Output

```
~/Desktop/flex-sig-pdf-review/
  SCC/
    English/  flex-sig-under-15kg-SCC-print.pdf
              flex-sig-20-40kg-SCC-print.pdf
              flex-sig-over-40kg-SCC-print.pdf
    Spanish/  flex-sig-under-15kg-SCC-es-print.pdf
              ...
  PMCH/
    English/  ...
    Spanish/  ...
```

## Day-to-day editing

| To change... | Edit |
|---|---|
| Enema volume / instructions for a band | `data/procedure.yaml` (`bands:` block) |
| NPO timing | `data/procedure.yaml` (`npo:` block) |
| Procedure location | `data/procedure.yaml` (`locations:` block) |
| Practice name, phone, logo, footer | `practice.yaml` |
| QR codes (YouTube video, patient portal) | `practice.yaml` (`qr_targets:`) |
| Visual design / typography | `templates/flex-sig-print.{en,es}.html` |

## Design notes

- **PDF-only.** No DOCX or mobile HTML output. (The bowel-prep skill keeps a
  working DOCX renderer if you need editable copies for any band — port it in.)
- **No flex-sig mobile site yet.** `mobile_subdomain` is intentionally empty in
  `procedure.yaml`; the cover-mobile QR is therefore omitted by render.py.
  When we publish a flex-sig mobile site, fill in `mobile_subdomain` and add a
  cover-QR block to the templates.
- **The under-15kg band uses a different content shape.** It has no "shopping
  list" (saline is staff-administered or doctor-prescribed by syringe). The
  template gates the shopping list on `simple_diet: false` and shows a special
  callout (`infant_callout: true`) instead. The diet section is also simpler
  for that band.

## CLI usage

```bash
.venv/bin/python scripts/render.py \
    --out ~/Desktop/flex-sig-pdf-review \
    --location pmch \
    --lang en \
    --band over-40kg
```

`--band all` (default), `--location all` (default), and `--lang both`
(default) iterate over every combination.

## Migration to a new machine

```bash
git clone <your-repo> ~/.claude/skills/flex-sig-handout-generator
cd ~/.claude/skills/flex-sig-handout-generator
brew install pango
make install
make render
```

(Or use the top-level `peds-gi-prep-system` meta repo's
`scripts/setup_new_machine.sh` to clone all 9 repos at once.)
