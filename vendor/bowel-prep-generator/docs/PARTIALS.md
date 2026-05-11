# Sections-as-Partials (Tier-1 architecture)

The skill's print templates duplicate the same section markup across 26 files
(standard / combined / infant / infant-enema × en / es × print/mobile).
A structural change (e.g. shopping-table redesign) used to require editing
all 26. The partials architecture extracts shared sections into single files
that every template includes by token substitution.

## Layout

```
templates/
  partials/
    _location_box.{en,es}.html
    _shopping_table.{en,es}.html
    _medications_note.{en,es}.html
    _precleanout_callout.{en,es}.html
    _day_of_procedure.{en,es}.html
    _helpful_resources.{en,es}.html
    _sample_meals.{en,es}.html
  standard-print.{en,es}.html        # uses {{PARTIAL_*}} tokens
  ...all other top-level templates    # still inline (Tier-1 POC)
```

## Naming convention

- File: `_<name>.<lang>.html` (the leading underscore marks it as a partial,
  mirroring SCSS / Jinja conventions).
- Token: `{{PARTIAL_<NAME>}}` (uppercase, no leading underscore). The renderer
  derives the token from the filename automatically — no manual registration.

## Substitution flow

`render_pdf_print()` calls `_load_partials(lang)` which globs
`templates/partials/_*.<lang>.html` once per language (cached) and returns a
dict `{ "{{PARTIAL_X}}": "<file body>", ... }`. That dict is merged into the
substitution map **first**, before per-band, QR, and practice replacements.
Per-band tokens that live inside a partial body (e.g. `{{HTML_PRECLEANOUT}}`
inside `_precleanout_callout.en.html`) are still substituted by the regular
pass.

## Adding a new partial

1. Create `templates/partials/_<name>.<lang>.html` (both en & es).
2. Replace the corresponding section in each top-level template with
   `{{PARTIAL_<NAME>}}`.
3. Run the smoke test: render the same PDF before and after; the HTML output
   must be byte-identical (`diff` to confirm).

## Migration plan for the other 24 templates

This branch only converts `standard-print.{en,es}.html`. Remaining templates:

- `combined-print.{en,es}.html` — uses 6 of 7 partials verbatim (day-of
  procedure differs to add EGD bullets); easy migration.
- `infant-print.{en,es}.html`, `infant-enema-print.{en,es}.html` — share the
  location box, helpful resources, and (likely) sample meals; shopping table
  and day-of differ. Expect 3–4 partials reusable.
- `combined-infant-print.{en,es}.html`, `combined-infant-enema-print.{en,es}.html`
  — same as infant variants.
- 16 mobile templates — different layout (single-column, no print CSS) but
  the location-box, helpful-resources, and sample-meals sections may still
  be reusable. Run `scripts/migrate_template_to_partials.py <file>` first.

The helper `scripts/migrate_template_to_partials.py <template>` reports
which partials currently appear inline in a candidate template (exact-match
substring search). Use it to scope each migration before editing.
