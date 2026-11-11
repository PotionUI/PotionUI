import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "api_key_scopes.tsv"
EXIT_UNDECLARED = 3
SOURCES = {"marker", "admin_dependency", "session_only", "default_deny", "public"}


def _rows():
    lines = GOLDEN.read_bytes().decode("utf-8").replace("\r\n", "\n").splitlines()
    assert lines[0] == "method\tpath\tread\twrite\tsource"
    return [line.split("\t") for line in lines[1:]]


def _undeclared_routes(script, tmp_path):
    env = {
        "PYTHONPATH": f"{ROOT / 'venv/lib/python3.12/site-packages'}{os.pathsep}{ROOT}",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "POTIONUI_DB_PATH": str(tmp_path / "db.sqlite"),
    }
    for name in ("SYSTEMROOT", "TEMP", "TMP", "PATHEXT", "COMSPEC"):
        if name in os.environ:
            env[name] = os.environ[name]

    result = subprocess.run(
        [sys.executable, str(script), "--default-deny"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
    )

    assert result.returncode in (0, EXIT_UNDECLARED), (
        f"the route table script failed with exit code {result.returncode}, so nothing was checked:\n"
        + result.stderr[-2000:]
    )
    return [line for line in result.stdout.splitlines() if line.endswith("default_deny")]


def test_every_row_of_the_golden_file_has_five_cells_and_a_known_source():
    for row in _rows():
        assert len(row) == 5, row
        assert row[4] in SOURCES, row


def test_no_authenticated_core_route_is_left_on_default_deny(tmp_path):
    undeclared = _undeclared_routes(ROOT / "tests" / "manual" / "api_key_scopes.py", tmp_path)

    assert undeclared == [], (
        "These routes authenticate a user but never say what an API key may do. Declare key_access(...) on "
        "the router, key_scope(...) on the route, or SESSION_ONLY, then run python tests/manual/api_key_scopes.py:\n"
        + "\n".join(undeclared)
    )


def test_a_crashing_route_table_script_fails_the_guard_instead_of_passing_it(tmp_path):
    crashing = tmp_path / "crash.py"
    crashing.write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    with pytest.raises(AssertionError) as failed:
        _undeclared_routes(crashing, tmp_path)

    assert "nothing was checked" in str(failed.value) and "boom" in str(failed.value)


def test_a_script_that_exits_cleanly_with_no_output_means_no_undeclared_routes(tmp_path):
    quiet = tmp_path / "quiet.py"
    quiet.write_text("", encoding="utf-8")

    assert _undeclared_routes(quiet, tmp_path) == []


def test_a_session_only_row_never_names_a_scope():
    for method, path, read, write, source in _rows():
        if source == "session_only":
            assert (read, write) == ("-", "-"), (method, path)


def test_a_default_deny_row_never_names_a_scope():
    for method, path, read, write, source in _rows():
        if source == "default_deny":
            assert (read, write) == ("-", "-"), (method, path)


def test_the_golden_file_is_read_the_same_with_windows_line_endings(tmp_path, monkeypatch):
    crlf = tmp_path / "golden.tsv"
    crlf.write_bytes(GOLDEN.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))

    monkeypatch.setattr(sys.modules[__name__], "GOLDEN", crlf)

    assert _rows() == [line.split("\t") for line in GOLDEN.read_bytes().decode("utf-8").replace("\r\n", "\n").splitlines()[1:]]
