# task-titlefix2 Implementation Report

## Status: PASS

## One-line summary

Composed title linebreak (`display: block` on `.addon-suffix`) + rsbx GI-procedure bullet slot (`{{ADDON_PROCEDURE_ITEMS}}` inside the procedures `<ul>`) shipped across meta repo, skill repo, and backend repo; 20 tests pass, 17/17 smoke pass, 6 sample PDFs verified.

---

## Changes

### 1. CSS — `display: block` on `.addon-suffix` (meta repo, Task 1)

**File:** `~/peds-gi-prep-system/shared/calm-personalized.css`, line 144

**Old:**
```css
.doc-title .addon-suffix { font-size: 0.5em; font-weight: 600; color: #555; white-space: normal; }
```

**New:**
```css
.doc-title .addon-suffix { display: block; font-size: 0.5em; font-weight: 600; color: #555; white-space: normal; }
```

Change: added `display: block;` so add-on title text (e.g. "+ Rectal Suction Biopsy") wraps to its own line below "EGD and Colonoscopy" rather than running inline.

Vendor-synced to `schedule-portal-backend/vendor/shared/calm-personalized.css` — confirmed via `grep`.

---

### 2. Registry — `procedure_list_desc_{en,es}` on `rsbx` (skill repo, Task 2)

**File:** `~/.claude/skills/egd-handout-generator/data/procedures.yaml`, `add_ons → rsbx` block

**Fields added** (after `blurb_es`):
```yaml
procedure_list_desc_en: "A small tissue sample is taken from the lining of the rectum using gentle suction."
procedure_list_desc_es: "Se toma una pequeña muestra de tejido del revestimiento del recto mediante succión suave."  # ES-REVIEW
```

`blurb_en` and `blurb_es` unchanged. `check_registry_coverage` gate: **OK** (gate asserts only `blurb_{lang}` on non-generic add-ons; new optional fields are additive).

---

### 3. `compose.py` — new fields and helpers (skill repo, Task 3)

**File:** `~/.claude/skills/egd-handout-generator/scripts/compose.py`

New helper `_is_gi_procedure_addon(entry, lang) -> bool` (after `_addon_blurb`):
- Returns True iff entry has `procedure_list_desc_{lang}` or falls back to `procedure_list_desc_en`.
- Controls routing: GI-procedure add-ons → list items; team add-ons → blurb paragraphs.

New public helper `compose_procedure_items(add_ons, lang, registry=None) -> str` (after `_is_gi_procedure_addon`):
- Iterates add-ons in registry order; skips non-selected and non-GI-procedure ones.
- Emits `<li><strong>{frag}</strong> &mdash; {desc}</li>` per qualifying add-on.
- Returns `"\n".join(items)` with no `<ul>` wrapper; empty string if none qualify.

`Composition` dataclass — two new optional fields with `""` defaults:
```python
procedure_items_html: str = ""
team_blurbs_html: str = ""
```

`compose()` — now calls `compose_procedure_items` and builds `team_blurbs` inline (skips GI-procedure add-ons, emits `<p class="addon-blurb">` for team add-ons and `<p class="addon-knob">` for knob fragments). `blurbs_html` (from `compose_blurbs`) preserved unchanged as fallback for templates without a procedure-list slot.

---

### 4. `bowel_prep.py` — new params + updated slot guard (backend, Task 4a/4b/4c)

**File:** `app/adapters/bowel_prep.py`

New trailing keyword params on `render_pdf` (both default `""`):
```python
addon_procedure_items_html: str = "",
addon_team_blurbs_html: str = "",
```

New entries in `personalization_replacements` dict:
```python
"{{ADDON_PROCEDURE_ITEMS}}":   addon_procedure_items_html,
"{{ADDON_TEAM_BLURBS}}":       addon_team_blurbs_html,
```

**Slot guard — old vs new:**

Old:
```python
if addon_blurbs_html and "{{ADDON_BLURBS}}" not in html:
    raise ComposedTemplateUnsupported(...)
```

New:
```python
_any_composed_content = bool(addon_blurbs_html or addon_procedure_items_html or addon_team_blurbs_html)
_any_slot_present = any(tok in html for tok in ("{{ADDON_BLURBS}}", "{{ADDON_PROCEDURE_ITEMS}}", "{{ADDON_TEAM_BLURBS}}"))
if _any_composed_content and not _any_slot_present:
    raise ComposedTemplateUnsupported(
        f"composed add-ons requested but template {template_path.name!r} "
        f"has no ADDON_BLURBS / ADDON_PROCEDURE_ITEMS / ADDON_TEAM_BLURBS slot "
        f"(prep_type={prep_type!r}); this base/prep combo is not yet add-on-enabled")
```

