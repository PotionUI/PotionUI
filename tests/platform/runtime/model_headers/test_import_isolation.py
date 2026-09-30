from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

SCRIPT = """
import sys
import src.platform.runtime.model_headers as headers
import src.platform.runtime.model_headers.reader
import src.platform.runtime.model_headers.signatures
import src.platform.runtime.model_headers.components
import src.platform.runtime.model_headers.registry
bad = sorted(m for m in sys.modules if m == 'torch' or m.startswith('src.platform.runtime.native'))
print('BAD:' + ','.join(bad))
"""


def test_model_headers_never_imports_torch_or_the_native_runtime():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in [str(REPO_ROOT), *sys.path] if p)
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "BAD:"
