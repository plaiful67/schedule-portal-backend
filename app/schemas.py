"""Pydantic request/response models for the /render endpoint.

A discriminated union on `procedure_type` enforces per-procedure validation:
  - bowel_prep / combined / flex_sig require a weight_band
  - egd has no weight_band

Stateless: no patient identifiers; nothing persisted server-side.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


# Weight bands as they appear in dosing.yaml (band.id values).
# Phase 1 ships only bowel_prep; the other unions are stubbed for forward-compat.
BowelPrepBand = Literal["under-15", "under-15-enema", "15-20", "21-30", "31-40", "41-50", "over-50"]
FlexSigBand = Literal["under-15kg", "20-40kg", "over-40kg"]

# Bowel-prep medication. MiraLAX is the default (matches what's been deployed
# all along). The non-default options are scheduler-gated and served from
# hidden subdomains (never linked from giready.com):
#   - Lactulose: backup for small kids who can't tolerate the MiraLAX+Gatorade
#     volume — only valid for under-15, 15-20, 21-30 kg; routes to
#     preplact{,86} / egdcolonlact{,86}.
#   - CLENPIQ (sodium picosulfate): alternative for kids 31 kg and up who
#     prefer a smaller-volume oral prep — only valid for 31-40, 41-50,
#     over-50; routes to prepclenpiq{,86} / egdcolonclenpiq{,86}.
#   - SUPREP (sodium/potassium/magnesium sulfate, Rx, FDA age 12+):
#     sulfate-based alternative for patients 50 kg and up — only valid for
#     over-50; routes to prepsuprep{,86} / egdcolonsuprep{,86}.
PrepType = Literal["miralax", "lactulose", "clenpiq", "suprep"]
LACTULOSE_ALLOWED_BANDS: set[str] = {"under-15", "15-20", "21-30"}
CLENPIQ_ALLOWED_BANDS:   set[str] = {"31-40", "41-50", "over-50"}
SUPREP_ALLOWED_BANDS:    set[str] = {"over-50"}

# IDENTITY fields (physician_id, location_id) are NO LONGER Literal enums.
# Unioning every tenant's roster/locations into a shared Literal would weld the
# tenants together at the type level — the one M3 change that's expensive to
# undo. They are now plain `str`, validated at RUNTIME against the *resolved
# tenant config* passed via Pydantic validation context (see
# _identity_membership below). CLINICAL cross-rules (band/prep-type gating)
# stay shared sets — they are tenant-independent dose-safety facts. The demo
# reuses giready's band set, so those validators stay valid.
#
# Validation context shape (set by the route, never by the client body):
#   {"physician_ids": set[str], "location_ids": set[str]}
# When absent (e.g. a direct unit-test construction with no context), the
# membership check is skipped — the route ALWAYS supplies it for real requests.


class _Base(BaseModel):
    location_id: str
    language: Literal["en", "es"]
    physician_id: str
    # Whether to bake the {location, lang} driving-directions PDF onto the
    # end of the prep handout. Defaults to True so older frontend builds
    # (or any direct API caller) still get directions without opting in.
    include_directions: bool = True
    appointment_date: date
    appointment_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    arrival_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    stop_meds: list[str] = Field(default_factory=list, max_length=20)
    # Optional follow-up appointment. If absent, the handout prints a
    # "Call the office to schedule a follow-up appointment." fallback.
    followup_date: date | None = None
    followup_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")

    @field_validator("appointment_date")
    @classmethod
    def _date_not_in_past(cls, v: date) -> date:
        if v < date.today():
            raise ValueError("appointment_date must be today or later")
        return v

    @model_validator(mode="after")
    def _followup_pair(self):
        if (self.followup_date is None) != (self.followup_time is None):
            raise ValueError(
                "followup_date and followup_time must both be set or both be omitted"
            )
        if self.followup_date is not None and self.followup_date < self.appointment_date:
            raise ValueError("followup_date must be on or after appointment_date")
        return self

    @model_validator(mode="after")
    def _identity_membership(self, info: ValidationInfo):
        """Runtime tenant-membership check for the identity fields, replacing the
        former Literal enums. The route supplies the resolved tenant's allowed
        physician_ids / location_ids via validation context; absent context
        (unit construction) skips the check."""
        ctx = info.context or {}
        allowed_phys = ctx.get("physician_ids")
        if allowed_phys is not None and self.physician_id not in allowed_phys:
            raise ValueError(
                f"unknown physician_id={self.physician_id!r} for this practice "
                f"(known: {sorted(allowed_phys)})"
            )
        allowed_loc = ctx.get("location_ids")
        if allowed_loc is not None and self.location_id not in allowed_loc:
            raise ValueError(
                f"unknown location_id={self.location_id!r} for this practice "
                f"(known: {sorted(allowed_loc)})"
            )
        return self


class _BowelPrepBase(_Base):
    """Shared base for procedures that involve a bowel prep (colonoscopy-only
    or combined EGD+colon). Carries weight_band + prep_type with cross-
    validation: lactulose is small-kid-only, clenpiq is big-kid-only."""
    weight_band: BowelPrepBand
    prep_type: PrepType = "miralax"

    @model_validator(mode="after")
    def _prep_type_band_check(self):
        if self.prep_type == "lactulose" and self.weight_band not in LACTULOSE_ALLOWED_BANDS:
            raise ValueError(
                f"prep_type=lactulose is only available for weight bands "
                f"{sorted(LACTULOSE_ALLOWED_BANDS)} (got {self.weight_band!r})"
            )
        if self.prep_type == "clenpiq" and self.weight_band not in CLENPIQ_ALLOWED_BANDS:
            raise ValueError(
                f"prep_type=clenpiq is only available for weight bands "
                f"{sorted(CLENPIQ_ALLOWED_BANDS)} (got {self.weight_band!r})"
            )
        if self.prep_type == "suprep" and self.weight_band not in SUPREP_ALLOWED_BANDS:
            raise ValueError(
                f"prep_type=suprep is only available for weight bands "
                f"{sorted(SUPREP_ALLOWED_BANDS)} (got {self.weight_band!r})"
            )
        return self


class BowelPrepRequest(_BowelPrepBase):
    procedure_type: Literal["bowel_prep"]


class EGDRequest(_Base):
    procedure_type: Literal["egd"]


class EGDPhMiiRequest(_Base):
    """EGD + 24-hr pH impedance monitoring. PMCH only (motility nurses staff
    only St. Vincent 86th St — see project_pmch_only_procedures memory).
    location_id is narrowed at the schema layer so SCC submissions 422 cleanly."""
    procedure_type: Literal["egd_phmii"]
    location_id: Literal["pmch"] = "pmch"  # type: ignore[assignment]


class CombinedRequest(_BowelPrepBase):
    procedure_type: Literal["combined"]


class FlexSigRequest(_Base):
    procedure_type: Literal["flex_sig"]
    weight_band: FlexSigBand


class ComposedRequest(_Base):
    """Base procedure + prep-neutral add-on procedures (sleep endoscopy, ENT
    airway, BAL, rectal suction biopsy, ...). The handout title and add-on
    blurbs are assembled by the skill's composition resolver from these ids;
    knob_picks parameterizes any add-on knobs (e.g. {"ppi_handling": "hold"}).

    `base` selects which prep backbone the add-ons ride:
      - "egd"         → no bowel prep; weight_band MUST be absent.
      - "colonoscopy" → colonoscopy-only bowel prep; weight_band REQUIRED.
      - "combined"    → EGD+colonoscopy combined prep; weight_band REQUIRED.
    Defaulting base="egd" keeps the EGD-only composed path valid for callers
    that send no base.
    """
    procedure_type: Literal["composed"]
    base: Literal["egd", "colonoscopy", "combined"] = "egd"
    weight_band: BowelPrepBand | None = None
    prep_type: PrepType = "miralax"
    add_ons: list[str] = Field(..., min_length=1, max_length=10)
    knob_picks: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _composed_base_band_check(self):
        prep_bases = {"colonoscopy", "combined"}
        if self.base in prep_bases and self.weight_band is None:
            raise ValueError(f"weight_band is required when base={self.base!r}")
        if self.base == "egd" and self.weight_band is not None:
            raise ValueError("weight_band must be absent when base='egd'")
        if self.weight_band is not None:
            if self.prep_type == "lactulose" and self.weight_band not in LACTULOSE_ALLOWED_BANDS:
                raise ValueError(
                    f"prep_type=lactulose is only available for weight bands "
                    f"{sorted(LACTULOSE_ALLOWED_BANDS)} (got {self.weight_band!r})")
            if self.prep_type == "clenpiq" and self.weight_band not in CLENPIQ_ALLOWED_BANDS:
                raise ValueError(
                    f"prep_type=clenpiq is only available for weight bands "
                    f"{sorted(CLENPIQ_ALLOWED_BANDS)} (got {self.weight_band!r})")
            if self.prep_type == "suprep" and self.weight_band not in SUPREP_ALLOWED_BANDS:
                raise ValueError(
                    f"prep_type=suprep is only available for weight bands "
                    f"{sorted(SUPREP_ALLOWED_BANDS)} (got {self.weight_band!r})")
        return self


RenderRequest = Annotated[
    BowelPrepRequest | EGDRequest | EGDPhMiiRequest | CombinedRequest | FlexSigRequest | ComposedRequest,
    Field(discriminator="procedure_type"),
]
