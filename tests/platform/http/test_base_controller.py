"""Regression coverage for BaseController's exception sanitization.

`handle_exception` is the client-facing boundary for every uncaught exception
a controller method raises deliberately; it must never let exception text
reach the response body, only the server-side log.
"""

import pytest
from fastapi import HTTPException

from src.platform.http.base_controller import BaseController


class _Controller(BaseController):
    pass


@pytest.fixture
def controller():
    return _Controller()


class TestHandleException:
    def test_exception_text_never_reaches_the_client(self, controller):
        with pytest.raises(HTTPException) as exc_info:
            controller.handle_exception(Exception("secret-db-path"), "boom_failed")

        detail = exc_info.value.detail
        assert "secret-db-path" not in str(detail)
        assert exc_info.value.status_code == 500
        assert detail["error"] == "boom_failed"

    def test_a_caller_supplied_message_is_used_verbatim(self, controller):
        with pytest.raises(HTTPException) as exc_info:
            controller.handle_exception(Exception("secret-db-path"), "boom_failed", message="Failed to boom")

        assert exc_info.value.detail["message"] == "Failed to boom"

    def test_no_message_falls_back_to_a_generic_one(self, controller):
        with pytest.raises(HTTPException) as exc_info:
            controller.handle_exception(Exception("secret-db-path"), "boom_failed")

        assert "secret-db-path" not in exc_info.value.detail["message"]

    def test_status_code_is_honored(self, controller):
        with pytest.raises(HTTPException) as exc_info:
            controller.handle_exception(Exception("x"), "boom_failed", status_code=502)

        assert exc_info.value.status_code == 502


class TestErrorResponse:
    def test_plain_control_flow_does_not_log_a_stacktrace(self, controller, caplog):
        """error_response called from ordinary control flow (a 404, a bad
        request) has no exception in flight - logging traceback.format_exc()
        there would print a literal 'NoneType: None'."""
        with pytest.raises(HTTPException):
            controller.error_response(error="not_found", message="missing", status_code=404)

        assert "NoneType: None" not in caplog.text
        assert "Stacktrace" not in caplog.text

    def test_a_stacktrace_is_logged_when_raised_from_within_an_except_block(self, controller, caplog):
        try:
            raise ValueError("boom")
        except ValueError:
            with pytest.raises(HTTPException):
                controller.error_response(error="boom_failed", message="boom", status_code=400)

        assert "Stacktrace" in caplog.text
        assert "ValueError: boom" in caplog.text
