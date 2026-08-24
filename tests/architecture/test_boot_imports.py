"""Guard against reintroducing heavy imports on the `import src.bootstrap.app`
path.

Boot cost was cut by getting torch, chromadb, cv2, diffusers and
transformers, and the native text-encoder tree out of the import chain that
merely *defining* the FastAPI app walks (as opposed to the chain that runs
when a generation, or an admin request that reads hardware-derived defaults,
actually needs them). A regression here typically looks like a new top-level
`import torch` (or a re-export that pulls one in) in a module that ends up on
`src.bootstrap.container`'s or `src.bootstrap.routers`'s import graph.

Runs the check in a subprocess (not an in-process `sys.modules` snapshot) so
whatever this test file itself needs to import for pytest collection can
never contaminate the result.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every module that boot must NOT eagerly import. Any of these being present
# for a *legitimate* new reason should extend this set alongside the change
# that made it necessary, not just get silently deleted.
_FORBIDDEN_AT_BOOT = ("torch", "chromadb", "cv2", "diffusers", "transformers")

_CHECK_SCRIPT = """
import sys
import src.bootstrap.app

present = sorted(m for m in {forbidden!r} if m in sys.modules)
print("PRESENT:" + ",".join(present))
"""


def _run_boot_import_check() -> list[str]:
    site_packages = REPO_ROOT / "venv" / "lib" / "python3.12" / "site-packages"
    env_pythonpath = f"{site_packages}:{REPO_ROOT}" if site_packages.is_dir() else str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-c", _CHECK_SCRIPT.format(forbidden=_FORBIDDEN_AT_BOOT)],
        cwd=str(REPO_ROOT),
        env={"PYTHONPATH": env_pythonpath, "PATH": "/usr/bin:/bin:/usr/local/bin"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"`import src.bootstrap.app` failed in a clean subprocess.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    present = []
    for line in result.stdout.splitlines():
        if line.startswith("PRESENT:"):
            payload = line[len("PRESENT:"):]
            present = [m for m in payload.split(",") if m]
    return present


def test_bootstrap_app_import_leaves_heavy_modules_unimported():
    present = _run_boot_import_check()
    assert not present, (
        "Importing src.bootstrap.app pulled in module(s) boot must not pay for: "
        f"{present}. Trace the import chain (python -X importtime -c "
        "'import src.bootstrap.app') and defer the offending import to a "
        "function-local import at its actual point of use."
    )
