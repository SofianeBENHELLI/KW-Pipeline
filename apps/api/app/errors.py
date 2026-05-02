from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(StarletteHTTPException):
    """HTTP error with a stable public error code."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        detail: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail=message if detail is None else detail,
            headers=headers,
        )
        self.code = code
        self.message = message


_STATUS_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "UNPROCESSABLE_ENTITY",
}


def install_error_handlers(app: FastAPI) -> None:
    """Install the public API error envelope while preserving legacy detail."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if isinstance(exc, ApiError):
            return _json_error_response(
                status_code=exc.status_code,
                detail=exc.detail,
                code=exc.code,
                message=exc.message,
                headers=exc.headers,
            )
        return _json_error_response(
            status_code=exc.status_code,
            detail=exc.detail,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _json_error_response(
            status_code=422,
            detail=jsonable_encoder(exc.errors()),
            code="VALIDATION_ERROR",
            message="Request validation failed.",
        )


def _json_error_response(
    *,
    status_code: int,
    detail: Any,
    code: str | None = None,
    message: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    message = message or _message_from_detail(status_code=status_code, detail=detail)
    payload = {
        "error": {
            "code": code or _STATUS_ERROR_CODES.get(status_code, "HTTP_ERROR"),
            "message": message,
            "status": status_code,
        },
        # Backward-compatible field for existing clients/tests that still read
        # FastAPI's default error shape.
        "detail": detail,
    }
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(payload),
        headers=dict(headers) if headers is not None else None,
    )


def _message_from_detail(*, status_code: int, detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        for key in ("message", "detail", "error"):
            value = detail.get(key)
            if isinstance(value, str):
                return value
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP error"
