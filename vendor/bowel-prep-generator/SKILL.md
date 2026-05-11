---
name: bowel-prep-generator
description: Generate pediatric colonoscopy bowel-prep patient handouts (mobile HTML and Word DOCX, English and Spanish) from a single dosing table. Use this skill whenever Sebastian mentions updating, regenerating, or adding bowel prep instructions, colonoscopy prep handouts, MiraLAX or Dulcolax dosing, weight-band handouts, or Surgery Center of Carmel prep materials. Trigger on phrases like "regenerate the bowel prep", "update the 31-40 kg dose", "add a new weight band", "translate prep handouts", "the under-15 kg handout", "the standard protocol", or any mention of adjusting Dulcolax tablets, MiraLAX capfuls, Gatorade volumes, or pre-cleanout dosing on the pediatric GI handouts. Also trigger when the user uploads any of the existing bowel-prep mobile HTML or SCC DOCX files.
---

# Bowel Prep Handout Generator

This skill produces pediatric colonoscopy bowel-prep patient handouts for Sebastian's practice (Pediatric Gastroenterology at Surgery Center of Carmel). It renders four files per weight band — English mobile HTML, Spanish mobile HTML, English print DOCX, Spanish print DOCX — from a single structured dosing table, so dose changes propagate consistently to every artifact.

## Why this exists

These handouts were previously maintained as eight hand-edited files per weight band. When a dose needed adjustment, eight places had to be updated in sync, across two languages and two formats, with no guarantee the numbers matched. This skill treats `data/dosing.yaml` as the single source of truth: edit the numbers there, run one command, and every handout is regenerated with matching content.

## Layout

```
bowel-prep-generator/
├── SKILL.md                    # this file
├── data/
│   └── dosing.yaml             # single source of truth — edit dose numbers here
├── templates/
│   ├── standard.en.html        # standard-protocol mobile HTML, English
│   ├── standard.es.html        # standard-protocol mobile HTML, Spanish
│   ├── standard.en.docx        # standard-protocol print DOCX, English
│   ├── standard.es.docx        # standard-protocol print DOCX, Spanish
│   ├── infant.en.html          # infant-protocol mobile HTML, English
│   ├── infant.es.html          # infant-protocol mobile HTML, Spanish
│   ├── infant.en.docx          # infant-protocol print DOCX, English
│   └── infant.es.docx          # infant-protocol print DOCX, Spanish
└── scripts/
    └── render.py               # reads dosing.yaml, fills templates, writes outputs
```

## The two protocols

Every weight band in `dosing.yaml` has a `protocol` field:

- **`infant`** — For babies who cannot take MiraLAX/Dulcolax. The handout says staff will administer a saline enema on arrival, provides infant fasting times, a dehydration warning, and clear-liquid guidance. No oral dosing is shown. Currently used for the `under-15` band.
- **`standard`** — For children who can take the oral prep. The handout includes shopping list, 4:00 PM Dulcolax, 5:00 PM MiraLAX/Gatorade, and a pre-cleanout callout. Currently used for all bands from `15-20` up through `over-50`.

The two protocols use separate templates; the render script picks the right one based on the band's `protocol` field.

## How to run it

Always use the skill's pinned venv — system python 3.9 on this Mac does not have `pyyaml` or `python-docx`. The venv lives at `.venv/` inside this skill directory.

```bash
# From the skill directory (~/.claude/skills/bowel-prep-generator/):

# Regenerate every band, both languages, both formats
.venv/bin/python scripts/render.py --out <output_directory>

# Regenerate just one band
.venv/bin/python scripts/render.py --out <output_directory> --band 31-40

# Only the English HTMLs
.venv/bin/python scripts/render.py --out <output_directory> --lang en --format html
```

If the venv is ever missing or corrupted, recreate it with:
```bash
python3 -m venv .venv && .venv/bin/pip install pyyaml python-docx
```

Outputs follow the established naming convention:
- `bowel-prep-{stem}-mobile.html`          (English HTML)
- `bowel-prep-{stem}-mobile-es.html`       (Spanish HTML)
- `bowel-prep-{stem}-SCC.docx`             (English DOCX)
- `bowel-prep-{stem}-SCC-es.docx`          (Spanish DOCX)

where `{stem}` comes from the `filename_stem` field on each band (e.g. `under-15kg`, `15-20kg`, `over-50kg`).

## Editing dosing.yaml

Each standard-protocol band has three kinds of fields:

