"""Every shipped preset/mode's rendered pipe configuration must satisfy the
pipe class's own `PipeConfigSpec` bounds (min_value/max_value/choices/type).

Regression for the krea2-edit "edit" mode shipping `guidance: 0.0` /
`["cfg", 0.0]` while its generator (`GeneratorKrea2EditPipe`) INHERITS the
core Krea-2 generator's `guidance` spec (`min_value=1.0`) unchanged - a
partial migration away from the old "0.0 means NoCFG" convention (the
encoder's own `guidance_scale` had already moved to 1.0) left two literal
config sites behind. That reached `validate_pipe_configuration`
(src/features/generation/generation.py) only at real generation time:

    Parameter 'guidance' for pipe 'generator' must be >= 1.0, but got: 0.0

This mirrors exactly what `GenerationExecutor` does before running a pipe
(generation.py:601-608: `deep_update(pipe_class.get_default_config(), custom_config)`
then `validate_pipe_configuration`), across every preset x mode x enabled
pipe this installation ships - core presets AND plugin-contributed ones, so
the next partial migration/typo in ANY preset's literal `configuration:`
block fails here instead of at a user's real generation.

Pure rendering plus pipe-class introspection: no GPU, no model file opened,
no DB write. A pipe whose module cannot be imported in this environment
(the detailer family needs cv2) resolves to `None` and is skipped, same as
`test_execution_package_native_presets.py`.
"""

import copy
import tempfile
import warnings

import pytest

from scripts.preset_render import (
    FIXED_SEED,
    ROOT,
    _all_plugin_manifests,
    _collect_named_fields,
    _fixture_timeline_document,
    _fixture_video_director_document,
    _inject_unresolvable_defaults,
    _mode_references_timeline,
    _mode_references_video_director,
    _plugin_registry_stub,
    build_form_serializer,
    build_generation_data,
    build_processor,
    load_all_presets,
    relativize_media_placeholders,
)
from src.features.forms.binding import bind_form
from src.features.generation.generation import deep_update, validate_pipe_configuration
from src.features.presets.templates import default_form_name
from src.pipelines.catalog import PipeCatalog


def _bound_form_data(serializer, preset, mode, storage_dir):
    mode_data = preset.modes[mode]
    form_name = default_form_name(mode_data)
    form_data = {}

    if form_name is not None:
        form_template = next(f for f in mode_data.forms if f.name == form_name)
        resolved = serializer._resolve_external_children(
            form_template.fields, {"paths": {"preset": preset.path}}
        )
        _collect_named_fields(resolved, form_data)
        _inject_unresolvable_defaults(resolved, form_data)

    relativize_media_placeholders(form_data)

    form_data.setdefault("quantity", 1)
    form_data.setdefault("seed", FIXED_SEED)
    if _mode_references_video_director(mode_data) and "video_director" not in form_data:
        form_data["video_director"] = _fixture_video_director_document()
    if _mode_references_timeline(mode_data) and "timeline" not in form_data:
        form_data["timeline"] = _fixture_timeline_document()

    bound = bind_form(
        preset, mode, form_name=None, raw_form_data=form_data,
        user_id=None, storage_dir=str(storage_dir),
    )
    return dict(bound.values)


@pytest.fixture(scope="module")
def pipe_catalog():
    # Plugin-aware, unlike test_execution_package_native_presets.py's
    # catalog: plugin-contributed pipes (e.g. krea2-edit's
    # generator/krea2_edit) resolve to None without a plugin_registry,
    # which is exactly how this bug slipped past that suite's
    # native-engine-only sweep.
    manifests = _all_plugin_manifests()
    plugin_registry = _plugin_registry_stub(manifests)
    return PipeCatalog("src/pipelines/pipes", "pipes/custom", plugin_registry=plugin_registry)


@pytest.fixture(scope="module")
def rendered_pipe_configs(tmp_path_factory, pipe_catalog):
    """(label, pipe_name, pipe_id, merged_config) for every enabled pipe in
    every preset x mode this installation ships, engine-agnostic."""
    storage_dir = tmp_path_factory.mktemp("storage")
    presets, _errors = load_all_presets()
    processor = build_processor()
    serializer = build_form_serializer()

    core_root = str(ROOT / "content" / "presets")
    results = []
    for preset in sorted(presets, key=lambda p: p.id):
        for mode in sorted(preset.modes.keys()):
            try:
                form_data = _bound_form_data(serializer, preset, mode, storage_dir)
                generation_data = build_generation_data(mode, form_data, preset.modes.get(mode))
                pipes = processor.process(preset, generation_data)
            except Exception:
                # A core preset that cannot render is a real failure. A
                # plugin preset that cannot render (local plugins are not
                # repo artifacts) must not kill the whole module at setup
                # and take every core preset's coverage with it -- its own
                # bounds simply cannot be checked, which is reported via
                # the warning, not hidden.
                if str(preset.path).startswith(core_root):
                    raise
                warnings.warn(
                    f"unrenderable plugin preset skipped by spec-bounds sweep: "
                    f"{preset.name}/{mode} ({preset.path})")
                continue

            label = f"{preset.name}/{mode}"
            for pipe in pipes:
                if not pipe.get("enabled"):
                    continue
                pipe_class = pipe_catalog.get_pipe(pipe["name"])
                if pipe_class is None:
                    continue
                default_cfg = pipe_class.get_default_config() or {}
                custom_cfg = pipe.get("config") or {}
                merged = deep_update(copy.deepcopy(default_cfg), copy.deepcopy(custom_cfg))
                results.append((label, pipe["name"], pipe.get("id"), pipe_class, merged))

    return results


def test_every_rendered_pipe_config_satisfies_its_own_spec_bounds(rendered_pipe_configs):
    assert rendered_pipe_configs, "no enabled pipe was rendered across the shipped preset tree"

    failures = []
    for label, pipe_name, pipe_id, pipe_class, merged in rendered_pipe_configs:
        try:
            validate_pipe_configuration(pipe_class, merged)
        except ValueError as e:
            failures.append(f"{label} :: {pipe_name} (id={pipe_id}): {e}")

    assert not failures, "\n" + "\n".join(failures)


def test_krea2_edit_generator_guidance_is_in_range(rendered_pipe_configs):
    # Targeted pin for the exact reported bug, alongside the general sweep
    # above: krea2-edit's "edit" mode generator must render a `guidance`
    # that is legal under GeneratorKrea2EditPipe's inherited spec.
    matches = [
        merged for label, pipe_name, _pipe_id, _pipe_class, merged in rendered_pipe_configs
        if pipe_name == "generator/krea2_edit"
    ]
    if not matches:
        pytest.skip("krea2-edit plugin not present")
    for merged in matches:
        assert merged["guidance"] >= 1.0, merged["guidance"]
