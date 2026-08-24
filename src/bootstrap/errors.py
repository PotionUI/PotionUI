"""Global exception handlers for the FastAPI app.

These sanitize error responses so internals (tracebacks, paths, echoed request
bodies) never reach the client; the full context is logged server-side under a
correlation id, which is the only detail the client receives.
"""

import logging
import traceback
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def global_exception_handler(request: Request, exc: Exception):
    """Handle all unhandled exceptions without leaking internals to the client.

    The full traceback and request context are logged server-side under a
    correlation id; the client only receives that id, so a support request can
    be tied back to the log entry without exposing stack traces or paths.
    """
    correlation_id = uuid.uuid4().hex

    # Log the full exception server-side, keyed by the correlation id.
    logging.error(
        f"Unhandled exception [{correlation_id}] in {request.method} {request.url.path}: {str(exc)}"
    )
    logging.error(f"Full traceback [{correlation_id}]:\n{traceback.format_exc()}")

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "internal_error",
            "message": "Internal server error",
            "correlation_id": correlation_id,
        },
    )


def _sanitize_validation_errors(errors):
    """Drop the echoed input/context from validation errors.

    Pydantic includes the offending 'input' value (and sometimes 'ctx') in each
    error dict; echoing those back reflects raw request data (potentially
    secrets) to the client. Keep only the location, message and type.
    """
    sanitized = []
    for err in errors:
        if isinstance(err, dict):
            sanitized.append({k: v for k, v in err.items() if k not in ("input", "ctx")})
        else:
            sanitized.append(err)
    return sanitized


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors without echoing the request body back."""

    error_detail = {
        "success": False,
        "error": "validation_error",
        "message": "Request validation failed",
        "detail": _sanitize_validation_errors(exc.errors()),
    }

    # Log validation errors (server-side only).
    logging.warning(f"Validation error in {request.method} {request.url.path}: {exc.errors()}")

    return JSONResponse(
        status_code=422,
        content=error_detail
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach the sanitizing exception handlers to `app`."""
    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
