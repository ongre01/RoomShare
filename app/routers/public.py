"""Public HTTP adapters; all DB operations remain in synchronous services."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute

from app.schemas import (
    CalendarResponse, CancellationRequest, CancellationResponse,
    ReservationRequest, ReservationResponse,
)
from app.services.calendar import get_calendar
from app.services.cancellation import cancel_reservation
from app.services.errors import InvalidInputError
from app.services.reservations import create_reservation


class PublicRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def parse_json(request: Request):
            if request.method == "POST":
                json_body_only(request)
                # Parse once and cache on Request before FastAPI validates it.
                # Bound bytes are already enforced by PublicAPIMiddleware.
                try:
                    await request.json()
                except (ValueError, UnicodeError, RecursionError):
                    raise HTTPException(status_code=400) from None
            return await handler(request)

        return parse_json


router = APIRouter(prefix="/api", route_class=PublicRoute)


def json_body_only(request: Request) -> None:
    if request.query_params:
        raise InvalidInputError("query")
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415)


def service_options(request: Request) -> dict:
    return {
        "db_path": request.app.state.settings.db_path,
        "clock": request.app.state.clock,
        "busy_timeout_ms": request.app.state.busy_timeout_ms,
    }


@router.get("/calendar", response_model=CalendarResponse)
def calendar(request: Request, month: str) -> dict:
    if list(request.query_params.multi_items()) != [("month", month)]:
        raise InvalidInputError("query")
    return get_calendar(month=month, **service_options(request))


@router.post(
    "/reservations", status_code=201, response_model=ReservationResponse,
)
def reserve(request: Request, body: ReservationRequest) -> dict:
    return create_reservation(**body.model_dump(), **service_options(request))


@router.post(
    "/reservations/cancel", response_model=CancellationResponse,
)
def cancel(request: Request, body: CancellationRequest) -> dict:
    return cancel_reservation(**body.model_dump(), **service_options(request))
