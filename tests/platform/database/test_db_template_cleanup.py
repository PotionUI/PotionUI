import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.db_template import _remove_template_directory

REPO_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "potionui-test-db-template-"
HOLDER = (
    "import sys\n"
    "from tests.fixtures.db_template import template_db_path\n"
    "print(template_db_path(), flush=True)\n"
    "sys.stdin.readline()\n"
)


def _environment(tmp_path):
    temp_root = tmp_path / "temp"
    temp_root.mkdir(exist_ok=True)
    environment = dict(os.environ)
    environment.pop("PYTEST_CURRENT_TEST", None)
    environment.pop("PYTEST_XDIST_WORKER", None)
    for name in ("TMPDIR", "TEMP", "TMP"):
        environment[name] = str(temp_root)
    environment["POTIONUI_DB_PATH"] = str(tmp_path / "unused" / "db.sqlite")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return temp_root, environment


def _start_holder(environment):
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", HOLDER],
        cwd=REPO_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    line = process.stdout.readline().strip()
    assert line, "the holder process printed no template path"
    return process, Path(line)


def _finish(process):
    process.communicate("\n", timeout=60)
    assert process.returncode == 0


def _template_directories(temp_root):
    return sorted(path.name for path in temp_root.iterdir() if path.name.startswith(PREFIX))


def test_the_template_directory_is_removed_when_its_process_ends(tmp_path):
    temp_root, environment = _environment(tmp_path)
    process, template = _start_holder(environment)
    try:
        assert template.is_file()
        assert template.parent.parent == temp_root
        assert _template_directories(temp_root) == [template.parent.name]
    finally:
        _finish(process)

    assert not template.parent.exists()
    assert _template_directories(temp_root) == []


def test_a_process_ending_leaves_the_template_of_a_parallel_process(tmp_path):
    temp_root, environment = _environment(tmp_path)
    first, first_template = _start_holder(environment)
    try:
        second, second_template = _start_holder(environment)
    except BaseException:
        _finish(first)
        raise
    try:
        assert first_template.parent != second_template.parent
        _finish(first)

        assert not first_template.parent.exists()
        assert second_template.is_file()
        connection = sqlite3.connect(second_template)
        try:
            tables = connection.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
        finally:
            connection.close()
        assert tables > 0
    finally:
        if second.returncode is None:
            _finish(second)

    assert _template_directories(temp_root) == []


def test_another_process_never_removes_the_directory(tmp_path):
    directory = tmp_path / f"{PREFIX}kept"
    directory.mkdir()
    (directory / "template.sqlite").write_bytes(b"")

    _remove_template_directory(directory, os.getpid() + 1)

    assert (directory / "template.sqlite").is_file()


def test_the_owning_process_removes_the_directory(tmp_path):
    directory = tmp_path / f"{PREFIX}owned"
    directory.mkdir()
    (directory / "template.sqlite").write_bytes(b"")

    _remove_template_directory(directory, os.getpid())

    assert not directory.exists()


def test_a_missing_directory_is_not_an_error(tmp_path):
    _remove_template_directory(tmp_path / f"{PREFIX}gone", os.getpid())


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_a_forked_child_leaves_the_parent_template(tmp_path):
    temp_root, environment = _environment(tmp_path)
    script = (
        "import os, sys\n"
        "from tests.fixtures.db_template import template_db_path\n"
        "template = template_db_path()\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    sys.exit(0)\n"
        "os.waitpid(pid, 0)\n"
        "print(template, template.is_file(), flush=True)\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    path, present = result.stdout.split()
    assert present == "True"
    assert not Path(path).parent.exists()
    assert _template_directories(temp_root) == []
