"""HTTP middleware stack for the FastAPI app.

`register_middleware(app)` adds the middleware in a load-bearing order:
Starlette applies them outermost-last, so the registration sequence here
determines the request path. Request logging is DEBUG-mode only.
"""

import json
import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

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


# Keys whose values must never be written to the request log (case-insensitive).
_SENSITIVE_LOG_KEYS = frozenset({
    "password", "token", "api_key", "apikey", "authorization", "cookie", "secret",
})
# Hard cap on how much of a request body we log, in bytes/characters.
_MAX_LOGGED_BODY = 2048


def _redact_sensitive(value):
    """Recursively redact sensitive keys in a JSON-like structure for logging."""
    if isinstance(value, dict):
        return {
            k: ("***REDACTED***" if isinstance(k, str) and k.lower() in _SENSITIVE_LOG_KEYS
                else _redact_sensitive(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(v) for v in value]
    return value


# Create request/response logging middleware
class LoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.logger = logging.getLogger("api.requests")

    async def dispatch(self, request: Request, call_next):
        # Track timing
        start_time = time.time()

        # Only log and track requests in DEBUG mode
        if DEBUG_MODE:
            # Generate request ID for tracking
            request_id = str(uuid.uuid4())
            request_path = request.url.path
            request_query = str(request.query_params)
            request_method = request.method
            client_host = request.client.host if request.client else "unknown"

            # Log basic request info
            self.logger.info(f"Request [{request_id}]: {request_method} {request_path} from {client_host} - Query: {request_query}")

            # Log request body for POST, PUT, PATCH requests
            if request_method in ["POST", "PUT", "PATCH"]:
                try:
                    # Clone the request body
                    body = await request.body()
                    # Reconstruct the request with the same body
                    request._body = body

                    # Try to parse as JSON and log
                    try:
                        body_str = body.decode()
                        if body_str:
                            # Try to parse and pretty-print JSON
                            try:
                                json_body = json.loads(body_str)
                                # Redact sensitive fields before logging.
                                redacted = json.dumps(_redact_sensitive(json_body))
                                if len(redacted) > _MAX_LOGGED_BODY:
                                    redacted = redacted[:_MAX_LOGGED_BODY] + "... (truncated)"
                                self.logger.info(f"Request Body [{request_id}]: {redacted}")
                            except json.JSONDecodeError:
                                # Not JSON: log as plain text, capped. We can't
                                # reliably redact unstructured bodies, so keep the
                                # cap tight rather than echo secrets.
                                if len(body_str) > _MAX_LOGGED_BODY:
                                    self.logger.info(f"Request Body [{request_id}]: {body_str[:_MAX_LOGGED_BODY]}... (truncated)")
                                else:
                                    self.logger.info(f"Request Body [{request_id}]: {body_str}")
                    except UnicodeDecodeError:
                        self.logger.info(f"Request Body [{request_id}]: Binary data (not logged)")
                except Exception as e:
                    self.logger.warning(f"Failed to log request body [{request_id}]: {str(e)}")

        # Process request
        try:
            response = await call_next(request)

            # Calculate processing time
            process_time = time.time() - start_time
            status_code = response.status_code

            # Only log successful responses in DEBUG mode
            if DEBUG_MODE:
                self.logger.info(f"Response [{request_id}]: {status_code} - Completed in {process_time:.4f}s")

            return response
        except Exception as e:
            # ALWAYS log exceptions regardless of DEBUG mode
            process_time = time.time() - start_time
            request_method = request.method
            request_path = request.url.path
            request_id = str(uuid.uuid4())  # Generate ID only for error logging
            self.logger.error(f"Error [{request_id}]: {request_method} {request_path} - {str(e)} - Failed after {process_time:.4f}s")
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
    _default_origins = ["http://localhost:3005", "http://127.0.0.1:3005", "http://localhost:3001", "http://127.0.0.1:3001"]
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
