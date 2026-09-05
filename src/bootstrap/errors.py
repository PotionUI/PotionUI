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

from src.platform.security.redaction import is_secret_key


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


# Stands in for a validator message that named a credential field. The client
# already knows the value it sent; the log must not learn it.
_WITHHELD_MESSAGE = "value rejected (message withheld: credential field)"


def _loggable_validation_errors(errors):
    """The sanitized errors, with messages about credential fields withheld.

    Dropping `input`/`ctx` is not enough for the log: a field validator writes
    its own message and may interpolate the offending value into it
    (`ValueError(f"weak password: {value}")`), so for a field whose name looks
    like a credential the message itself is not safe to write down either.
    """
    loggable = []
    for err in _sanitize_validation_errors(errors):
        if isinstance(err, dict) and any(
            is_secret_key(part) for part in err.get("loc", ()) if isinstance(part, str)
        ):
            err = {**err, "msg": _WITHHELD_MESSAGE}
        loggable.append(err)
    return loggable


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors without echoing the request body back."""

    error_detail = {
        "success": False,
        "error": "validation_error",
        "message": "Request validation failed",
        "detail": _sanitize_validation_errors(exc.errors()),
    }

    # Log validation errors (server-side only). The raw errors carry the
    # offending `input` value, so the log gets the sanitized list as well.
    logging.warning(
        f"Validation error in {request.method} {request.url.path}: "
        f"{_loggable_validation_errors(exc.errors())}"
    )

    return JSONResponse(
        status_code=422,
        content=error_detail
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach the sanitizing exception handlers to `app`."""
    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
