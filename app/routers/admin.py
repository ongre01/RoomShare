"""Protected HTTP adapters; middleware guards every administrator path first."""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates

from app.admin_schemas import (
    AdminCreated, AdminReservation, BlockRequest, BlockResponse, CSRFResponse,
    LogList, ReservationList, ReservationPatch, SettingsRequest, SettingsResponse,
    UnblockResponse,
)
from app.routers.public import json_body_only, service_options
from app.schemas import CancellationResponse, ReservationRequest
from app.security import issue_csrf_token
from app.services import admin
from app.services.errors import InvalidInputError
from app.services.date_policy import KST
from app.services.reservations import create_reservation


class AdminRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validate_request(request: Request):
            if request.method in {"POST", "PATCH"}:
                json_body_only(request)
                try:
                    await request.json()
                except (ValueError, UnicodeError, RecursionError):
                    raise HTTPException(status_code=400) from None
            elif request.method == "DELETE":
                if request.query_params or await request.body():
                    raise InvalidInputError("request")
            elif await request.body():
                raise InvalidInputError("request")
            return await handler(request)

        return validate_request


router = APIRouter(prefix="/api/admin", route_class=AdminRoute)
page_router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


@page_router.get("/admin", response_class=HTMLResponse)
def management_page(request: Request) -> HTMLResponse:
    """Render only after AdminMiddleware; no DB data or credentials in HTML."""
    today = request.app.state.clock().astimezone(KST).date()
    return templates.TemplateResponse(
        request=request, name="admin/index.html",
        context={"initial_month": today.isoformat()[:7]},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


def read_options(request: Request) -> dict:
    return {
        "db_path": request.app.state.settings.db_path,
        "busy_timeout_ms": request.app.state.busy_timeout_ms,
    }


def query_values(request: Request, allowed: set[str]) -> dict[str, str]:
    pairs = list(request.query_params.multi_items())
    values = dict(pairs)
    if len(values) != len(pairs) or values.keys() - allowed:
        raise InvalidInputError("query")
    return values


@router.get("/csrf", response_model=CSRFResponse)
def csrf(request: Request) -> dict:
    query_values(request, set())
    return issue_csrf_token(request)


@router.get("/reservations", response_model=ReservationList)
def reservations(request: Request) -> dict:
    values = query_values(request, {"month", "status"})
    return admin.list_reservations(
        month=values.get("month"), status=values.get("status", "CONFIRMED"),
        **read_options(request),
    )


@router.post("/reservations", status_code=201, response_model=AdminCreated)
def reserve(request: Request, body: ReservationRequest) -> dict:
    result = create_reservation(**body.model_dump(), **service_options(request))
    # Administrator-created reservations have no user-facing cancellation token.
    return {key: result[key] for key in ("id", "dates", "status")}


@router.patch("/reservations/{id}", response_model=AdminReservation)
def update(request: Request, id: str, body: ReservationPatch) -> dict:
    return admin.update_reservation(
        id=id, changes=body.model_dump(exclude_unset=True), **service_options(request),
    )


@router.delete("/reservations/{id}", response_model=CancellationResponse)
def cancel(request: Request, id: str) -> dict:
    return admin.cancel_reservation(id=id, **service_options(request))


@router.post("/blocked-dates", status_code=201, response_model=BlockResponse)
def block(request: Request, body: BlockRequest) -> dict:
    return admin.block_dates(**body.model_dump(), **service_options(request))


@router.delete("/blocked-dates/{date}", response_model=UnblockResponse)
def unblock(request: Request, date: str) -> dict:
    return admin.unblock_date(date=date, **service_options(request))


@router.get("/logs", response_model=LogList)
def logs(request: Request) -> dict:
    values = query_values(request, {"page", "page_size"})
    parsed = {}
    for key, default, upper in (("page", "1", 2147483647), ("page_size", "50", 100)):
        value = values.get(key, default)
        if not re.fullmatch(r"[1-9][0-9]{0,9}", value) or int(value) > upper:
            raise InvalidInputError(key)
        parsed[key] = int(value)
    return admin.get_logs(**parsed, **read_options(request))


@router.get("/settings", response_model=SettingsResponse)
def settings(request: Request) -> dict:
    query_values(request, set())
    return admin.get_settings(**read_options(request))


@router.patch("/settings", response_model=SettingsResponse)
def change_settings(request: Request, body: SettingsRequest) -> dict:
    return admin.update_settings(**body.model_dump(), **service_options(request))
