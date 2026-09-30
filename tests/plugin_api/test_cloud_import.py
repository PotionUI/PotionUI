import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCRIPT = "; ".join([
    "import sys",
    "import src.plugin_api.cloud",
    "import src.plugin_api.cloud_testing",
    "import src.pipelines.cloud",
    "print('HEAVY:' + ','.join(sorted(m for m in ('torch', 'diffusers', 'transformers') if m in sys.modules)))",
])


def test_cloud_plugin_api_does_not_import_torch():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in [str(REPO_ROOT), os.environ.get("PYTHONPATH", "")] if p)
    result = subprocess.run([sys.executable, "-c", SCRIPT], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "HEAVY:"


def test_public_surface_is_importable_and_complete():
    import src.plugin_api.cloud as cloud

    for name in cloud.__all__:
        assert hasattr(cloud, name), name
    for required in ("CloudProvider", "CloudBackendConfig", "CloudHttp", "CloudError", "register_cloud_provider", "CloudModelSpec"):
        assert required in cloud.__all__