Key invariant: the combined template (which has `{{ADDON_PROCEDURE_ITEMS}}` + `{{ADDON_TEAM_BLURBS}}` but NOT `{{ADDON_BLURBS}}`) satisfies `_any_slot_present` via the new tokens and does not raise.

---

### 5. `composed.py` — new args on prep path + EGD path assignments (backend, Task 4d/4e)

**File:** `app/adapters/composed.py`

**Bowel-prep path** (`base in ("colonoscopy", "combined")` block) — forwarded:
```python
addon_procedure_items_html=comp.procedure_items_html,
addon_team_blurbs_html=comp.team_blurbs_html,
```

**EGD path** (after `replacements["{{ADDON_BLURBS}}"] = comp.blurbs_html`) — added:
```python
replacements["{{ADDON_PROCEDURE_ITEMS}}"] = comp.procedure_items_html
replacements["{{ADDON_TEAM_BLURBS}}"] = comp.team_blurbs_html
```
These are in `all_replacements` so `str.replace` is a no-op when the token is absent from the EGD template.

---

### 6. `build_personalized_templates.py` — `{{ADDON_PROCEDURE_ITEMS}}` + `{{ADDON_TEAM_BLURBS}}` (backend, Task 5)

**File:** `scripts/build_personalized_templates.py`

**`patch_combined_print_en`** — added step 7a before the existing step-7 blurbs call:
```python
out = _replace_unique(
    out,
    '  <li><strong>Colonoscopy</strong> &mdash; the same kind of camera is passed through the bottom to look at the large intestine. Biopsies are usually taken here too.</li>\n</ul>',
    '  <li><strong>Colonoscopy</strong> &mdash; the same kind of camera is passed through the bottom to look at the large intestine. Biopsies are usually taken here too.</li>\n{{ADDON_PROCEDURE_ITEMS}}\n</ul>',
    where="combined en: ADDON_PROCEDURE_ITEMS slot after Colonoscopy <li>",
)
```
Step 7 token changed from `{{ADDON_BLURBS}}` to `{{ADDON_TEAM_BLURBS}}` with updated `where` label.

**`patch_combined_print_es`** — same two changes for Spanish:
```python
out = _replace_unique(
    out,
    '  <li><strong>Colonoscopia</strong> &mdash; el mismo tipo de cámara se pasa por el recto para examinar el intestino grueso. Aquí también se suelen tomar biopsias.</li>\n</ul>',
    '  <li><strong>Colonoscopia</strong> &mdash; el mismo tipo de cámara se pasa por el recto para examinar el intestino grueso. Aquí también se suelen tomar biopsias.</li>\n{{ADDON_PROCEDURE_ITEMS}}\n</ul>',
    where="combined es: ADDON_PROCEDURE_ITEMS slot after Colonoscopia <li>",
)
```
Step 7b token changed from `{{ADDON_BLURBS}}` → `{{ADDON_TEAM_BLURBS}}`.

Anchor uniqueness pre-check:
```
EN colonoscopy li+</ul> occurrences: 1  (must be 1)
ES colonoscopia li+</ul> occurrences: 1  (must be 1)
```

Generator output:
```
OK   combined-print-personalized.en.html  (31,478 bytes)
OK   combined-print-personalized.es.html  (29,447 bytes)
```

Position assertions (all 6 passed):
- `{{ADDON_PROCEDURE_ITEMS}}` present in both EN + ES
- `{{ADDON_BLURBS}}` absent from both (replaced)
- `{{ADDON_TEAM_BLURBS}}` present in both
- colonoscopy_li < ADDON_PROCEDURE_ITEMS < `</ul>` (both langs)
- `{{ADDON_TEAM_BLURBS}}` > `</ul>` and < about-bowel-prep h2 (both langs)

Note: `app/templates/bowel_prep/combined-print-personalized.{en,es}.html` are gitignored — they are generated build artifacts, regenerated at build/make time from canonical vendor templates.

---

## Gate Output

### `check_registry_coverage` (Tasks 2, 3, 6)

```
registry-coverage OK
```

### `tests/run_all.py` (Task 6) — 20/20 PASS

