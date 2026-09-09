"""The entry point configures logging once, and error logs are masked."""

import ast
import json
import logging
from pathlib import Path

from starlette.requests import Request

from src.bootstrap import errors
from src.bootstrap.errors import global_exception_handler
from src.platform.security.redaction import SECRET_MASK

API_ENTRY_POINT = Path(__file__).resolve().parents[2] / "api.py"


def _entry_point_tree() -> ast.Module:
    return ast.parse(API_ENTRY_POINT.read_text(encoding="utf-8"))


def _called_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def test_entry_point_does_not_configure_logging_a_second_time():
    """`basicConfig` in the entry point would fork the format between the two
    ways of starting the server: `python api.py` ran it, `uvicorn api:app` did
    not."""
    assert "basicConfig" not in _called_names(_entry_point_tree())


def test_entry_point_calls_configure_logging_at_module_level():
    tree = _entry_point_tree()

    module_level_calls = {
        node.value.func.id
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    }

    assert "configure_logging" in module_level_calls


def test_entry_point_keeps_uvicorn_from_reconfiguring_logging():
    """`uvicorn.run` applies its own dictConfig unless told not to, which would
    take the uvicorn loggers back off the root handlers."""
    source = API_ENTRY_POINT.read_text(encoding="utf-8")

    assert "log_config=None" in source


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/generation/generate",
            "headers": [],
            "query_string": b"",
        }
    )


async def test_unhandled_exception_log_masks_the_message(caplog):
    with caplog.at_level(logging.ERROR):
        try:
            raise RuntimeError("upstream rejected Authorization: Bearer sk-live-9f2c")
        except RuntimeError as exc:
            await global_exception_handler(_request(), exc)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "sk-live-9f2c" not in logged
    assert SECRET_MASK in logged
    assert "/api/generation/generate" in logged


async def test_unhandled_exception_log_masks_the_traceback(monkeypatch, caplog):
    monkeypatch.setattr(
        errors.traceback,
        "format_exc",
        lambda: 'Traceback:\n  File "x.py", line 1\nRuntimeError: sent api_key=sk-live-9f2c',
    )

    with caplog.at_level(logging.ERROR):
        await global_exception_handler(_request(), RuntimeError("boom"))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "sk-live-9f2c" not in logged
    assert f"api_key={SECRET_MASK}" in logged
    assert 'File "x.py", line 1' in logged


async def test_the_correlation_id_ties_the_response_to_the_log(caplog):
    with caplog.at_level(logging.ERROR):
        response = await global_exception_handler(_request(), RuntimeError("boom"))

    correlation_id = json.loads(response.body)["correlation_id"]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert f"Unhandled exception [{correlation_id}]" in logged
    assert f"Full traceback [{correlation_id}]" in logged