1. **Structured numeric dosing** — `dulcolax_tablets`, `dulcolax_mg_total`, `miralax_capfuls`, `miralax_grams`, `gatorade_oz`, `gatorade_liters`. These feed into rendered phrases via language-aware format strings in `render.py`. This is what you change when a dose is being adjusted.
2. **Localized label strings** — `label_en`, `label_es`, `html_title_en`, `html_title_es`, `docx_heading_en`, `docx_heading_es`. These are stored as full strings because the wording varies (e.g. "Over 50 kg" vs "Mayores de 50 kg" vs "Children 31-40 kg"), and computing them would be brittle.
3. **Pre-cleanout sentences** — `precleanout_en`, `precleanout_es`. These are stored as full localized strings because the phrasing varies between bands ("1–2 capfuls (15–30 g)..." for small bands vs "4 capfuls (~60–68 g)..." with tilde for the 41-50 band).

For the infant protocol, only the label/title/warning-weight fields apply; there are no dose numbers.

## Common tasks

### "Update the MiraLAX dose for 31-40 kg to 8 capfuls"

1. Open `data/dosing.yaml`.
2. Find the band with `id: "31-40"`.
3. Change `miralax_capfuls` to `8` and `miralax_grams` to the corresponding grams (roughly 17 g per capful × 8 ≈ 136 g — confirm with Sebastian).
4. Run `python scripts/render.py --out <output_directory> --band 31-40`.
5. Return the four generated files via `computer://` links.

### "Add a new weight band"

1. Open `data/dosing.yaml`.
2. Copy an existing band block that's closest to the new one.
3. Update `id`, `filename_stem`, all six label/title/heading fields (English + Spanish), the dosing numbers, and the pre-cleanout sentences.
4. Decide which protocol applies (`infant` or `standard`).
5. Run `python scripts/render.py --out <output_directory> --band <new-id>`.

### "Safety pass on the doses"

The dosing in these handouts has already been checked against OpenEvidence by Sebastian. When asked for a safety pass, read `data/dosing.yaml` and look for internal inconsistencies. Report findings and **never silently edit**; always flag and ask before changing numbers.

