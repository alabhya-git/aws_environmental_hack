"""Wire and storage schemas. Persisted quantities are integer grams; times are UTC."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Origin = Literal["measured", "simulated", "imported"]
Measurement = Literal["weighed", "scoop_estimate", "photo_estimate", "generated"]
Satisfaction = Literal["enough", "wanted_more", "unsure"]
LeftoverReason = Literal["none", "enough_food", "dislike", "interrupted", "shared", "unknown"]
SecondsStatus = Literal["not_requested", "served", "unavailable", "delayed", "declined"]
EventType = Literal["serve_initial", "serve_seconds", "return", "decline_food", "void"]

ID = r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,63}$"
MAX_G = 5000


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PhotoDetail(Strict):
    """How a photo estimate was derived. Numbers only; the photos never leave the device."""

    method: Literal["before_after", "after_only"]
    share_before: Optional[float] = Field(default=None, ge=0, le=1)   # food as a share of the plate's area
    share_after: float = Field(ge=0, le=1)
    reference_g: int = Field(ge=0, le=MAX_G)                           # grams in the before photo
    exponent: float = Field(ge=0.5, le=2.5)                            # mass ~ area^exponent
    edited: bool = False                                               # outline corrected by hand
    engine: Literal["model", "by-hand"] = "model"


class EventIn(Strict):
    """One immutable observation. `event_id` is the idempotency key."""

    event_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{8,64}$")
    session_id: str = Field(pattern=ID)
    diner_token: str = Field(pattern=ID)
    food_id: str = Field(pattern=ID)
    type: EventType
    grams: Optional[int] = Field(default=None, ge=0, le=MAX_G)
    scoops: Optional[float] = Field(default=None, ge=0, le=40)
    measurement: Measurement = "scoop_estimate"
    satisfaction: Optional[Satisfaction] = None
    leftover_reason: Optional[LeftoverReason] = None
    seconds_status: Optional[SecondsStatus] = None
    seconds_wait_min: Optional[int] = Field(default=None, ge=0, le=240)
    photo_sd_g: Optional[int] = Field(default=None, ge=1, le=1000)  # stated uncertainty of a photo estimate
    photo_detail: Optional[PhotoDetail] = None
    losses_g: int = Field(default=0, ge=0, le=MAX_G)
    incoming_transfer_g: int = Field(default=0, ge=0, le=MAX_G)
    transfer_unknown: bool = False
    requested_scoops: Optional[float] = Field(default=None, gt=0, le=40)
    recommended: Optional[dict] = None
    supersedes: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_\-]{8,64}$")
    event_time: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=200)

    @field_validator("event_time")
    @classmethod
    def _utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


class FoodIn(Strict):
    name: str = Field(min_length=1, max_length=60)
    recipe_version: str = Field(default="v1", pattern=ID)
    cost_per_kg: Optional[float] = Field(default=None, ge=0, le=100000)


class CalibrationIn(Strict):
    food_id: str = Field(pattern=ID)
    scoop_id: str = Field(default="scoop-1", pattern=ID)
    weights_g: list[int] = Field(min_length=5, max_length=60)
    scale_resolution_g: int = Field(default=1, ge=1, le=50)
    measurement: Measurement = "weighed"

    @field_validator("weights_g")
    @classmethod
    def _positive(cls, v: list[int]) -> list[int]:
        if any(w <= 0 or w > 2000 for w in v):
            raise ValueError("each scoop weight must be between 1 and 2000 g")
        return v


class SessionIn(Strict):
    name: str = Field(min_length=1, max_length=60)
    service_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    food_ids: list[str] = Field(min_length=1, max_length=12)
    expected_attendance: int = Field(default=120, ge=1, le=5000)
    attendance_cv: float = Field(default=0.10, ge=0, le=0.6)
    intervals: int = Field(default=6, ge=2, le=16)
    interval_minutes: int = Field(default=15, ge=5, le=60)
    batch_g: int = Field(default=5000, ge=500, le=100000)
    lead_minutes: int = Field(default=30, ge=0, le=480)
    max_parallel_batches: int = Field(default=2, ge=1, le=10)
    stock_cooked_equiv_g: int = Field(default=60000, ge=0, le=5_000_000)
    refill_capacity_per_interval: int = Field(default=100, ge=0, le=5000)
    seconds_accessible: bool = True
    conventional_scoops: float = Field(default=3.0, gt=0, le=20)


class SessionPatch(Strict):
    session_id: str = Field(pattern=ID)
    status: Optional[Literal["open", "closed"]] = None
    expected_attendance: Optional[int] = Field(default=None, ge=1, le=5000)
    attendance_cv: Optional[float] = Field(default=None, ge=0, le=0.6)
    batch_g: Optional[int] = Field(default=None, ge=500, le=100000)
    lead_minutes: Optional[int] = Field(default=None, ge=0, le=480)
    stock_cooked_equiv_g: Optional[int] = Field(default=None, ge=0, le=5_000_000)
    refill_capacity_per_interval: Optional[int] = Field(default=None, ge=0, le=5000)
    seconds_accessible: Optional[bool] = None
    surplus_destination: Optional[Literal["retained", "donated", "discarded", "unknown"]] = None
    kitchen_surplus_g: Optional[int] = Field(default=None, ge=0, le=5_000_000)
    prepared_g: Optional[int] = Field(default=None, ge=0, le=5_000_000)


class DinerIn(Strict):
    token: Optional[str] = Field(default=None, pattern=ID)
    consent: bool = True
    portion_preference_scoops: Optional[float] = Field(default=None, gt=0, le=40)
    personalization_opt_out: bool = False


class DinerRef(Strict):
    token: str = Field(pattern=ID)


class DinerStateArgs(Strict):
    session_id: str = Field(pattern=ID)
    token: str = Field(pattern=ID)
    food_id: str = Field(pattern=ID)
    requested_scoops: Optional[float] = Field(default=None, gt=0, le=40)


class LearnArgs(Strict):
    token: str = Field(pattern=ID)
    food_id: str = Field(pattern=ID)


class WhatIfArgs(Strict):
    token: str = Field(pattern=ID)
    food_id: str = Field(pattern=ID)
    plate_g: int = Field(ge=1, le=MAX_G)
    repeats: int = Field(default=1, ge=1, le=6)  # the same plate on this many meals in a row


class KitchenArgs(Strict):
    session_id: str = Field(pattern=ID)
    food_id: str = Field(pattern=ID)
    expected_attendance: Optional[int] = Field(default=None, ge=1, le=5000)
    attendance_cv: Optional[float] = Field(default=None, ge=0, le=0.6)
    lead_minutes: Optional[int] = Field(default=None, ge=0, le=480)
    batch_g: Optional[int] = Field(default=None, ge=500, le=100000)
    stock_cooked_equiv_g: Optional[int] = Field(default=None, ge=0, le=5_000_000)
    refill_capacity_per_interval: Optional[int] = Field(default=None, ge=0, le=5000)
    alpha: Optional[float] = Field(default=None, ge=0.005, le=0.5)
    policy: Literal["proofplate", "conventional"] = "proofplate"


class EvidenceArgs(Strict):
    food_id: Optional[str] = Field(default=None, pattern=ID)


class SessionRef(Strict):
    session_id: str = Field(pattern=ID)


class ExportArgs(Strict):
    what: Literal["events", "participations"] = "participations"
    format: Literal["json", "csv"] = "csv"
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=2000, ge=1, le=5000)


class ImportManifest(Strict):
    source_name: str = Field(min_length=2, max_length=120)
    source_url: Optional[str] = Field(default=None, max_length=300)
    license: str = Field(min_length=2, max_length=120)
    collection_method: str = Field(min_length=2, max_length=300)
    measurement_status: Literal["measured", "synthetic"]
    what_it_measures: str = Field(min_length=2, max_length=300)
    food_id: str = Field(pattern=ID)


class ImportArgs(Strict):
    csv_text: str = Field(max_length=400_000)
    manifest: ImportManifest


class SettingsPatch(Strict):
    """Site-level policy assumptions a kitchen manager may change."""

    refill_friction_g: Optional[float] = Field(default=None, ge=0, le=80)
    max_change_scoops: Optional[float] = Field(default=None, ge=0.5, le=4)
    alpha: Optional[float] = Field(default=None, ge=0.01, le=0.25)
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)


class Empty(Strict):
    pass
