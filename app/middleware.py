"""Bounded public API requests, app-wide throttling and private error responses."""

from collections.abc import Callable
from math import ceil
from threading import Lock
import time

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def is_public_api_path(path: str) -> bool:
    return any(path == root or path.startswith(root + "/") for root in (
        "/api/calendar", "/api/reservations",
    ))


class PublicAPIMiddleware:
    """One bounded fixed-window counter per app, never per caller-supplied IP.

    The deployment contract uses a single worker. Multiple processes and process
    restarts do not share this counter; the edge proxy needs its own limits.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        rate_limit_requests: int,
        rate_limit_window_seconds: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.rate_limit_requests = rate_limit_requests
        self.rate_limit_window_seconds = rate_limit_window_seconds
        self.monotonic = monotonic
        self._window_start: float | None = None
        self._request_count = 0
        # Test clients may call one app from independent threads/event loops.
        self._lock = Lock()

    def _retry_after(self) -> int | None:
        with self._lock:
            now = self.monotonic()
            if self._window_start is None or now - self._window_start >= self.rate_limit_window_seconds:
                self._window_start = now
                self._request_count = 0
            if self._request_count >= self.rate_limit_requests:
                return max(1, ceil(self.rate_limit_window_seconds - (now - self._window_start)))
            self._request_count += 1
            return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_public_api_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        started = False

        async def private_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message = dict(message)
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        async def reject(status: int, code: str, message: str, headers=None) -> None:
            response = JSONResponse(
                {"code": code, "message": message}, status_code=status, headers=headers,
            )
            await response(scope, receive, private_send)

        retry_after = self._retry_after()
        if retry_after is not None:
            await reject(
                429, "RATE_LIMITED", "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요",
                {"Retry-After": str(retry_after)},
            )
            return

        lengths = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
        if lengths:
            # Reject ambiguous transport metadata without echoing supplied bytes.
            if len(lengths) != 1 or not lengths[0] or not lengths[0].isdigit():
                await reject(400, "INVALID_REQUEST", "올바른 요청이 아닙니다")
                return
            normalized = lengths[0].lstrip(b"0") or b"0"
            maximum = str(self.max_body_bytes).encode("ascii")
            if len(normalized) > len(maximum) or (len(normalized) == len(maximum) and normalized > maximum):
                await reject(413, "REQUEST_TOO_LARGE", "요청 본문이 너무 큽니다")
                return

        # Read before routing, including chunked bodies and requests with a false
        # small Content-Length. The buffer never grows past the configured bound.
        body = bytearray()
        while True:
            incoming = await receive()
            if incoming["type"] == "http.disconnect":
                return
            chunk = incoming.get("body", b"")
            if len(body) + len(chunk) > self.max_body_bytes:
                await reject(413, "REQUEST_TOO_LARGE", "요청 본문이 너무 큽니다")
                return
            body.extend(chunk)
            if not incoming.get("more_body", False):
                break

        delivered = False

        async def replay_body() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        try:
            await self.app(scope, replay_body, private_send)
        except Exception:
            if started:
                # A second response would corrupt the protocol. Preserve the
                # failure, but not a potentially sensitive exception message.
                raise RuntimeError("Public API response failed") from None
            await reject(500, "INTERNAL_ERROR", "요청을 처리할 수 없습니다")
