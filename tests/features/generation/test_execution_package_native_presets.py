"""Every shipped native preset-mode must assemble into a valid execution package.

Binds each mode's fixture form with a REAL storage root. That matters: with
`storage_dir=None` - what `scripts/preset_render.py` and the golden harness use
- `_check_media_containment` returns early, so the branch that absolutizes a
submitted media path into the storage root never runs and the values under test
are placeholders that cannot occur in production.

Pure rendering plus pipe-class introspection: no GPU, no model file is opened,
no DB write. A pipe whose module cannot be imported in this environment (the
detailer family needs cv2) resolves to None and is reported rather than
silently skipped.
"""

import copy
import json
import tempfile
from pathlib import Path

import pytest

from scripts.preset_render import (
    FIXED_SEED,
    _collect_named_fields,
    _fixture_timeline_document,
    _fixture_video_director_document,
    _inject_unresolvable_defaults,
    _mode_references_timeline,
    _mode_references_video_director,
    build_form_serializer,
    build_generation_data,
    build_processor,
    load_all_presets,
    relativize_media_placeholders,
)
from src.features.forms.binding import bind_form
from src.features.generation.generation import deep_update
from src.features.generation.package_assembly import assemble_execution_package
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.presets.templates import default_form_name
from src.pipelines.catalog import PipeCatalog
from src.platform.worker_protocol import (
    ContentDigest,
    ModelBundleManifestV1,
    read_envelope,
    to_wire,
)

NATIVE_ENGINE = "native"

MODEL_BUNDLE = ModelBundleManifestV1(
    bundle_id="fixture-bundle",
    bundle_digest=ContentDigest(algorithm="sha256", hex="cd" * 32),
    entries=(),
)


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

    # The harness's media placeholders are absolute and outside any storage
    # root; a real submission is a path relative to it, which is what makes
    # the containment/absolutizing branch run.
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
    return PipeCatalog("src/pipelines/pipes", "pipes/custom")


@pytest.fixture(scope="module")
def native_packages(tmp_path_factory, pipe_catalog):
    """(label, source pipes, package) for every shipped native preset-mode."""
    storage_dir = tmp_path_factory.mktemp("storage")
    presets, _errors = load_all_presets()
    processor = build_processor()
    serializer = build_form_serializer()

    assembled = []
    for preset in sorted(presets, key=lambda p: p.id):
        if preset.engine != NATIVE_ENGINE:
            continue
        for mode in sorted(preset.modes.keys()):
            form_data = _bound_form_data(serializer, preset, mode, storage_dir)
            generation_data = build_generation_data(mode, form_data, preset.modes.get(mode))
            pipes = processor.process(preset, generation_data)

            built = BuiltPipeline(
                generation_id=f"gen-{preset.id}-{mode}",
                preset_id=preset.id,
                preset_template=preset,
                pipes=pipes,
            )
            package = assemble_execution_package(
                built, pipe_catalog=pipe_catalog, model_bundle=MODEL_BUNDLE,
            )
            assembled.append((f"{preset.name}/{mode}", copy.deepcopy(pipes), package))

    return assembled


def test_every_native_preset_mode_was_covered(native_packages):
    assert len(native_packages) >= 18, (
        f"only {len(native_packages)} native preset-modes rendered; "
        f"the shipped tree has 18"
    )


def test_packages_are_json_safe_end_to_end(native_packages):
    for label, _pipes, package in native_packages:
        decoded = read_envelope(to_wire(package))
        assert decoded == package, label


def test_pipe_count_and_order_match_the_built_pipeline(native_packages):
    for label, pipes, package in native_packages:
        assert [p.pipe_type for p in package.processed_pipes.pipes] == [
            p["name"] for p in pipes
        ], label
        assert [p.enabled for p in package.processed_pipes.pipes] == [
            p["enabled"] for p in pipes
        ], label


def test_config_equals_the_executor_merge(native_packages, pipe_catalog):
    """The package's config is exactly what PipelineExecutor computes from the
    pipe class defaults plus the preset's config (generation.py:601-604)."""
    for label, pipes, package in native_packages:
        for shipped, source in zip(package.processed_pipes.pipes, pipes):
            if not source["enabled"]:
                assert shipped.config == (source.get("config") or {}), label
                continue

            pipe_class = pipe_catalog.get_pipe(source["name"])
            if pipe_class is None:
                assert shipped.config == (source.get("config") or {}), label
                continue

            executor_config = deep_update(
                copy.deepcopy(pipe_class.get_default_config() or {}),
                copy.deepcopy(source.get("config") or {}),
            )
            assert shipped.config == executor_config, f"{label} :: {source['name']}"