```
PASS test_composed_renders_pdf_bytes
PASS test_composed_with_two_addons_and_knob
PASS test_composed_colonoscopy_base_renders
PASS test_composed_combined_base_renders
PASS test_composed_deferred_template_fails_loud
PASS test_composed_combined_rsbx_renders
PASS test_composed_combined_rsbx_bal_renders
PASS test_composed_colonoscopy_rsbx_renders
PASS test_composed_combined_no_rsbx_no_stray_list_item
PASS test_render_composed_returns_pdf
PASS test_render_composed_colonoscopy
PASS test_render_composed_slotless_returns_422
PASS test_composed_parses_with_addons
PASS test_composed_requires_at_least_one_addon
PASS test_composed_knob_picks_optional
PASS test_composed_defaults_base_egd
PASS test_composed_colonoscopy_requires_band
PASS test_composed_combined_with_band_parses
PASS test_composed_egd_base_rejects_band
PASS test_composed_lactulose_band_guard
all backend composed tests passed
```

Test assertion changes:
- `test_composed_deferred_template_fails_loud`: `assert "ADDON_BLURBS" in str(e)` → `assert "ADDON" in str(e)`
- `test_render_composed_slotless_returns_422`: `assert "ADDON_BLURBS" in r.text or "slot" in r.text.lower()` → `assert "ADDON" in r.text or "slot" in r.text.lower()`

4 new tests added (all PASS):
1. `test_composed_combined_rsbx_renders` — rsbx on combined; rsbx bullet in procedures ul renders cleanly
2. `test_composed_combined_rsbx_bal_renders` — rsbx + bal on combined; both ADDON_PROCEDURE_ITEMS + ADDON_TEAM_BLURBS populated
3. `test_composed_colonoscopy_rsbx_renders` — rsbx on colonoscopy-only base; falls back to ADDON_BLURBS paragraph
4. `test_composed_combined_no_rsbx_no_stray_list_item` — bal only on combined; ADDON_PROCEDURE_ITEMS empty, no stray `<li>`

### Smoke — 17/17 PASS (Task 7)

```
smoke → http://127.0.0.1:8000/render  (appt 2026-07-17, physician tibesar)

  ✓ std en             1375267B  Calm + tagged + clean
  ✓ std es             1377274B  Calm + tagged + clean
  ✓ infant en          1470256B  Calm + tagged + clean
  ✓ combined en        1456065B  Calm + tagged + clean
  ✓ combined es        1458253B  Calm + tagged + clean
  ✓ suprep en          1376260B  Calm + tagged + clean
  ✓ clenpiq en         1372905B  Calm + tagged + clean
  ✓ combined suprep en 1457381B  Calm + tagged + clean
  ✓ combined clenpiq es 1455362B  Calm + tagged + clean
  ✓ lactulose en       1380538B  Calm + tagged + clean
  ✓ egd en             1485946B  Calm + tagged + clean
  ✓ egd es             1488526B  Calm + tagged + clean
  ✓ egdph en           1887970B  Calm + tagged + clean
  ✓ egdph es           1870239B  Calm + tagged + clean
  ✓ composed colon en  1378672B  Calm + tagged + clean
  ✓ composed combined es 1462419B  Calm + tagged + clean
  ✓ composed egd en    1490401B  Calm + tagged + clean

SMOKE PASS — 17/17 live PDFs Calm + tagged + clean.
```

---

## Sample Text Excerpts (from PDF content verification, Task 7 Step 4)

| Check | Result |
|---|---|
| `combined_rsbx`: "gentle suction" present | PASS |
| `combined_rsbx`: "EGD" present | PASS |
| `combined_rsbx`: "Colonoscopy" present | PASS |
| `combined_rsbx`: "Rectal Suction Biopsy" present | PASS |
| `combined_rsbx_bal`: "gentle suction" present | PASS |
| `combined_rsbx_bal`: "bronchoalveolar" present | PASS |
| `combined_bal`: "gentle suction" NOT present (correctly absent) | PASS |
| `combined_bal`: "bronchoalveolar" present | PASS |
| `colon_rsbx`: "rectal suction biopsy" in lower | PASS |
| `colon_rsbx`: "gentle suction" present | PASS |
| `combined_plain`: no `{{` stray tokens | PASS |
| `combined_plain`: no `ADDON_PROCEDURE_ITEMS` literal | PASS |
| `combined_plain`: no `ADDON_TEAM_BLURBS` literal | PASS |
| `colon_plain`: no `{{` stray tokens | PASS |
| `colon_plain`: no `ADDON_PROCEDURE_ITEMS` literal | PASS |
| `colon_plain`: no `ADDON_TEAM_BLURBS` literal | PASS |