**Clinical invariants to check against** (these are Sebastian's practice standards, not universal guidelines):

- **MiraLAX concentration: ~1 capful per 4 oz of Gatorade.** This is the preferred mixing ratio. Concretely: 5 capfuls → 20 oz, 7 capfuls → 28 oz, 10 capfuls → 40 oz, 12 capfuls → 48 oz. The only intentional exception is the top band (14 capfuls), which uses the standard full bottle of Gatorade at 64 oz because that's the largest convenient bottle size. If you see a band where `gatorade_oz != miralax_capfuls * 4` and it's not the 14-capful band, flag it.
- **MiraLAX grams ≈ capfuls × 17 g.** A capful is ~17 g; values should round cleanly. Large deviations indicate a transcription error.
- **Dulcolax mg = tablets × 5.** Each Dulcolax tablet is 5 mg.
- **Monotonic dose progression by weight.** Bigger kids get more Dulcolax, more MiraLAX, and (per the 4:1 rule) more Gatorade. A plateau or reversal between adjacent bands is a red flag unless clinically intentional.

### "Add a new language"

1. Create `templates/standard.<lang>.html`, `templates/standard.<lang>.docx`, `templates/infant.<lang>.html`, `templates/infant.<lang>.docx` by translating the existing templates (preserving the `{{PLACEHOLDER}}` tokens exactly).
2. Add `label_<lang>`, `html_title_<lang>`, `docx_heading_<lang>`, `precleanout_<lang>`, `miralax_note_<lang>`, and `warning_weight_<lang>` fields to every band in `dosing.yaml`.
3. Extend `build_strings()` and `build_infant_strings()` in `scripts/render.py` with a new language branch containing the phrasing rules (e.g. pluralization: "tablet" vs "tablets" vs "tableta" vs "tabletas").
4. Update the `--lang` argparse choices and the default `both` behaviour to include the new language.

## Placeholder reference

Standard-protocol HTML templates expect these tokens:

| Token | Example value | Where it's used |
|---|---|---|
| `{{HTML_TITLE}}` | `Colonoscopy Bowel Preparation Instructions - Children 31-40 kg` | `<title>` tag |
| `{{BAND_LABEL}}` | `31-40 kg (68-88 lb)` | Sticky header + strong under H1 |
| `{{HTML_DULCOLAX_SHORT}}` | `2 tablets (10 mg)` | Shopping list + 4:00 PM line |
| `{{HTML_MIRALAX_SHORT}}` | `10 capfuls (~170 g)` | Shopping list + 5:00 PM line |
| `{{HTML_GATORADE_VOL}}` | `64 oz (1.9 L)` | Shopping list + 5:00 PM line |
| `{{HTML_PRECLEANOUT}}` | `3–4 capfuls (47–60 g) mixed into 24–32 oz of juice, water, or Gatorade` | Pre-cleanout callout |

Standard-protocol DOCX templates expect:

| Token | Example value |
|---|---|
| `{{DOCX_HEADING}}` | `Children 31-40 kg (68-88 lb)` |
| `{{DOCX_DULCOLAX_LONG}}` | `2 Dulcolax 5 mg tablets (10 mg total)` |
| `{{DOCX_MIRALAX_SHOPPING}}` | `10 capfuls (~170 g) of MiraLAX mixed into 64 oz (1.9 L) of clear Gatorade (no red or purple)` |
| `{{DOCX_MIRALAX_5PM}}` | `10 capfuls (~170 g) of MiraLAX in 64 oz (1.9 L) of Gatorade` |
| `{{DOCX_PRECLEANOUT}}` | `3–4 capfuls (47–60 g) mixed into 24–32 oz of juice, water, or Gatorade` |

Infant-protocol templates use only `{{HTML_TITLE}}`, `{{BAND_LABEL}}`, `{{WARNING_WEIGHT}}`, and `{{DOCX_HEADING}}`, because the infant handout contains no oral dosing — only the weight thresholds need to move when the band changes.

## Invariants maintained by the templates

These details live in the template files, not in dosing data, so they stay consistent across every generated handout:

- **Branding**: Peyton Manning Children's Hospital logo + Children's Surgery Verification logo embedded as base64 in HTML; preserved as `word/media/*.png` in DOCX.
- **QR codes**: DOCX includes QR codes for the prep video and the patient portal (preserved through zip-level file passthrough — `render.py` only touches `word/document.xml`).
- **Collapsible sections open by default**: All `<details>` elements in the HTML templates are `<details open>` to prevent Google Drive's preview from rendering a thumbnail where the sections look permanently collapsed.
- **Emoji font isolation**: The DOCX templates put emoji characters (📅, 🎥, ⚠, ✅) in their own runs with `Apple Color Emoji` so they render as actual glyphs on macOS Word and Pages (Sebastian's environment). The surrounding text stays in Arial. On Windows, Word falls back to Segoe UI Emoji automatically. If you ever hand-edit a template and introduce new emojis, run `python scripts/fix_emojis.py <path-to-docx>` to split them out (the script is idempotent, so running it on already-fixed files is safe).
- **Output folder layout**: By default, `render.py` writes into a nested structure — `<out>/English/<band folder>/...` and `<out>/Spanish/<band folder>/...` — matching the organization Sebastian uses in Google Drive. Folder names come from `folder_en`/`folder_es` in `dosing.yaml`. Pass `--flat` to dump everything into a single directory instead (useful for quick previews and eval runs).
- **Office phone, surgery center address, patient portal URL, GIKids resource link**: these are static and deliberately not parameterized; update them in the template files if they ever change.

## Things NOT to do

- Don't try to regenerate the DOCX from the HTML — they have genuinely different layouts (the DOCX is a printable letterhead-style document with QR codes; the HTML is a mobile-web layout with collapsible sections). They are kept in sync at the **content** level via `dosing.yaml`, not at the format level.
- Don't silently change dosing numbers. The numbers have been vetted; if a safety pass surfaces a concern, report it and let Sebastian decide.
- Don't modify `word/document.xml` directly in generated DOCX files — edit the template and rerun the script, or (if a structural change is needed) edit the `.docx` template in Word, save it back into `templates/`, and re-run.
- Don't break the `<details open>` convention in HTML templates. If a new collapsible section is added, it must also be `open` by default.

## Future extensions anticipated

- **Additional languages** beyond English/Spanish — follow the "Add a new language" task above.
- **EGD (upper endoscopy) prep handouts** — likely a new protocol type with its own templates and a different subset of dosing fields. Add a new `protocol: egd` value and a parallel set of template files; extend `render.py`'s dispatch on `protocol`.
- **More weight bands** — just add them to `dosing.yaml`.
