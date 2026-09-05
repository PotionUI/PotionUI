"""HTTP middleware stack for the FastAPI app.

`register_middleware(app)` adds the middleware in a load-bearing order:
Starlette applies them outermost-last, so the registration sequence here
determines the request path. Request metadata is logged either way; DEBUG mode
only raises it from DEBUG to INFO and adds a redacted JSON request body.
"""

import json
import logging
import os
import time
import uuid
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

from src.platform.security.redaction import SECRET_MASK, is_secret_key, redact_mapping

# Get DEBUG mode from environment variable
DEBUG_MODE = os.getenv('DEBUG', 'false').lower() in ('true', '1', 'yes')


# GZip middleware that skips streaming endpoints to prevent SSE buffering.
# GZipMiddleware buffers the full response body before compressing, which
# breaks text/event-stream responses (events arrive all at once instead of
# incrementally). This wrapper bypasses gzip for known streaming paths.
class SSEAwareGZipMiddleware:
    def __init__(self, app, minimum_size: int = 1000, compresslevel: int = 9):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size, compresslevel=compresslevel)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").endswith("/messages/stream"):
            await self.app(scope, receive, send)
        else:
            await self.gzip(scope, receive, send)


# Hard cap on how much of a request body we log, in characters.
_MAX_LOGGED_BODY = 2048

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

# Every body on these routes is a credential in some shape - a form login, a
# JSON password change, a token exchange - so none of them is ever logged.
_CREDENTIAL_ROUTE_PREFIX = "/api/auth"


def _redact_query_string(query_params) -> str:
    """The query string with credential-shaped values masked.

    `multi_items()` rather than the mapping view: a repeated key must be masked
    in every occurrence, not just the last one that wins.
    """
    return urlencode([
        (key, SECRET_MASK if is_secret_key(key) and value else value)
        for key, value in query_params.multi_items()
    ])


def _body_skip_reason(path: str, content_type: str) -> str | None:
    """Why this body must not be logged, or None if a redacted copy may be.

    Only structured JSON can be masked key by key. A form-encoded or multipart
    body is a flat blob in which `password=` is indistinguishable from any other
    field, so it is described rather than echoed.
    """
    if path == _CREDENTIAL_ROUTE_PREFIX or path.startswith(_CREDENTIAL_ROUTE_PREFIX + "/"):
        return "credential route"
    media_type = content_type.split(";", 1)[0].strip().lower()
    if not media_type:
        return "no content type"
    if media_type == "application/json" or media_type.endswith("+json"):
        return None
    return media_type


# Create request/response logging middleware
class LoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.logger = logging.getLogger("api.requests")

    def _log_metadata(self, message: str) -> None:
        self.logger.log(logging.INFO if DEBUG_MODE else logging.DEBUG, message)

    async def _log_body(self, request: Request, request_id: str) -> None:
        reason = _body_skip_reason(request.url.path, request.headers.get("content-type", ""))
        if reason is not None:
            self.logger.info(f"Request Body [{request_id}]: <redacted: {reason}>")
            return

        try:
            body = await request.body()
        except Exception as e:
            self.logger.warning(f"Failed to read request body [{request_id}]: {str(e)}")
            return
        if not body:
            return

        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # The content type promised JSON and it isn't; nothing here can be
            # masked by key, so describe it instead of echoing it.
            self.logger.info(f"Request Body [{request_id}]: <redacted: malformed JSON>")
            return

        redacted = json.dumps(redact_mapping(parsed))
        if len(redacted) > _MAX_LOGGED_BODY:
            redacted = redacted[:_MAX_LOGGED_BODY] + "... (truncated)"
        self.logger.info(f"Request Body [{request_id}]: {redacted}")

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        request_id = str(uuid.uuid4())
        request_method = request.method
        request_path = request.url.path
        client_host = request.client.host if request.client else "unknown"
        content_type = request.headers.get("content-type", "-")
        content_length = request.headers.get("content-length", "-")

        self._log_metadata(
            f"Request [{request_id}]: {request_method} {request_path} from {client_host}"
            f" - query={_redact_query_string(request.query_params)}"
            f" content-type={content_type} content-length={content_length}"
        )

        if DEBUG_MODE and request_method in _BODY_METHODS:
            await self._log_body(request, request_id)

        # Process request
        try:
            response = await call_next(request)

            # Calculate processing time
            process_time = time.time() - start_time

            self._log_metadata(
                f"Response [{request_id}]: {response.status_code} - Completed in {process_time:.4f}s"
            )

            return response
        except Exception as e:
            # ALWAYS log exceptions regardless of DEBUG mode
            process_time = time.time() - start_time
            self.logger.error(
                f"Error [{request_id}]: {request_method} {request_path} - {str(e)}"
                f" - Failed after {process_time:.4f}s"
            )
            raise


async def _add_swagger_headers(request: Request, call_next):
    response = await call_next(request)
    # Check if the request is for Swagger UI or ReDoc
    if request.url.path in ["/docs", "/redoc", "/openapi.json"]:
        # Add CORS headers for Swagger UI access
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
    return response


def register_middleware(app: FastAPI) -> None:
    """Add the middleware stack to `app`, preserving the original order."""
    # Add middleware for better performance and concurrency
    app.add_middleware(SSEAwareGZipMiddleware, minimum_size=1000)  # Compress responses > 1KB, skip SSE

    # Add CORS middleware for frontend
    _default_origins = ["http://localhost:3005", "http://127.0.0.1:3005", "http://localhost:7681", "http://127.0.0.1:7681"]
    _allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").split(",") if os.environ.get("ALLOWED_ORIGINS") else _default_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add middleware to serve Swagger UI and API docs
    app.middleware("http")(_add_swagger_headers)

    # Add logging middleware
    app.add_middleware(LoggingMiddleware)
