"""Strict public request/response contracts, with no implicit type coercion."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReservationRequest(StrictModel):
    name: str
    dates: list[str] = Field(min_length=1)
    memo: str = Field(default="", max_length=200)

    @field_validator("name", "memo")
    @classmethod
    def valid_unicode(cls, value: str) -> str:
        # JSON escapes can represent lone surrogates, which SQLite cannot store.
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Invalid text") from None
        return value

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 40:
            raise ValueError("Invalid name")
        return value


class CancellationRequest(StrictModel):
    id: str
    cancel_token: str


class ReservationResponse(StrictModel):
    id: str
    dates: list[str]
    status: Literal["CONFIRMED"]
    cancel_token: str


class CancellationResponse(StrictModel):
    id: str
    status: Literal["CANCELLED"]


class CalendarDay(StrictModel):
    date: str
    status: Literal["AVAILABLE", "RESERVED", "BLOCKED", "PAST"]


class CalendarResponse(StrictModel):
    dates: list[CalendarDay]
    today: str
    min_date: str
    max_date: str
