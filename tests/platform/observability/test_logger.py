"""The logging setup: handlers installed once, and a log file that rotates."""

import logging
import logging.handlers
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rich.logging import RichHandler

from src.platform.observability import logger as logger_module
from src.platform.observability.logger import configure_logging

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def root_logger():
    """The root logger, with whatever this test installs on it torn down.

    Handlers pytest itself put on the root (log capture) are left in place;
    only what a test adds is removed again, so no other test inherits a file
    handler pointing at a `tmp_path` that is about to disappear.
    """
    root = logging.getLogger()
    before = list(root.handlers)
    before_level = root.level
    yield root
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()
    root.handlers = before
    root.setLevel(before_level)


def _console_handlers(root):
    return [h for h in root.handlers if isinstance(h, RichHandler)]


def _file_handlers(root):
    return [h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]


@pytest.fixture
def log_env(monkeypatch, tmp_path):
    """Point every log variable at `tmp_path` so no test writes to `storage/`."""
    monkeypatch.setenv("POTIONUI_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("POTIONUI_LOG_FILE", raising=False)
    monkeypatch.delenv("POTIONUI_LOG_MAX_BYTES", raising=False)
    monkeypatch.delenv("POTIONUI_LOG_BACKUP_COUNT", raising=False)
    monkeypatch.setenv("POTIONUI_LOG_LEVEL", "INFO")
    return tmp_path / "logs"


def test_installs_one_console_and_one_file_handler(root_logger, log_env):
    configure_logging()

    assert len(_console_handlers(root_logger)) == 1
    assert len(_file_handlers(root_logger)) == 1
    assert (log_env / "potionui.log").exists()


def test_second_call_adds_nothing(root_logger, log_env):
    configure_logging()
    console = _console_handlers(root_logger)[0]
    file_handler = _file_handlers(root_logger)[0]

    configure_logging()

    assert _console_handlers(root_logger) == [console]
    assert _file_handlers(root_logger) == [file_handler]


def test_log_level_applies_to_both_handlers(root_logger, log_env, monkeypatch):
    monkeypatch.setenv("POTIONUI_LOG_LEVEL", "WARNING")

    configure_logging()

    assert root_logger.level == logging.WARNING
    assert _console_handlers(root_logger)[0].level == logging.WARNING
    assert _file_handlers(root_logger)[0].level == logging.WARNING


def test_uvicorn_loggers_propagate_to_the_root_handlers(root_logger, log_env):
    access = logging.getLogger("uvicorn.access")
    access.addHandler(logging.NullHandler())
    access.propagate = False
    try:
        configure_logging()

        assert access.handlers == []
        assert access.propagate is True
        assert logging.getLogger("uvicorn.error").propagate is True
    finally:
        access.handlers = []
        access.propagate = True


def test_file_line_carries_timestamp_level_logger_and_message(root_logger, log_env):
    configure_logging()

    logging.getLogger("is").warning("cache reclaimed")
    _file_handlers(root_logger)[0].flush()

    line = (log_env / "potionui.log").read_text(encoding="utf-8").splitlines()[-1]
    stamp, level, name, message = (part.strip() for part in line.split("|", 3))
    assert level == "WARNING"
    assert name == "is"
    assert message == "cache reclaimed"
    assert len(stamp) == len("2026-09-09 14:03:11")


def test_file_rotates_and_keeps_only_the_backup_count(root_logger, log_env, monkeypatch):
    monkeypatch.setenv("POTIONUI_LOG_MAX_BYTES", "500")
    monkeypatch.setenv("POTIONUI_LOG_BACKUP_COUNT", "2")

    configure_logging()
    for index in range(200):
        logging.getLogger("is").info(f"line {index} {'x' * 60}")
    _file_handlers(root_logger)[0].flush()

    assert (log_env / "potionui.log").exists()
    assert (log_env / "potionui.log.1").exists()
    assert (log_env / "potionui.log.2").exists()
    assert not (log_env / "potionui.log.3").exists()
    assert (log_env / "potionui.log.1").stat().st_size <= 600


def test_log_file_off_installs_no_file_handler(root_logger, log_env, monkeypatch):
    monkeypatch.setenv("POTIONUI_LOG_FILE", "off")

    configure_logging()

    assert _console_handlers(root_logger)
    assert _file_handlers(root_logger) == []
    assert not log_env.exists()


def test_empty_log_dir_installs_no_file_handler(root_logger, monkeypatch):
    monkeypatch.setenv("POTIONUI_LOG_DIR", "")
    monkeypatch.delenv("POTIONUI_LOG_FILE", raising=False)

    configure_logging()

    assert _file_handlers(root_logger) == []


def test_unwritable_directory_warns_on_the_console_and_carries_on(
    root_logger, tmp_path, monkeypatch, caplog
):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("a file is standing where the log directory should be")
    monkeypatch.setenv("POTIONUI_LOG_DIR", str(blocked))
    monkeypatch.delenv("POTIONUI_LOG_FILE", raising=False)

    with caplog.at_level(logging.WARNING):
        configure_logging()

    assert _file_handlers(root_logger) == []
    assert _console_handlers(root_logger)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "not writable" in warnings[0].getMessage()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_permission_denied_directory_warns_and_carries_on(
    root_logger, tmp_path, monkeypatch, caplog
):
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    read_only.chmod(0o500)
    monkeypatch.setenv("POTIONUI_LOG_DIR", str(read_only))
    monkeypatch.delenv("POTIONUI_LOG_FILE", raising=False)

    try:
        with caplog.at_level(logging.WARNING):
            configure_logging()
    finally:
        read_only.chmod(0o700)

    assert _file_handlers(root_logger) == []
    assert any("not writable" in r.getMessage() for r in caplog.records)


def test_importing_the_module_configures_nothing_and_creates_no_directory(tmp_path):
    """The log directory belongs to app start, not to `import logger`.

    A subprocess, because the module under test is already imported here: this
    has to observe a first import, in a working directory that is not the repo.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "venv/lib/python3.12/site-packages"), str(REPO_ROOT)]
    )
    env.pop("POTIONUI_LOG_DIR", None)
    env.pop("POTIONUI_LOG_FILE", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import logging;"
            "from src.platform.observability.logger import logger;"
            "print(len(logging.getLogger().handlers))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"
    assert not (tmp_path / "storage").exists()


def test_default_directory_is_under_storage(monkeypatch):
    monkeypatch.delenv("POTIONUI_LOG_DIR", raising=False)
    monkeypatch.delenv("POTIONUI_LOG_FILE", raising=False)

    assert logger_module.log_directory() == Path("storage/logs")
