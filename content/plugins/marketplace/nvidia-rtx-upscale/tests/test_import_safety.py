"""The nvidia-rtx-upscale pipe must import on a host without `nvvfx` installed.

This is the load-bearing property of the plugin, not a nicety. `PipeCatalog`
imports pipe modules to discover them, and `_load_pipe_module` swallows any
exception and returns None - so a pipe that cannot import does not fail
loudly, it silently ceases to exist. If importing this module needed
`nvvfx`, the pipe (and the whole RTX Upscale preset with it) would be
invisible on every machine that hasn't installed the package - which is
every machine, until someone does.

The two tests that call the real `requirements_satisfied` (src/pipelines/
installer.py) live in tests/plugins/marketplace/ instead of here - a
marketplace plugin's own tests dir may import only `src.plugin_api`
(tests/architecture/test_layering.py), and that installer is a core internal
plugin_api does not expose.
"""

import sys

import pytest

from src.plugin_api.pipes import BasePipe


def pipe_class_in(module):
    for attr in vars(module).values():
        if (isinstance(attr, type) and issubclass(attr, BasePipe)
                and attr is not BasePipe and attr.__module__ == module.__name__):
            return attr
    return None


@pytest.mark.parametrize("pipe_dir", ["upscaler_rtx", "interpolator_rtx"])
def test_pipe_module_imports_with_nvvfx_blocked(pipe_paths, heavy_modules, load_pipe_module_fn, pipe_dir):
    """The guard proper: import the pipe with `nvvfx` made unimportable, so it
    passes on a host that HAS it installed too."""
    path = next(path for path in pipe_paths if path.parent.name == pipe_dir)
    module = load_pipe_module_fn(path, blocked=heavy_modules)

    assert pipe_class_in(module) is not None, f"{path} defined no pipe class"


def test_the_blocking_finder_actually_blocks(blocking_finder_cls):
    """Without this, the assertion above could pass vacuously - a finder that
    silently failed to block would prove nothing at all."""
    finder = blocking_finder_cls(["json"])
    sys.meta_path.insert(0, finder)
    try:
        with pytest.raises(ImportError):
            finder.find_spec("json")
    finally:
        sys.meta_path.remove(finder)


@pytest.mark.parametrize("pipe_dir", ["upscaler_rtx", "interpolator_rtx"])
def test_importing_the_pipe_does_not_pull_in_nvvfx(pipe_paths, load_pipe_module_fn, pipe_dir):
    """The complement of the block: on a host where `nvvfx` IS installed,
    blocking it proves the import path avoids it, but this also catches an
    import that succeeds by loading it for real."""
    path = next(path for path in pipe_paths if path.parent.name == pipe_dir)
    already_loaded = "nvvfx" in sys.modules
    load_pipe_module_fn(path)
    newly_loaded = "nvvfx" in sys.modules and not already_loaded

    assert not newly_loaded, (
        f"importing {pipe_dir} loaded nvvfx - the import belongs inside "
        "process(), not at module scope"
    )


def test_requirements_name_the_import_not_the_pip_package(pipe_module, vfg_module):
    """`requirements_satisfied` (src/pipelines/installer.py) checks this list
    with `importlib.import_module`, so it must be the import name (`nvvfx`),
    not the PyPI package name (`nvidia-vfx`) - the two differ."""
    for pipe in (pipe_module.RtxUpscalePipe, vfg_module.RtxFrameGenerationPipe):
        assert pipe.get_requirements()["pip"] == ["nvvfx"]


def test_manual_install_instructions_name_the_correct_pip_package(pipe_module, vfg_module):
    """The installer would otherwise run `pip install nvvfx` (the import-name
    entry in get_requirements()), which is not what the package is published
    as - manual_install_instructions() being non-None is what stops that."""
    for pipe in (pipe_module.RtxUpscalePipe, vfg_module.RtxFrameGenerationPipe):
        instructions = pipe.manual_install_instructions()
        assert instructions is not None
        assert 'pip install "nvidia-vfx>=0.2" --extra-index-url https://pypi.nvidia.com' in instructions
        assert "570.190" in instructions
        assert "LD_LIBRARY_PATH=/usr/lib/wsl/lib" in instructions
        assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility,ngx" in instructions


def test_pipe_defaults_survive_a_json_round_trip(pipe_module, vfg_module):
    """Plugin pipes are outside the core catalog sweep (no plugin registry in
    `tests/pipelines/test_pipe_default_config_is_json.py`), so this needs its
    own copy of the guard: defaults travel to a worker as JSON."""
    import json

    for pipe in (pipe_module.RtxUpscalePipe, vfg_module.RtxFrameGenerationPipe):
        defaults = pipe.get_default_config() or {}
        assert json.loads(json.dumps(defaults)) == defaults

        for spec in pipe.configuration() or []:
            assert json.loads(json.dumps(spec.default)) == spec.default, spec.name
            assert defaults[spec.name] == spec.default, spec.name
