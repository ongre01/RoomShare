"""Fail-closed administrator boundary; proxy transport is verified at deployment.

No HTTP header alone establishes authority. The opt-in production mode needs a
loopback transport peer, a private proxy secret, and the proxy's mTLS verdict.
Uvicorn must run with --no-proxy-headers so the transport peer cannot be rewritten.
"""

from dataclasses import dataclass
import hashlib
import hmac
import inspect
import re
import secrets

from fastapi import FastAPI, Request
from starlette._utils import get_route_path
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.middleware import PublicAPIMiddleware


CSRF_TTL_SECONDS = 1800
_CSRF_FORMAT = re.compile(r"([0-9]{1,12})\.([A-Za-z0-9_-]{43})\.([0-9a-f]{64})", re.ASCII)


@dataclass(frozen=True)
class AdminContext:
    """A verified server-side context, never deserialized from request data."""

    verified: bool = True


def is_admin_path(path: str) -> bool:
    return any(path == root or path.startswith(root + "/") for root in ("/admin", "/api/admin"))


def _single_header(scope: Scope, name: bytes) -> bytes | None:
    values = [value for key, value in scope.get("headers", []) if key.lower() == name]
    return values[0] if len(values) == 1 else None


def resolve_admin_context(request: Request) -> AdminContext | None:
    """Validate production proxy evidence; tests override this callable in code.

    Header forwarding is deliberately not interpreted here. RS-008 must verify
    Nginx overwrites the private headers and prevents external backend access.
    """
    settings = request.app.state.settings
    if settings.admin_access != "proxy_mtls":
        return None
    peer = request.scope.get("client")
    if not peer or peer[0] not in ("127.0.0.1", "::1"):
        return None
    if _single_header(request.scope, b"x-roomshare-admin-verified") != b"SUCCESS":
        return None
    supplied = _single_header(request.scope, b"x-roomshare-proxy-secret")
    if supplied is None or settings.admin_proxy_secret is None:
        return None
    if not hmac.compare_digest(supplied, settings.admin_proxy_secret.encode("ascii")):
        return None
    return AdminContext()


def initialize_admin_security(app: FastAPI) -> None:
    """An independent, ephemeral 256-bit signing key per application instance."""
    app.state.admin_csrf_secret = secrets.token_bytes(32)


def _csrf_signature(request: Request, issued: str, nonce: str) -> str:
    payload = f"roomshare:csrf:v1:{request.app.state.settings.admin_host}:{issued}:{nonce}"
    return hmac.new(request.app.state.admin_csrf_secret, payload.encode("ascii"), hashlib.sha256).hexdigest()


def _now(request: Request) -> int:
    now = request.app.state.clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Administrator clock must be timezone-aware")
    return int(now.timestamp())


def issue_csrf_token(request: Request) -> dict:
    # Only the protected GET endpoint calls this function. Keep this assertion
    # here as a guard against accidentally adding an unprotected issuer later.
    context = getattr(request.state, "admin_context", None)
    if type(context) is not AdminContext or not context.verified:
        raise RuntimeError("Administrator context required")
    issued = str(_now(request))
    nonce = secrets.token_urlsafe(32)
    return {
        "csrf_token": f"{issued}.{nonce}.{_csrf_signature(request, issued, nonce)}",
        "expires_in": CSRF_TTL_SECONDS,
    }


def _valid_csrf(request: Request) -> bool:
    expected_origin = f"https://{request.app.state.settings.admin_host}".encode("ascii")
    if _single_header(request.scope, b"origin") != expected_origin:
        return False
    token = _single_header(request.scope, b"x-csrf-token")
    if token is None or len(token) > 121:
        return False
    try:
        matched = _CSRF_FORMAT.fullmatch(token.decode("ascii"))
    except UnicodeDecodeError:
        return False
    if matched is None:
        return False
    issued, nonce, signature = matched.groups()
    if not hmac.compare_digest(signature, _csrf_signature(request, issued, nonce)):
        return False
    age = _now(request) - int(issued)
    return 0 <= age < CSRF_TTL_SECONDS


class AdminMiddleware:
    """Protect every admin path before routing, validation, or request parsing."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.protected_app = PublicAPIMiddleware(
            app,
            max_body_bytes=settings.max_body_bytes,
            rate_limit_requests=settings.rate_limit_requests,
            rate_limit_window_seconds=settings.rate_limit_window_seconds,
            path_predicate=is_admin_path,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not (
            is_admin_path(scope["path"]) or is_admin_path(get_route_path(scope))
        ):
            await self.app(scope, receive, send)
            return

        async def reject(status: int, code: str, message: str) -> None:
            response = JSONResponse(
                {"code": code, "message": message}, status_code=status,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)

        request = Request(scope)
        try:
            settings = request.app.state.settings
            if _single_header(scope, b"host") != settings.admin_host.encode("ascii"):
                await reject(403, "ADMIN_FORBIDDEN", "관리자 접근이 허용되지 않습니다")
                return
            # Overrides exist only in Python test code, never configuration or
            # inbound request data. Host and CSRF remain mandatory under override.
            resolver = request.app.dependency_overrides.get(resolve_admin_context, resolve_admin_context)
            context = resolver(request)
            if inspect.isawaitable(context):
                context = await context
            if type(context) is not AdminContext or not context.verified:
                await reject(403, "ADMIN_FORBIDDEN", "관리자 접근이 허용되지 않습니다")
                return
            request.state.admin_context = context
            if scope["method"] not in ("GET", "HEAD", "OPTIONS") and not _valid_csrf(request):
                await reject(403, "CSRF_REJECTED", "요청 출처 또는 CSRF 토큰을 확인해 주세요")
                return
        except Exception:
            await reject(500, "INTERNAL_ERROR", "요청을 처리할 수 없습니다")
            return
        await self.protected_app(scope, receive, send)