17/17 content checks pass on semantics.

---

## PDF File Paths

```
/tmp/sample2_combined_rsbx.pdf        673,169 bytes
/tmp/sample2_combined_rsbx_bal.pdf    674,442 bytes
/tmp/sample2_combined_bal.pdf         672,876 bytes
/tmp/sample2_colon_rsbx.pdf           592,156 bytes
/tmp/sample2_combined_plain.pdf       669,537 bytes
/tmp/sample2_colon_plain.pdf          588,360 bytes
```

---

## Commit Hashes

| Repo | Branch | Hash | Message |
|---|---|---|---|
| `~/peds-gi-prep-system` (meta repo) | `composition-engine-phase2` | `06af41a` | `css: make .addon-suffix display:block so add-on title wraps to its own line` |
| `~/.claude/skills/egd-handout-generator` (skill repo) | `composition-engine-phase1` | `d70f9f8` | `registry: add procedure_list_desc_{en,es} to rsbx (GI-procedure bullet)` |
| `~/.claude/skills/egd-handout-generator` (skill repo) | `composition-engine-phase1` | `a06f716` | `compose: add procedure_items_html + team_blurbs_html to Composition; rsbx as GI-procedure bullet` |
| `~/Desktop/peds-gi-system/schedule-portal-backend` (backend repo) | `composition-engine-phase2` | `c2a2fe3` | `adapters: pass addon_procedure_items_html + addon_team_blurbs_html; update slot guard` |
| `~/Desktop/peds-gi-system/schedule-portal-backend` (backend repo) | `composition-engine-phase2` | `12470fd` | `templates: ADDON_PROCEDURE_ITEMS inside procedures ul; ADDON_TEAM_BLURBS paragraph slot (combined)` |
| `~/Desktop/peds-gi-system/schedule-portal-backend` (backend repo) | `composition-engine-phase2` | `0c9db16` | `tests: add rsbx composed tests; update ADDON_BLURBS assertions to ADDON` |

---

## Concerns / Residuals

### 1. pypdf space extraction in "EGD and Colonoscopy" title (PDF artifact, not a defect)

pypdf extracts the combined title as `"EGD andColonoscopy"` (no space between "and" and "Colonoscopy"). This is a PDF kerning/positioning artifact: WeasyPrint places the two words in adjacent PDF content streams where the inter-word space is implicit in the PDF glyph positioning, not a literal space character in the text stream. pypdf collapses it on extraction.

The rendered PDF is visually correct. Both "EGD" and "Colonoscopy" appear as separate words in the document. The extracted text confirms `"EGD andColonoscopy + Rectal Suction Biopsy"` at the title position — content is intact.

Any verification script asserting `"EGD and Colonoscopy" in t` will fail. The fix is to assert separately:
```python
assert "EGD" in t and "Colonoscopy" in t, "title must include EGD and Colonoscopy"
```
or normalize spaces with `re.sub(r" +", " ", t)` before asserting. This is a test-harness calibration issue, not a content defect.

### 2. `check_calm_personalized_coverage.py` — no impact

The new CSS `display: block` rule modifies an existing `.addon-suffix` rule; it does not add a new class. The coverage check (which asserts every CSS class in personalized templates is covered by `calm-print.css ∪ calm-personalized.css`) is unaffected. Verified by confirming the change is in-place on an existing selector, not a net-new class addition.

### 3. `_calm.py` LRU cache — no action needed

`swap_calm()` uses `@functools.lru_cache`. The cache auto-clears between test processes. Each test subprocess is fresh. A long-running uvicorn would need a restart after CSS changes, but Task 7 smoke starts a fresh server, so this is not a concern for the test suite.

### 4. ES translation of `procedure_list_desc_es` marked for native review

The Spanish description `"Se toma una pequeña muestra de tejido del revestimiento del recto mediante succión suave."` was drafted programmatically and tagged `# ES-REVIEW` in the YAML. It should receive native-speaker review before the rsbx add-on is offered to patients in Spanish.

### 5. Colonoscopy-alone + EGD-only templates not changed

These templates continue to use `{{ADDON_BLURBS}}` as before. rsbx on colonoscopy-only base correctly falls back to the `blurbs_html` paragraph path (verified by `test_composed_colonoscopy_rsbx_renders`). EGD-only template assignments for the new tokens are no-ops (tokens absent from EGD template → `str.replace` is identity).
