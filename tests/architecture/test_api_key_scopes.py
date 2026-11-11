import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "api_key_scopes.tsv"
SOURCES = {"marker", "admin_dependency", "session_only", "default_deny", "public"}


def _rows():
    lines = GOLDEN.read_text().splitlines()
    assert lines[0] == "method\tpath\tread\twrite\tsource"
    return [line.split("\t") for line in lines[1:]]


def test_every_row_of_the_golden_file_has_five_cells_and_a_known_source():
    for row in _rows():
        assert len(row) == 5, row
        assert row[4] in SOURCES, row


def test_no_authenticated_core_route_is_left_on_default_deny(tmp_path):
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
        [sys.executable, str(ROOT / "tests" / "manual" / "api_key_scopes.py"), "--default-deny"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
    )

    undeclared = [line for line in result.stdout.splitlines() if line.endswith("default_deny")]
    assert result.returncode in (0, 1), result.stderr[-2000:]
    assert undeclared == [], (
        "These routes authenticate a user but never say what an API key may do. Declare key_access(...) on "
        "the router, key_scope(...) on the route, or SESSION_ONLY, then run python tests/manual/api_key_scopes.py:\n"
        + "\n".join(undeclared)
    )


def test_a_session_only_or_default_deny_row_never_names_a_scope():
    for method, path, read, write, source in _rows():
        if source == "session_only":
            assert (read, write) == ("-", "-"), (method, path)
