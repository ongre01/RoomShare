"""ASGI factory: uvicorn app.main:create_app --factory --host 127.0.0.1 --no-access-log."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings, load_settings
from app.api_errors import install_error_handlers
from app.db import DEFAULT_BUSY_TIMEOUT_MS
from app.middleware import PublicAPIMiddleware
from app.routers.public import router as public_router
from app.services.date_policy import Clock, KST, utc_now


APP_DIR = Path(__file__).resolve().parent


def create_app(
    settings: Settings | None = None, *, clock: Clock = utc_now,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> FastAPI:
    """Create an independent app; no DB is opened, created, or migrated here."""
    if settings is None:
        settings = load_settings()
    if not isinstance(settings, Settings):
        raise TypeError("settings must be a Settings instance")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.state.settings = settings
    app.state.clock = clock
    app.state.busy_timeout_ms = busy_timeout_ms
    install_error_handlers(app)
    app.add_middleware(
        PublicAPIMiddleware,
        max_body_bytes=settings.max_body_bytes,
        rate_limit_requests=settings.rate_limit_requests,
        rate_limit_window_seconds=settings.rate_limit_window_seconds,
    )
    app.include_router(public_router)
    templates = Jinja2Templates(directory=APP_DIR / "templates")
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        # Bootstrap the first month from the server clock, never the device's
        # timezone. Calendar API responses supply the current booking policy.
        today = request.app.state.clock().astimezone(KST).date()
        return templates.TemplateResponse(
            request=request, name="home.html",
            context={"initial_month": today.isoformat()[:7]},
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    return app
