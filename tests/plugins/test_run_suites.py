import sys
import time
from pathlib import Path
from unittest.mock import patch

_TESTS_PLUGINS_DIR = Path(__file__).resolve().parent
if str(_TESTS_PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_PLUGINS_DIR))

import run_suites  # noqa: E402


class _FakeCompletedProcess:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _make_marketplace(tmp_path, plugin_names):
    marketplace = tmp_path / "content" / "plugins" / "marketplace"
    for name in plugin_names:
        (marketplace / name / "tests").mkdir(parents=True)
    return marketplace


class TestRunSuitesConcurrency:
    def test_all_plugins_run_and_the_right_one_fails(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["run_suites.py"])
        marketplace = _make_marketplace(tmp_path, ["alpha", "bravo", "charlie"])

        outcomes = {"alpha": (0, 0.03), "bravo": (1, 0.01), "charlie": (0, 0.02)}

        def fake_run(cmd, cwd, capture_output, text):
            plugin_name = Path(cmd[3]).parent.name
            returncode, delay = outcomes[plugin_name]
            time.sleep(delay)
            return _FakeCompletedProcess(returncode, stdout=f"ran {plugin_name}")

        with patch.object(run_suites, "MARKETPLACE_ROOT", marketplace), \
                patch.object(run_suites, "REPO_ROOT", tmp_path), \
                patch.object(run_suites.subprocess, "run", side_effect=fake_run):
            exit_code = run_suites.main()

        out = capsys.readouterr().out
        assert exit_code == 1
        assert "FAILED plugin suites: bravo" in out
        assert "ran alpha" in out
        assert "ran bravo" in out
        assert "ran charlie" in out

    def test_all_pass_returns_zero(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["run_suites.py"])
        marketplace = _make_marketplace(tmp_path, ["alpha", "bravo"])

        def fake_run(cmd, cwd, capture_output, text):
            return _FakeCompletedProcess(0, stdout="ok")

        with patch.object(run_suites, "MARKETPLACE_ROOT", marketplace), \
                patch.object(run_suites, "REPO_ROOT", tmp_path), \
                patch.object(run_suites.subprocess, "run", side_effect=fake_run):
            exit_code = run_suites.main()

        assert exit_code == 0
        assert "All 2 plugin suite(s) passed." in capsys.readouterr().out

    def test_plugins_run_concurrently_not_serially(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["run_suites.py"])
        marketplace = _make_marketplace(tmp_path, ["alpha", "bravo", "charlie"])
        delay = 0.05

        def fake_run(cmd, cwd, capture_output, text):
            time.sleep(delay)
            return _FakeCompletedProcess(0, stdout="ok")

        with patch.object(run_suites, "MARKETPLACE_ROOT", marketplace), \
                patch.object(run_suites, "REPO_ROOT", tmp_path), \
                patch.object(run_suites.subprocess, "run", side_effect=fake_run):
            start = time.monotonic()
            exit_code = run_suites.main()
            elapsed = time.monotonic() - start

        assert exit_code == 0
        assert elapsed < delay * 2
