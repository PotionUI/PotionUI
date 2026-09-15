import sys
from pathlib import Path
from unittest.mock import patch

_RELEASE_DIR = Path(__file__).resolve().parents[1] / "release"
if str(_RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(_RELEASE_DIR))

import release_gate  # noqa: E402


class TestLintOnly:
    def test_lint_only_skips_heavy_gates(self):
        with patch.object(release_gate, "gate_recipe_lint", return_value=True) as recipe_lint, \
                patch.object(release_gate, "gate_layering") as layering, \
                patch.object(release_gate, "gate_setup_suite") as setup_suite, \
                patch.object(release_gate, "gate_gpu_preset_e2e") as gpu_gate, \
                patch.object(release_gate, "gate_preset_lint_budget", return_value=True) as lint_budget:
            exit_code = release_gate.main(["--lint-only"])

        assert exit_code == 0
        recipe_lint.assert_called_once()
        lint_budget.assert_called_once()
        layering.assert_not_called()
        setup_suite.assert_not_called()
        gpu_gate.assert_not_called()

    def test_default_runs_all_gates(self):
        with patch.object(release_gate, "gate_recipe_lint", return_value=True), \
                patch.object(release_gate, "gate_layering", return_value=True) as layering, \
                patch.object(release_gate, "gate_setup_suite", return_value=True) as setup_suite, \
                patch.object(release_gate, "gate_gpu_preset_e2e", return_value=release_gate.SKIP) as gpu_gate, \
                patch.object(release_gate, "gate_preset_lint_budget", return_value=True):
            exit_code = release_gate.main(["--skip-gpu"])

        assert exit_code == 0
        layering.assert_called_once()
        setup_suite.assert_called_once()
        gpu_gate.assert_called_once_with(True)

    def test_lint_only_failure_propagates(self):
        with patch.object(release_gate, "gate_recipe_lint", return_value=True), \
                patch.object(release_gate, "gate_layering") as layering, \
                patch.object(release_gate, "gate_setup_suite") as setup_suite, \
                patch.object(release_gate, "gate_gpu_preset_e2e") as gpu_gate, \
                patch.object(release_gate, "gate_preset_lint_budget", return_value=False):
            exit_code = release_gate.main(["--lint-only"])

        assert exit_code == 1
        layering.assert_not_called()
        setup_suite.assert_not_called()
        gpu_gate.assert_not_called()
