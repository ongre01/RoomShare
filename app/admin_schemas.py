"""Strict administrator payloads, with explicit response field allowlists."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas import ReservationRequest, StrictModel


class ReservationPatch(ReservationRequest):
    # Defaults allow omission; inherited validators reject explicit null.
    name: str = None
    dates: list[str] = None
    memo: str = None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set:
            raise ValueError("At least one field is required")
        return self


class BlockRequest(StrictModel):
    dates: list[str] = Field(min_length=1)
    memo: str = Field(default="", max_length=200)


class SettingsRequest(StrictModel):
    booking_horizon_days: int = Field(ge=1, le=366)


class AdminCreated(StrictModel):
    id: str
    dates: list[str]
    status: Literal["CONFIRMED"]


class AdminReservation(StrictModel):
    id: str
    name: str
    memo: str
    dates: list[str]
    status: Literal["CONFIRMED", "CANCELLED"]
    created_at: str
    cancelled_at: str | None


class ReservationList(StrictModel):
    reservations: list[AdminReservation]


class BlockResponse(StrictModel):
    dates: list[str]
    status: Literal["BLOCKED"]


class UnblockResponse(StrictModel):
    date: str
    status: Literal["AVAILABLE"]


class SettingsResponse(StrictModel):
    booking_horizon_days: int


class AdminLog(StrictModel):
    id: int
    reservation_id: str | None
    action: str
    detail: dict
    created_at: str


class LogList(StrictModel):
    logs: list[AdminLog]
    page: int
    page_size: int
    total: int


class CSRFResponse(StrictModel):
    csrf_token: str
    expires_in: int
