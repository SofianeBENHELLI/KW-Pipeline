"""Public error envelope for the Harvester API (issues #120 / #97).

Every error response carries:

* ``error.code`` — stable machine-readable identifier. Codes specific
  to KW Pipeline are prefixed ``KW_`` (e.g. ``KW_UPLOAD_EMPTY``); the
  generic fallbacks (``KW_NOT_FOUND``, ``KW_HTTP_ERROR``) are derived
  from HTTP status when a raise site doesn't pick a more specific
  one.
* ``error.message`` — short user-facing summary.
* ``error.status`` — HTTP status (mirrors the response status; here
  for clients that read the body without inspecting headers).
* ``error.retryable`` — boolean. ``True`` when the same request might
  succeed if retried (e.g. transient backend, rate-limit). ``False``
  for permanent errors (validation, lookup, lifecycle conflict).
  Frontends use this to decide whether to surface a Retry button.
* ``error.remediation`` — optional actionable hint. ``null`` when no
  hint applies. Frontends render this in their notice banners after
  the message.

The legacy ``detail`` field is preserved alongside the envelope so
older clients/tests reading FastAPI's default error shape keep
working (issue #120).
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorCode:
    """Closed catalog of stable error codes (issue #97).

    Adding a new code is a public-API change — document it in
    ``docs/architecture/api_contract.md`` and add a regression test
    to ``tests/test_error_contract.py`` that pins the (status, code,
    retryable, remediation) tuple. Removing or renaming a code is a
    breaking change.
    """

    # ─── Upload (POST /documents/upload) ──────────────────────────
    UPLOAD_EMPTY = "KW_UPLOAD_EMPTY"
    UPLOAD_TOO_LARGE = "KW_UPLOAD_TOO_LARGE"
    UPLOAD_UNSUPPORTED_TYPE = "KW_UPLOAD_UNSUPPORTED_TYPE"

    # ─── Lifecycle FSM (validate / reject / extract / generate) ───
    LIFECYCLE_CONFLICT = "KW_LIFECYCLE_CONFLICT"

    # ─── Idempotency (POST routes with Idempotency-Key) ───────────
    IDEMPOTENCY_REPLAY = "KW_IDEMPOTENCY_REPLAY"

    # ─── Phase 3 vector RAG (GET /knowledge/search) ────────────────
    VECTOR_SEARCH_DISABLED = "KW_VECTOR_SEARCH_DISABLED"

    # ─── Phase 3 grounded chat (POST /knowledge/chat) ──────────────
    CHAT_DISABLED = "KW_CHAT_DISABLED"

    # ─── HITL auto-promotion (POST /admin/hitl/run_auto_promote_pass) ─
    HITL_DISABLED = "KW_HITL_DISABLED"

    # ─── Generic fallbacks (status-derived) ───────────────────────
    BAD_REQUEST = "KW_BAD_REQUEST"
    UNAUTHORIZED = "KW_UNAUTHORIZED"
    FORBIDDEN = "KW_FORBIDDEN"
    NOT_FOUND = "KW_NOT_FOUND"
    CONFLICT = "KW_CONFLICT"
    PAYLOAD_TOO_LARGE = "KW_PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "KW_UNSUPPORTED_MEDIA_TYPE"
    UNPROCESSABLE_ENTITY = "KW_UNPROCESSABLE_ENTITY"
    HTTP_ERROR = "KW_HTTP_ERROR"
    VALIDATION_ERROR = "KW_VALIDATION_ERROR"


_STATUS_FALLBACK_CODES: dict[int, str] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: ErrorCode.UNPROCESSABLE_ENTITY,
}


class ApiError(StarletteHTTPException):
    """HTTP error with a stable public error code, retryable flag, and
    optional remediation hint.

    Use this at raise sites where a specific code is meaningful for the
    frontend (e.g. an upload-empty error needs different remediation
    copy than a lifecycle conflict). For generic 404/409s where the
    HTTP status carries enough information, ``HTTPException`` still
    works — the global handler falls back to a status-derived code.
    """

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        remediation: str | None = None,
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
        self.retryable = retryable
        self.remediation = remediation


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
                retryable=exc.retryable,
                remediation=exc.remediation,
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
            code=ErrorCode.VALIDATION_ERROR,
            message="Request validation failed.",
            retryable=False,
            remediation=(
                "Inspect `detail` for the list of fields that failed "
                "validation and re-send the request with corrected values."
            ),
        )


def _json_error_response(
    *,
    status_code: int,
    detail: Any,
    code: str | None = None,
    message: str | None = None,
    retryable: bool = False,
    remediation: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    message = message or _message_from_detail(status_code=status_code, detail=detail)
    payload = {
        "error": {
            "code": code or _STATUS_FALLBACK_CODES.get(status_code, ErrorCode.HTTP_ERROR),
            "message": message,
            "status": status_code,
            "retryable": retryable,
            "remediation": remediation,
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
