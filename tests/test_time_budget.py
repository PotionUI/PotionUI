import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import time_budget

REPO_ROOT = Path(__file__).resolve().parent.parent

SLOW_TEST = textwrap.dedent(
    """
    import time

    def test_slow():
        time.sleep(0.6)

    def test_fast():
        pass

    def test_slow_and_failing():
        time.sleep(0.6)
        assert False, "original failure"
    """
)


def run_pytest(tmp_path, budget):
    (tmp_path / "test_sample.py").write_text(SLOW_TEST)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    env = {k: v for k, v in os.environ.items() if k != time_budget.BUDGET_ENV}
    inherited = [str(Path(entry).resolve()) for entry in env.get("PYTHONPATH", "").split(os.pathsep) if entry]
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), *inherited])
    if budget is not None:
        env[time_budget.BUDGET_ENV] = budget
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "time_budget", "-p", "no:cacheprovider", "-q", "--no-header", "test_sample.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )


class TestBudgetPlugin:
    def test_slow_test_fails_naming_test_and_duration(self, tmp_path):
        result = run_pytest(tmp_path, "0.3")
        out = result.stdout
        assert result.returncode != 0
        assert "test_sample.py::test_slow took" in out
        assert "over the 0.3s per-test budget" in out
        assert "fakes" in out and "tiny tensors" in out
        assert "2 failed, 1 passed" in out
        assert "AssertionError: original failure" in out

    def test_disabled_when_env_unset(self, tmp_path):
        result = run_pytest(tmp_path, None)
        assert "1 failed, 2 passed" in result.stdout
        assert "per-test budget" not in result.stdout

    def test_disabled_when_env_zero_or_garbage(self, tmp_path):
        for value in ("0", "abc", "-5"):
            assert "per-test budget" not in run_pytest(tmp_path, value).stdout

    def test_generous_budget_passes_slow_test(self, tmp_path):
        result = run_pytest(tmp_path, "30")
        assert "per-test budget" not in result.stdout
        assert "1 failed, 2 passed" in result.stdout


class TestReadBudget:
    @pytest.mark.parametrize(
        "raw,expected",
        [("10", 10.0), ("2.5", 2.5), ("", 0.0), ("0", 0.0), ("-1", 0.0), ("x", 0.0)],
    )
    def test_parses(self, raw, expected):
        assert time_budget.read_budget({time_budget.BUDGET_ENV: raw}) == expected

    def test_missing_is_zero(self):
        assert time_budget.read_budget({}) == 0.0
