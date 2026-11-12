import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.api_reference import collect

ROOT = Path(__file__).resolve().parents[2]
HARNESS = "tests.docs.api_example_harness"


def harness_environment():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(part for part in (str(ROOT), env.get("PYTHONPATH", "")) if part)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CUDA_VISIBLE_DEVICES"] = ""
    return env


def run_phase(*args):
    result = subprocess.run(
        [sys.executable, "-m", HARNESS, *args],
        cwd=str(ROOT),
        env=harness_environment(),
        capture_output=True,
        text=True,
        timeout=280,
    )
    assert result.returncode == 0, "\n".join((result.stderr or result.stdout).splitlines()[-25:])


@pytest.fixture(scope="session")
def example_results(tmp_path_factory):
    scratch = tmp_path_factory.mktemp("api-examples")
    target = scratch / "results.json"
    run_phase("prepare", str(scratch / "instance"))
    run_phase("run", str(scratch / "instance"), str(target))
    return json.loads(target.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def route_snapshot():
    return collect(ROOT)
