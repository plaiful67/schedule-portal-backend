# Personalized-template reconciliation ledger

**Status:** DRAFT for human review (Sebastian) — gates Phase 2 of the standardization program.
**Date:** 2026-06-19
**Purpose:** Before generalizing `scripts/build_personalized_templates.py` from 2 → many
auto-generated personalized print templates, every difference between each hand-maintained
`*-print-personalized.{en,es}.html` and its canonical source is classified so that
**no intentional clinical/UX nuance is silently flattened** by a uniform generator (review risk R1).

A template may be auto-generated only once all its residual deltas are classified here as either
**DRIFT** (accidental → fold into the shared primitive) or **INTENTIONAL** (deliberate → must be
reproduced by a *named override slot*). Templates with no standing canonical stay on the
`HAND_MAINTAINED` allowlist. **EXPECTED** deltas (the documented personalization transforms —
legal-footer strip, personalization CSS, performing-physician block, appt-callout at
`<!-- LOCATION -->`, `{{FOLLOWUP_BLOCK_HTML}}`, `pz-only`/`data-pz-*` date markup, BANNER) are not findings.

## Headline findings

- **No dosing / NPO / timing drift anywhere.** Every SUPREP / CLENPIQ / lactulose dose volume,
  bottle count, timing window, and the infant-enema "10 mL/kg" dose is token- or byte-identical to
  its canonical across all 24 canonical-backed pairs. The risk is UX/layout, not dose-text.
- **🔴 Pre-existing regression (fix independent of the refactor): `egd/print-personalized.{en,es}`
  dropped the feedback-QR bar.** Canonical `egd-print` carries one `{{FEEDBACK_URL}}` block;
  the personalized EGD print has **zero** (verified: `grep -c FEEDBACK_URL` → canonical 1, personalized 0).
  This violates the standing "feedback QR on every handout" rule. Restore it (and drop the dead
  unused `.meds-reference` CSS in those two files) — this is content loss, not personalization.
- **8 personalized combined-variant templates have no canonical** → permanently `HAND_MAINTAINED` (decision locked).
- All other INTENTIONAL deltas are UX/layout and map to ~5 named override slots (below).

## A. Mapping verification (24 canonical-backed + 8 hand-maintained = 32)

All 24 mapped canonicals exist. Only `combined-print.{en,es}` is currently wired into `VARIANTS`
(script-generated + drift-checked); the other 22 are silent hand-maintained forks.

| Personalized (×en/es) | Canonical | Generated today? |
|---|---|---|
| print-personalized | vendor `standard-print` | no |
| infant-print / infant-enema-print | vendor `infant-print` / `infant-enema-print` | no |
| lactulose-standard / lactulose-infant | vendor `lactulose-standard-print` / `lactulose-infant-print` | no |
| suprep-standard / clenpiq-standard | vendor `suprep-standard-print` / `clenpiq-standard-print` | no |
| combined-print | vendor `combined-print` | **yes** |
| combined-infant / combined-infant-enema | vendor `combined-infant-print` / `combined-infant-enema-print` | no |
| egd/print | skill `egd-print` | no |
| egd_phmii/print | skill `egdph-print` | no |

## B. Gating decision per template

### Clean — safe to auto-generate as-is (DRIFT-only / EXPECTED-only)
- **suprep-standard** (en+es) — 0 residuals. `.meds-reference` present symmetrically in both. No dosing drift.
- **clenpiq-standard** (en+es) — 0 residuals. No dosing drift.
- **combined-print** (en+es) — already generated + faithful. (es has a stray `</div>` mirrored from
  canonical — a canonical-side cosmetic defect; fix upstream in the skill template, not via override.)

### Auto-generable, but require a NAMED OVERRIDE SLOT for an intentional delta
| Override slot | Templates | What it preserves |
|---|---|---|
| `FEEDBACK_BAR` (resource-card → bottom `feedback-bar` table) | infant, infant-enema, lactulose-standard, lactulose-infant, combined-infant, combined-infant-enema (all en+es) | the feedback CTA is relocated, not lost; clinical text identical |
| `MEDS_REFERENCE_CSS` | print-personalized / standard (en+es) | `.meds-reference` CSS block absent from canonical `standard-print` (verified 0 vs 9) |
| `APPT_CALLOUT_FORM` (one-row vs multi-block) | print-personalized (multi-block), combined-infant / combined-infant-enema (multi-block) vs combined-print (one-row) | per-variant appt-callout layout |
| `EGD_MEDS_CALLOUT` (3-`<p>`→2-`<p>` rewording, en+es) | egd/print | deliberate, consistent meds-callout phrasing; GLP-1 7-day stop preserved |
| `PHMII_DATE_STAMPS` (per-heading date pz-spans + `.med-stop-date` CSS) | egd_phmii/print (en+es) | genuine personalization that any generator must reproduce |

### Must-fix BEFORE treating EGD as auto-generable (regression, not an override)
- **egd/print-personalized.{en,es}** — restore the dropped `{{FEEDBACK_URL}}` feedback bar; remove dead `.meds-reference` CSS.

## C. HAND_MAINTAINED allowlist (no canonical — never auto-generated)

The inventory gate must assert these exist + are classified, but exclude them from regeneration:

| File (×en/es) | Reason |
|---|---|
| combined-suprep-standard-print-personalized | no canonical `combined-suprep-standard-print`; hand-derived combined EGD+colo SUPREP |
| combined-clenpiq-standard-print-personalized | no canonical `combined-clenpiq-standard-print`; hand-derived |
| combined-lactulose-standard-print-personalized | no canonical `combined-lactulose-standard-print`; hand-derived |
| combined-lactulose-infant-print-personalized | no canonical `combined-lactulose-infant-print`; hand-derived |

Trade-off accepted: these 8 do not auto-inherit canonical wording fixes; combined-prep print copy
changes must be hand-applied. The gate detects their continued presence/classification, not content drift.

## D. Sign-off

- [ ] Sebastian reviewed the override slots and the `HAND_MAINTAINED` list.
- [ ] EGD feedback-bar regression fixed (separately) before EGD is added to the generated set.
- [ ] Then: implement the patch primitives + override slots, extend `check_template_drift.py` to the
      generated set, add the inventory gate, and do the isolated `rebaseline:` commit.