def test_class_defaults_actually_reach_the_shipped_configs(native_packages, pipe_catalog):
    """The point of the exercise: keys a preset never wrote - including the
    literal `models/...` paths pipe classes default to - must be in the
    package, not contributed by whatever executes it."""
    gained = 0
    for _label, pipes, package in native_packages:
        for shipped, source in zip(package.processed_pipes.pipes, pipes):
            gained += len(set(shipped.config) - set(source.get("config") or {}))

    assert gained > 0


def test_no_pipe_class_default_key_is_missing_from_a_shipped_config(
    native_packages, pipe_catalog
):
    for label, pipes, package in native_packages:
        for shipped, source in zip(package.processed_pipes.pipes, pipes):
            if not source["enabled"]:
                continue
            pipe_class = pipe_catalog.get_pipe(source["name"])
            if pipe_class is None:
                continue
            missing = set(pipe_class.get_default_config() or {}) - set(shipped.config)
            assert not missing, f"{label} :: {source['name']} missing {sorted(missing)}"


def test_the_real_detailer_ships_the_host_paths_hidden_in_its_defaults(native_packages):
    """Against the real `detailer/sdxl` class, not a stand-in.

    Its detection models are the case the whole exercise is about: `person`
    is not in the preset's `detect:` list, so its `models/...` path exists
    only in the pipe class's defaults and appears nowhere in the payload -
    which is why auditing the payload alone could never have found it.
    """
    checked = 0
    for label, _pipes, package in native_packages:
        for shipped in package.processed_pipes.pipes:
            if shipped.pipe_type != "detailer/sdxl" or not shipped.enabled:
                continue
            checked += 1
            person = shipped.config["detections"]["person"]
            assert person["model"], label
            assert person["box_color"] == [255, 0, 255], label
            assert isinstance(person["box_color"], list), label

    assert checked, "no enabled detailer/sdxl pipe was exercised"


def test_input_asset_collection_strips_the_host_storage_root_from_the_wire(pipe_catalog):
    """The strong acceptance test for collect_input_assets: once assembly is
    given a real storage root, no string anywhere in the serialized package -
    not the pipe configs, not the manifest - may carry that host filesystem
    prefix. Uses a real preset-mode and a REAL planted file (unlike
    `native_packages`, whose media placeholders never exist on disk and so
    exercise no actual collection).

    A directly-submitted absolute path is the live vector this guards today:
    `bind_form`'s containment check (`_check_single_media_value`) is
    validate-only and returns an absolute value unchanged once it has
    verified it stays inside `storage_dir` - see
    src/features/forms/binding.py's `_check_media_containment` docstring.
    """
    presets, _errors = load_all_presets()
    preset = next(p for p in presets if p.name == "SeedVR2")
    mode = "upscale"
    mode_data = preset.modes[mode]
    form_name = default_form_name(mode_data)
    serializer = build_form_serializer()
    form_template = next(f for f in mode_data.forms if f.name == form_name)
    resolved = serializer._resolve_external_children(
        form_template.fields, {"paths": {"preset": preset.path}}
    )
    form_data = {}
    _collect_named_fields(resolved, form_data)
    _inject_unresolvable_defaults(resolved, form_data)

    with tempfile.TemporaryDirectory() as td:
        storage_dir = Path(td) / "storage"
        (storage_dir / "uploads").mkdir(parents=True)
        planted = storage_dir / "uploads" / "reference.png"
        planted.write_bytes(b"real planted bytes")

        form_data["input_image"] = str(planted)
        form_data.setdefault("quantity", 1)
        form_data.setdefault("seed", FIXED_SEED)

        bound = bind_form(
            preset, mode, form_name=None, raw_form_data=form_data,
            user_id=None, storage_dir=str(storage_dir),
        )
        generation_data = build_generation_data(mode, dict(bound.values), preset.modes.get(mode))
        processor = build_processor()
        pipes = processor.process(preset, generation_data)

        built = BuiltPipeline(
            generation_id="gen-strips-host-root",
            preset_id=preset.id,
            preset_template=preset,
            pipes=pipes,
        )
        package = assemble_execution_package(
            built, pipe_catalog=pipe_catalog, model_bundle=MODEL_BUNDLE,
            storage_dir=storage_dir,
        )

        assert package.input_assets is not None
        assert len(package.input_assets.assets) == 1

        wire = to_wire(package)
        assert str(storage_dir) not in wire
        assert "uploads/reference.png" not in wire
        assert "asset://" in wire


def test_pipe_ids_are_unique_and_configs_are_plain_json(native_packages):
    for label, _pipes, package in native_packages:
        ids = [p.pipe_id for p in package.processed_pipes.pipes]
        assert len(ids) == len(set(ids)), label
        for shipped in package.processed_pipes.pipes:
            json.dumps(shipped.config)
            json.dumps(shipped.inputs)
