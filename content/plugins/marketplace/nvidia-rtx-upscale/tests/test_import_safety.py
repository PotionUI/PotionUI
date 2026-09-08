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


def test_pipe_module_imports_with_nvvfx_blocked(pipe_main_path, heavy_modules, load_pipe_module_fn):
    """The guard proper: import the pipe with `nvvfx` made unimportable, so it
    passes on a host that HAS it installed too."""
    module = load_pipe_module_fn(pipe_main_path, blocked=heavy_modules)

    assert pipe_class_in(module) is not None, f"{pipe_main_path} defined no pipe class"


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


def test_importing_the_pipe_does_not_pull_in_nvvfx(pipe_main_path, load_pipe_module_fn):
    """The complement of the block: on a host where `nvvfx` IS installed,
    blocking it proves the import path avoids it, but this also catches an
    import that succeeds by loading it for real."""
    already_loaded = "nvvfx" in sys.modules
    load_pipe_module_fn(pipe_main_path)
    newly_loaded = "nvvfx" in sys.modules and not already_loaded

    assert not newly_loaded, (
        "importing upscaler_rtx loaded nvvfx - the import belongs inside "
        "process(), not at module scope"
    )


def test_requirements_name_the_import_not_the_pip_package(pipe_module):
    """`requirements_satisfied` (src/pipelines/installer.py) checks this list
    with `importlib.import_module`, so it must be the import name (`nvvfx`),
    not the PyPI package name (`nvidia-vfx`) - the two differ."""
    requirements = pipe_module.RtxUpscalePipe.get_requirements()

    assert requirements["pip"] == ["nvvfx"]


def test_manual_install_instructions_name_the_correct_pip_package(pipe_module):
    """The installer would otherwise run `pip install nvvfx` (the import-name
    entry in get_requirements()), which is not what the package is published
    as - manual_install_instructions() being non-None is what stops that."""
    instructions = pipe_module.RtxUpscalePipe.manual_install_instructions()

    assert instructions is not None
    assert "pip install nvidia-vfx" in instructions
    assert "570.190" in instructions


def test_pipe_defaults_survive_a_json_round_trip(pipe_module):
    """Plugin pipes are outside the core catalog sweep (no plugin registry in
    `tests/pipelines/test_pipe_default_config_is_json.py`), so this needs its
    own copy of the guard: defaults travel to a worker as JSON."""
    import json

    defaults = pipe_module.RtxUpscalePipe.get_default_config() or {}
    assert json.loads(json.dumps(defaults)) == defaults

    for spec in pipe_module.RtxUpscalePipe.configuration() or []:
        assert json.loads(json.dumps(spec.default)) == spec.default, spec.name
