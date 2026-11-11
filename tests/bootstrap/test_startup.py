import pytest

from src.bootstrap.startup import create_or_explain


def test_a_working_factory_returns_its_app():
    sentinel = object()

    assert create_or_explain(lambda: sentinel) is sentinel


def test_a_failing_factory_ends_with_one_readable_line_after_the_traceback_and_a_failing_exit(capsys):
    def broken():
        raise RuntimeError("database is locked")

    with pytest.raises(SystemExit) as stopped:
        create_or_explain(broken)

    lines = capsys.readouterr().err.rstrip("\n").splitlines()
    assert stopped.value.code == 1
    assert lines[-1] == "PotionUI could not start: RuntimeError: database is locked"
    assert any("Traceback" in line for line in lines[:-1])
    assert sum("PotionUI could not start" in line for line in lines) == 1
