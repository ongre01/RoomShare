"""Fixed public error messages. Never serialize validation inputs or exceptions."""

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette._utils import get_route_path

from app.services.cancellation import (
    CancellationNotAllowedError, InvalidCancellationCredentialsError,
)
from app.services.errors import DateConflictError, InvalidInputError, TemporaryDatabaseError
from app.services.admin import AdminChangeNotAllowedError, AdminNotFoundError


def error_response(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"code": code, "message": message, **extra}, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AdminNotFoundError)
    async def admin_not_found(request: Request, error: AdminNotFoundError):
        return error_response(404, "NOT_FOUND", "요청한 대상을 찾을 수 없습니다")

    @app.exception_handler(AdminChangeNotAllowedError)
    async def admin_change_not_allowed(request: Request, error: AdminChangeNotAllowedError):
        return error_response(409, "CHANGE_NOT_ALLOWED", "현재 상태에서는 변경할 수 없습니다")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if any(item["type"] == "json_invalid" for item in error.errors()):
            return error_response(400, "INVALID_JSON", "올바른 JSON 본문이 필요합니다")
        return error_response(422, "INVALID_INPUT", "입력값을 확인해 주세요")

    @app.exception_handler(InvalidInputError)
    async def input_error(request: Request, error: InvalidInputError):
        return error_response(422, "INVALID_INPUT", "입력값을 확인해 주세요")

    @app.exception_handler(DateConflictError)
    async def conflict(request: Request, error: DateConflictError):
        return error_response(409, error.code, "예약할 수 없는 날짜입니다", dates=list(error.dates))

    @app.exception_handler(TemporaryDatabaseError)
    async def busy(request: Request, error: TemporaryDatabaseError):
        return error_response(503, error.code, "잠시 후 다시 시도해 주세요")

    @app.exception_handler(InvalidCancellationCredentialsError)
    async def credentials(request: Request, error: InvalidCancellationCredentialsError):
        return error_response(404, error.code, "예약 정보 또는 취소 토큰을 확인해 주세요")

    @app.exception_handler(CancellationNotAllowedError)
    async def policy(request: Request, error: CancellationNotAllowedError):
        return error_response(409, error.code, "오늘 또는 과거 날짜를 포함한 예약은 취소할 수 없습니다")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        from app.middleware import is_public_api_path
        from app.security import is_admin_path

        if not any(
            is_public_api_path(path) or is_admin_path(path)
            for path in (request.scope["path"], get_route_path(request.scope))
        ):
            return await http_exception_handler(request, error)
        messages = {
            400: ("INVALID_JSON", "올바른 JSON 본문이 필요합니다"),
            404: ("NOT_FOUND", "요청한 경로를 찾을 수 없습니다"),
            405: ("METHOD_NOT_ALLOWED", "허용되지 않은 요청 방식입니다"),
            415: ("UNSUPPORTED_MEDIA_TYPE", "application/json 본문이 필요합니다"),
        }
        code, message = messages.get(error.status_code, ("INTERNAL_ERROR", "요청을 처리할 수 없습니다"))
        response = error_response(error.status_code, code, message)
        if error.status_code == 405 and error.headers and "Allow" in error.headers:
            response.headers["Allow"] = error.headers["Allow"]
        return response
