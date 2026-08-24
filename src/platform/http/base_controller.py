from typing import Any, NoReturn, Optional
from fastapi import HTTPException
from pydantic import BaseModel
import sys
import traceback
import logging


class APIResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    message: Optional[str] = None
    error: Optional[str] = None


class APIError(BaseModel):
    success: bool = False
    error: str
    message: Optional[str] = None
    code: Optional[str] = None


class BaseController:
    """Base controller class with common functionality"""

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    def success_response(self, data: Any = None, message: str = None) -> APIResponse:
        """Create a success response"""
        return APIResponse(success=True, data=data, message=message)

    def error_response(self, error: str, message: str = None, status_code: int = 400) -> NoReturn:
        """Raise an HTTPException with a sanitized error body.

        The stack trace is logged server-side only; it is never returned to
        the client.
        """
        error_data = APIError(error=error, message=message)

        # Log the error; the stacktrace only when one is actually in flight -
        # most callers reach this from plain control flow (a 404, a bad
        # request), where format_exc() would otherwise log a literal
        # "NoneType: None".
        self.logger.error(f"API Error: {error} - {message}")
        if sys.exc_info()[0] is not None:
            self.logger.error(f"Stacktrace:\n{traceback.format_exc()}")

        raise HTTPException(status_code=status_code, detail=error_data.model_dump())

    def error_api_response(self, error: str, message: str = None) -> APIResponse:
        """Create an APIResponse with error information (does not raise exception)"""
        # Log the error
        self.logger.error(f"API Error: {error} - {message}")

        return APIResponse(success=False, error=error, message=message)

    def handle_exception(self, e: Exception, error_code: str = "internal_error",
                        message: str = None, status_code: int = 500) -> NoReturn:
        """Handle an exception: log the full traceback server-side, raise a sanitized error.

        `message` is returned to the CLIENT. It must never be built from
        `str(e)` - exception text can carry paths, connection strings or other
        internals. Callers that have no safe message to give should leave it
        None; a generic message is used instead.
        """

        # Log the exception with full traceback (server-side only).
        self.logger.exception(f"Exception in {self.__class__.__name__} ({error_code}): {e}")

        error_data = APIError(
            error=error_code,
            message=message or "An internal error occurred.",
        )

        raise HTTPException(status_code=status_code, detail=error_data.model_dump())
