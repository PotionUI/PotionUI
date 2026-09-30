from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ROOT_SCRIPT = """
import sys
import src.plugin_api
print('BAD:' + ','.join(sorted(m for m in sys.modules if m == 'torch')))
"""

LAZY_SCRIPT = """
import sys
import src.plugin_api as api
names = [
    'Conditioning', 'GeneratorContext', 'GeneratorKrea2Pipe', 'NativeGeneratorHandle',
    'ProgressEmitter', 'native_step_hooks', 'BackgroundMattingModel',
    'SamplingCancelled', 'sample_euler',
]
missing = [n for n in names if getattr(api, n, None) is None]
print('MISSING:' + ','.join(missing))
print('TORCH:' + str('torch' in sys.modules))
"""


def _run(script: str) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")] if p)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_plugin_api_root_does_not_import_torch():
    assert _run(ROOT_SCRIPT).strip().splitlines()[-1] == "BAD:"


def test_torch_backed_names_still_resolve_from_the_root_on_access():
    lines = _run(LAZY_SCRIPT).strip().splitlines()
    assert lines[-2] == "MISSING:"
    assert lines[-1] == "TORCH:True"
