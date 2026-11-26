"""ASGI factory: uvicorn app.main:create_app --factory --host 127.0.0.1."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings, load_settings


APP_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an independent app; no DB is opened, created, or migrated here."""
    if settings is None:
        settings = load_settings()
    if not isinstance(settings, Settings):
        raise TypeError("settings must be a Settings instance")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    templates = Jinja2Templates(directory=APP_DIR / "templates")
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="home.html")

    return app
