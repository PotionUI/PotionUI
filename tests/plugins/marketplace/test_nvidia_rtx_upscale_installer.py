"""`requirements_satisfied` (src/pipelines/installer.py) must report the
nvidia-rtx-upscale pipe's real install state - not a reimplementation of the
same check, or a broken `get_requirements()` (wrong import name, wrong pip
package) would pass silently.

This lives here rather than in the plugin's own `content/plugins/marketplace/
nvidia-rtx-upscale/tests/` - a marketplace plugin's own tests dir may only
import `src.plugin_api` (tests/architecture/test_layering.py's `content/plugins`
scope), and `requirements_satisfied` is a core internal plugin_api does not
expose. See test_nvidia_rtx_upscale_manifest.py for the same split applied to
manifest loading.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from src.pipelines.installer import requirements_satisfied

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "nvidia-rtx-upscale"


def _load_plugin_conftest() -> ModuleType:
    """Import the plugin's own tests/conftest.py by file path, to reuse its
    `load_pipe_module` rather than duplicating how the pipe is loaded."""
    spec = importlib.util.spec_from_file_location(
        "_nvidia_rtx_upscale_plugin_conftest", PLUGIN_ROOT / "tests" / "conftest.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pipe_module():
    return _load_plugin_conftest().load_pipe_module()


def test_the_pipe_reports_itself_uninstalled_without_nvvfx(pipe_module):
    if importlib.util.find_spec("nvvfx") is not None:
        pytest.skip("this host has nvvfx installed")

    assert requirements_satisfied(pipe_module.RtxUpscalePipe) is False


def test_the_pipe_reports_itself_installed_with_nvvfx_present(pipe_module):
    """The complement of the test above: on a host that DOES have nvvfx, the
    catalog must report it ready rather than perpetually NOT_INSTALLED."""
    if importlib.util.find_spec("nvvfx") is None:
        pytest.skip("this host has no nvvfx installed")

    assert requirements_satisfied(pipe_module.RtxUpscalePipe) is True
