"""Shared API error envelope and request-id helpers (minimal API contract §14)."""

import logging
import re
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("opsbrain.api.errors")

_SAFE_CODE_PATTERN = re.compile(r"^[a-z0-9_]+$")
_SAFE_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

_ERROR_MESSAGES: dict[str, str] = {
    "authentication_required": "Authentication is required.",
    "authentication_unavailable": "Authentication service is temporarily unavailable.",
    "query_forbidden": "Principal lacks query:execute capability.",
    "approval_forbidden": "Principal lacks document:approve capability.",
    "ingestion_forbidden": "Principal lacks document:ingest capability.",
    "reindex_forbidden": "Principal lacks index:rebuild capability.",
    "document_not_found": "Document was not found.",
    "ingestion_job_not_found": "Ingestion job was not found.",
    "reindex_job_not_found": "Reindex job was not found.",
    "idempotency_key_required": "Idempotency-Key header is required.",
    "idempotency_conflict": "Idempotency-Key was reused with a different request payload.",
    "version_conflict": "Document version does not match expected_version.",
    "invalid_review_transition": "Document version is not in review.",
    "independent_review_required": "Author cannot review their own document version.",
    "retrieval_unavailable": "Retrieval configuration is unavailable.",
    "embedding_unavailable": "Embedding service is temporarily unavailable.",
    "generation_not_implemented": "Answer generation is not implemented.",
    "unsupported_retrieval_strategy": "Requested retrieval strategy is not implemented yet.",
    "context_document_not_found": "Active context document was not found.",
    "configuration_not_found": "Active derived configuration was not found.",
    "targets_required": "At least one reindex target is required.",
    "invalid_target": "Requested reindex target is not supported.",
    "no_documents": "No eligible documents matched the request.",
    "current_version_missing": "Document current version is missing.",
    "validation_failed": "Request validation failed.",
    "internal_error": "An internal error occurred.",
}


def resolve_request_id(request: Request) -> str:
    """Return existing request_id from request.state, header, or generate one."""
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    header_value = request.headers.get("X-Request-Id", "")
    if header_value and _SAFE_REQUEST_ID_PATTERN.fullmatch(header_value):
        request.state.request_id = header_value
        return header_value
    generated = str(uuid4())
    request.state.request_id = generated
    return generated


def _format_loc(loc: tuple[Any, ...]) -> str:
    parts = [part for idx, part in enumerate(loc) if not (idx == 0 and part in {"body", "query", "path", "header"})]
    if not parts:
        parts = list(loc)
    formatted: list[str] = []
    for part in parts:
        if isinstance(part, int):
            if formatted:
                formatted[-1] = f"{formatted[-1]}[{part}]"
            else:
                formatted.append(f"[{part}]")
        else:
            formatted.append(str(part))
    return ".".join(formatted) or "request"


def _error_payload(
    *,
    request_id: str,
    code: str,
    message: str,
    retryable: bool,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details if details is not None else [],
        },
    }


def _http_code_and_message(exc: StarletteHTTPException) -> tuple[str, str]:
    detail = exc.detail
    if isinstance(detail, str) and _SAFE_CODE_PATTERN.fullmatch(detail):
        message = _ERROR_MESSAGES.get(
            detail, f"{detail.replace('_', ' ').capitalize()}."
        )
        return detail, message
    default_codes = {
        401: "authentication_required",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        422: "validation_failed",
        503: "service_unavailable",
    }
    code = default_codes.get(exc.status_code, f"http_{exc.status_code}")
    return code, _ERROR_MESSAGES.get(code, "Request failed.")


def register_error_handlers(app: FastAPI) -> None:
    """Attach request_id middleware and contract-compliant error handlers."""

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = resolve_request_id(request)
        response = await call_next(request)
        response.headers.setdefault("X-Request-Id", request_id)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        request_id = resolve_request_id(request)
        code, message = _http_code_and_message(exc)
        retryable = exc.status_code in {429, 503}
        headers = dict(exc.headers) if exc.headers else {}
        headers.setdefault("X-Request-Id", request_id)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(
                request_id=request_id,
                code=code,
                message=message,
                retryable=retryable,
            ),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        request_id = resolve_request_id(request)
        details: list[dict[str, Any]] = []
        for err in exc.errors():
            # Never copy err["input"], raw ctx values, or custom validator text.
            err_type = str(err.get("type", "invalid_value"))
            msg = (
                "Value failed validation."
                if err_type.startswith("value_error")
                else str(err.get("msg", "Invalid value"))
            )
            details.append(
                {
                    "code": err_type,
                    "field": _format_loc(tuple(err.get("loc", ()))),
                    "message": msg,
                }
            )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_payload(
                request_id=request_id,
                code="validation_failed",
                message=_ERROR_MESSAGES["validation_failed"],
                retryable=False,
                details=details,
            ),
            headers={"X-Request-Id": request_id},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = resolve_request_id(request)
        logger.exception("Unhandled exception [request_id=%s]", request_id)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_payload(
                request_id=request_id,
                code="internal_error",
                message=_ERROR_MESSAGES["internal_error"],
                retryable=False,
            ),
            headers={"X-Request-Id": request_id},
        )
