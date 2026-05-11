"""Pydantic request/response models for the /render endpoint.

A discriminated union on `procedure_type` enforces per-procedure validation:
  - bowel_prep / combined / flex_sig require a weight_band
  - egd has no weight_band

Stateless: no patient identifiers; nothing persisted server-side.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


# Weight bands as they appear in dosing.yaml (band.id values).
# Phase 1 ships only bowel_prep; the other unions are stubbed for forward-compat.
BowelPrepBand = Literal["under-15", "15-20", "21-30", "31-40", "41-50", "51-65", "66-80", "81+"]
FlexSigBand = Literal["under-15kg", "20-40kg", "over-40kg"]


class _Base(BaseModel):
    location_id: Literal["scc", "pmch"]
    language: Literal["en", "es"]
    appointment_date: date
    appointment_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    arrival_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    stop_meds: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("appointment_date")
    @classmethod
    def _date_not_in_past(cls, v: date) -> date:
        if v < date.today():
            raise ValueError("appointment_date must be today or later")
        return v


class BowelPrepRequest(_Base):
    procedure_type: Literal["bowel_prep"]
    weight_band: BowelPrepBand


class EGDRequest(_Base):
    procedure_type: Literal["egd"]


class CombinedRequest(_Base):
    procedure_type: Literal["combined"]
    weight_band: BowelPrepBand


class FlexSigRequest(_Base):
    procedure_type: Literal["flex_sig"]
    weight_band: FlexSigBand


RenderRequest = Annotated[
    BowelPrepRequest | EGDRequest | CombinedRequest | FlexSigRequest,
    Field(discriminator="procedure_type"),
]
