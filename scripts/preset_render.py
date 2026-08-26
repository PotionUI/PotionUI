#!/usr/bin/env python3
"""
Preset render developer harness.

Renders a preset's pipeline through the real `PresetProcessor.process()` code path with
deterministic fixture form data, and prints (or snapshots) every resulting pipe config
value together with its Python type. This is a read-only reporting tool: it never
touches `src/` behavior, the database, or the running app - it is meant to freeze
today's exact template-rendering output so a future templating-semantics change can be
diffed against it.

Usage:
    python scripts/preset_render.py <preset-dir-or-id> <mode> [--form fixture.yml] [--json]
    python scripts/preset_render.py <preset-dir-or-id> <mode> --golden [--out tests/golden/preset_renders]
    python scripts/preset_render.py --golden-all [--out tests/golden/preset_renders]

<preset-dir-or-id> may be a preset's ULID `id` (from preset.yml) or a path to (or
suffix of) its directory, e.g. `marketplace/SDXL/realistic` or `content/presets/marketplace/SDXL/realistic`.

Fixture form data (used when --form is not given): the mode's default form variant is
walked field-by-field (including external tab files, resolved exactly as
PresetProcessor does at generation time) and `form_data` is built from each named
field's `value` (if set) else `default` (if set) else omitted entirely - AS IS, with
no type coercion. This snapshots today's reality, warts and all: string "-1" seeds,
Jinja template strings that never get rendered because nothing else feeds them, etc.
`quantity` and `seed` are filled in from the walked fixture if a field produced them,
else defaulted to 1 and 42 respectively, so the processor always has the runtime keys
it expects.

See the module docstring parity notes below for the determinism guarantees this relies
on (stub settings/model-manager/loader, fixed prompts, fixed seed).
"""

import argparse
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.features.presets.loader import PresetTemplateLoader, plugin_preset_roots  # noqa: E402
from src.features.presets.processor import PresetProcessor  # noqa: E402
from src.platform.plugins.loader import PluginLoader  # noqa: E402
from src.platform.templating.processor import TemplateProcessor  # noqa: E402
from src.features.presets.templates import (
    PresetTemplate,
    ModeTemplate,
    FieldTemplate,
    sorted_forms,
    default_form_name,
)  # noqa: E402
from src.features.presets.form_serializer import PresetFormSerializer  # noqa: E402
from src.features.forms.binding import bind_form  # noqa: E402

# Field-type names that reference a model (see src/features/fields/builtin.py's
# FieldTypeDefinition registrations: "model" and "models" both back onto the
# `Model` field class). Presets can never ship a real path default for these
# (models aren't known until download time - see docs/presets.md), so
# bind_form would see them as missing and fail `required: true` validation.
_MODEL_FIELD_TYPES = frozenset({"model", "models"})
_LORA_PICKER_FIELD_TYPE = "lora_picker"
# Registered field-type names whose value is a plain string path (mirrors
# src/features/forms/binding.py's `_MEDIA_FIELD_TYPES`). A `required: true` one of
# these (e.g. an img2vid mode's source image) has no real file to point at in
# this harness either, so it gets the same placeholder-path treatment as
# model/lora_picker fields - `bind_form` never checks containment here
# anyway (no `storage_dir`), so an invented path is harmless and lets the
# harness capture the full pipeline shape instead of stopping at
# `FormBindingError: required field is missing`.
_MEDIA_FIELD_TYPES = frozenset({"image", "video", "audio", "media", "file"})
# A `required: true` plain-text field with no `default:` (e.g. a caption a
# form deliberately leaves for the user to write) has no real fixture value
# either - same "invented placeholder, harmless without a real storage_dir/
# validator behind it" treatment as the media/model fields above.
_TEXT_FIELD_TYPES = frozenset({"string", "textbox"})
_TEXT_FIELD_PLACEHOLDER = "GOLDEN placeholder text"


# ---------------------------------------------------------------------------
# Deterministic stubs: snapshots must never embed Mock() reprs (memory
# addresses), so nothing here uses unittest.mock.
# ---------------------------------------------------------------------------

class StubSettingsManager:
    """Minimal stand-in for SettingsManager.get_setting(key, default, user_id).

    Returns fixed, byte-stable values for the handful of keys pipeline.yml
    templates are known to read via `setting(...)`/`config(...)`, and a stable
    placeholder for anything else - never a Mock repr.
    """

    FIXED = {
        "file_storage_directory": "/STORAGE",
    }

    def get_setting(self, key: str, default: Any = None, user_id: Optional[str] = None) -> Any:
        if key in self.FIXED:
            return self.FIXED[key]
        return f"STUB:{key}"

    # Convenience methods some pipes/templates may call directly (mirrors
    # src/platform/settings/settings.py's public surface) - kept deterministic too.
    def get_file_storage_directory(self, user_id: Optional[str] = None) -> str:
        return self.FIXED["file_storage_directory"]

    def is_nsfw_enabled(self, user_id: Optional[str] = None) -> bool:
        return False

    def get_model_cache_scope(self, user_id: Optional[str] = None) -> str:
        return "preset"


class StubModelManager:
    """PresetProcessor.process() never calls model_manager directly - it's only
    threaded through for pipes that run for real. Kept as an inert stub (not a
    Mock) purely so nothing could ever leak a Mock repr into a rendered value."""
    pass


class StubPresetTemplateLoader:
    """Same rationale as StubModelManager - preset_template_loader is unused by
    PresetProcessor.process() itself."""
    presets: List[Any] = []

    def load_preset_by_id(self, preset_id: str) -> Optional[PresetTemplate]:
        return None


FIXED_POSITIVE_PROMPT = "golden positive prompt"
FIXED_NEGATIVE_PROMPT = "golden negative prompt"
FIXED_SEED = 42


def build_processor() -> PresetProcessor:
    template_processor = TemplateProcessor(settings_manager=StubSettingsManager())
    processor = PresetProcessor(
        template_processor=template_processor,
        model_manager=StubModelManager(),
        settings_manager=StubSettingsManager(),
        preset_template_loader=StubPresetTemplateLoader(),
    )
    # Admin-set preset configuration values live in the database (see
    # PresetProcessor._get_configuration_values / src/platform/settings). Rendering
    # must not depend on whatever happens to be installed in a developer's
    # local DB, so this is pinned to "no configuration set" for every preset.
    # It's a @staticmethod on the class; shadowing it on the instance is the
    # normal way to override a staticmethod-as-attribute per-instance.
    processor._get_configuration_values = lambda preset_id: {}
    return processor


def build_form_serializer() -> PresetFormSerializer:
    # `PresetFormSerializer` (used in production by operations.get_form_schema
    # to build the JSON form schema) is the one that correctly resolves external children
    # files recursively, including nested dict-shaped children coming straight
    # off disk (see PresetFormSerializer._resolve_external_children /
    # _expand_loop_fields, which handle both FieldTemplate and raw dict
    # representations). `PresetProcessor._process_form_fields` is dead code
    # (nothing outside itself calls it) and does NOT recurse into raw-dict
    # children, so reusing it here would crash on any tab file whose
    # container fields (e.g. a "row") have literal dict children - which is
    # the common case (see modes/txt2img/tabs/generation.yml).
    template_processor = TemplateProcessor(settings_manager=StubSettingsManager())
    return PresetFormSerializer(preset_loader=StubPresetTemplateLoader(), template_processor=template_processor)


# ---------------------------------------------------------------------------
# Fixture form-data construction
# ---------------------------------------------------------------------------

def _all_plugin_manifests() -> List[Any]:
    """Every plugin manifest discovered on disk (no app/DB boot needed)."""
    return PluginLoader().discover_plugins()


def _plugin_registry_stub(manifests: List[Any]) -> Any:
    """Satisfies the `get_enabled_plugins()` surface `PresetTemplateLoader`
    needs to merge plugin-contributed `preset_modes:` (see
    `PresetTemplateLoader._apply_preset_mode_contributions`), treating every
    discovered manifest as enabled - the same no-DB posture already used for
    plugin `presets:` roots below (there's no DB here, so on-disk presence is
    the only signal available)."""
    return SimpleNamespace(get_enabled_plugins=lambda: manifests)


def load_all_presets(
    presets_root: Path = None, include_plugins: bool = True,
) -> Tuple[List[PresetTemplate], Dict[str, List[str]]]:
    root = presets_root or (ROOT / "content" / "presets")
    if include_plugins:
        manifests = _all_plugin_manifests()
        # `content/presets` stays first so `paths._shared` resolves against the core tree;
        # plugin-contributed preset roots (discovered from manifests on disk, so no
        # app/DB boot) are appended. The same manifests feed `plugin_registry` so
        # plugin-contributed `preset_modes:` merge into their target
        # presets too, not just whole plugin-owned preset roots.
        paths = [str(root)] + [str(p) for p in plugin_preset_roots(manifests)]
        registry = _plugin_registry_stub(manifests)
    else:
        # The golden-snapshot surface: snapshots are a repo artifact, but
        # plugin-shipped presets AND plugin-contributed `preset_modes:` are
        # not repo-tracked (local plugins are gitignored; marketplace preset
        # plugins ship separately), so whatever plugins happen to sit on this
        # machine must not change the golden set the guard demands.
        paths = [str(root)]
        registry = None
    loader = PresetTemplateLoader(paths, plugin_registry=registry)
    loader.load_presets()
    return loader.presets, loader.load_errors


def find_preset(presets: List[PresetTemplate], preset_ref: str) -> Optional[PresetTemplate]:
    """Resolve a CLI `<preset-dir-or-id>` argument to a loaded PresetTemplate."""
    for preset in presets:
        if preset.id == preset_ref:
            return preset

    ref = preset_ref.rstrip("/")
    for preset in presets:
        path = preset.path.rstrip("/")
        if path == ref or path.endswith("/" + ref) or path == str(ROOT / ref):
            return preset

    return None


def _field_get(field: Any, attr: str) -> Any:
    """Read an attribute off a field that may be a FieldTemplate or a raw dict
    (external-children files that were never converted to FieldTemplate come
    back as dicts - see PresetFormSerializer._resolve_external_children)."""
    if isinstance(field, dict):
        return field.get(attr)
    return getattr(field, attr, None)


def _collect_named_fields(fields: List[Any], form_data: Dict[str, Any]) -> None:
    """Recursively walk resolved fields, writing each named leaf's value/default
    into form_data AS-IS (no coercion). Container fields (rows, tabs, tab) have
    no `name` and are only ever recursed into via their `children`."""
    for field in fields:
        name = _field_get(field, "name")
        if name:
            value = _field_get(field, "value")
            default = _field_get(field, "default")
            if value is not None:
                form_data[name] = value
            elif default is not None:
                form_data[name] = default
            # else: field genuinely has neither -> omitted, matching a real
            # submission that never touched this field.
        children = _field_get(field, "children")
        if children and isinstance(children, list):
            _collect_named_fields(children, form_data)


def _inject_unresolvable_defaults(fields: List[Any], form_data: Dict[str, Any]) -> None:
    """`bind_form` (src/features/forms/binding.py) applies each field's `default`
    for any key missing from the submission, then validates `required`.
    Model and LoRA-picker fields can never carry a real fixture default -
    models aren't known until download time (docs/presets.md) - so a
    `required: true` one of these would otherwise fail binding here even
    though it renders fine in production once a real model is picked.

    This is a fixture-harness-only placeholder pass, run BEFORE `bind_form`,
    so the harness can still snapshot template rendering downstream of a
    field bind_form would otherwise reject. It never touches real value/
    default resolution (`_collect_named_fields` above is unchanged) - it
    only fills in fields that came out of that walk with neither.
    """
    _MEDIA_EXTENSIONS = {"image": "png", "video": "mp4", "audio": "wav", "media": "png", "file": "bin"}

    for field in fields:
        name = _field_get(field, "name")
        field_type = _field_get(field, "type")
        if name and name not in form_data:
            if field_type in _MODEL_FIELD_TYPES:
                form_data[name] = f"/GOLDEN/models/{name}.safetensors"
            elif field_type == _LORA_PICKER_FIELD_TYPE:
                form_data[name] = []
            elif field_type in _MEDIA_FIELD_TYPES and _field_get(field, "required"):
                ext = _MEDIA_EXTENSIONS.get(field_type, "bin")
                placeholder = f"/GOLDEN/media/{name}.{ext}"
                # A `configuration.multi` media field carries a LIST, and its
                # own validator rejects a scalar outright - so the placeholder
                # has to match the declared shape or binding fails on a field
                # that renders fine in production.
                configuration = _field_get(field, "configuration") or {}
                multi = bool(configuration.get("multi")) if isinstance(configuration, dict) else False
                form_data[name] = [placeholder] if multi else placeholder
            elif field_type in _TEXT_FIELD_TYPES and _field_get(field, "required"):
                form_data[name] = _TEXT_FIELD_PLACEHOLDER
            # Non-required image/video/audio/media/file/string fields with no
            # default: left omitted, as before.
        children = _field_get(field, "children")
        if children and isinstance(children, list):
            _inject_unresolvable_defaults(children, form_data)


_MEDIA_PLACEHOLDER_PREFIX = "/GOLDEN/media/"


def relativize_media_placeholders(form_data: Dict[str, Any]) -> Dict[str, Any]:
    """Rewrite this harness's absolute media placeholders to storage-relative
    paths, in place.

    THIS harness never has a storage root, so it keeps the absolute form (the
    containment check is skipped and logged). Callers that DO bind against a
    real storage_dir - the two suites that reuse `_inject_unresolvable_
    defaults` to sweep every preset x mode - have to hand `bind_form` the
    relative path a real submission carries, or containment rejects a field
    that is fine in production. A `multi` media field's placeholder is a
    list, so the rewrite reaches inside it.
    """
    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str) and value.startswith(_MEDIA_PLACEHOLDER_PREFIX):
            return value[len(_MEDIA_PLACEHOLDER_PREFIX):]
        return value

    for key, value in list(form_data.items()):
        form_data[key] = rewrite(value)
    return form_data


def build_fixture_form_data(form_serializer: PresetFormSerializer, preset: PresetTemplate, mode: str) -> Dict[str, Any]:
    """Build fixture `form_data` and bind it exactly like production does.

    Walks the mode's default form variant to collect each named field's
    fixture value/default (warts and all - `_collect_named_fields` is
    unchanged), injects placeholders for model/lora_picker fields that have
    neither (see `_inject_unresolvable_defaults`), then runs the whole thing
    through `bind_form` (src/features/forms/binding.py) so the harness mirrors the
    templating rework's server boundary: `PresetProcessor.process()` now
    assumes `form_data` is already bound (typed/defaulted/validated) rather
    than binding it itself. `storage_dir=None` is correct here - this harness
    never has a real storage root, and `bind_form` simply skips the
    image/video/audio/media/file containment check (logged) in that case,
    per its own docstring; that's fine for a read-only rendering harness.
    """
    mode_data = preset.modes[mode]
    form_name = default_form_name(mode_data)
    form_data: Dict[str, Any] = {}

    if form_name is not None:
        form_template = next(f for f in mode_data.forms if f.name == form_name)
        context = {"paths": {"preset": preset.path}}
        # Resolves @loop and external `children` file references exactly the
        # way the real form schema endpoint does (see build_form_serializer).
        resolved_fields = form_serializer._resolve_external_children(form_template.fields, context)
        _collect_named_fields(resolved_fields, form_data)
        _inject_unresolvable_defaults(resolved_fields, form_data)

    form_data.setdefault("quantity", 1)
    form_data.setdefault("seed", FIXED_SEED)

    if _mode_references_video_director(mode_data) and "video_director" not in form_data:
        # `video_director` is never a declared FORM field - the orchestrator
        # merges the already-normalized document into `form_data` before the
        # processor ever runs (src/features/generation/orchestrator.py), so
        # nothing in the form-field walk above would ever produce it. Inject
        # the minimal valid t2v-shape document `normalize_video_director`
        # would itself produce (src/features/video_director/normalize.py), incl.
        # its derived `media_images`/`media_placements` (LTX's pipeline
        # reads those directly - see `derive_ltx_media_fields`'s docstring).
        form_data["video_director"] = _fixture_video_director_document()

    if _mode_references_timeline(mode_data) and "timeline" not in form_data:
        # `timeline` is never a declared FORM field either - like
        # `video_director`, the frontend's Prompt Relay timeline UI merges
        # the already-built document into `form_data` before the processor
        # ever runs (see the preset's `vars.prompt_relay_modes` +
        # src/features/forms/binding.py's `_PASSTHROUGH_KEYS`, which
        # documents this exact preset). Inject the minimal valid document
        # shape the frontend submits (frontend/src/lib/components/
        # PromptRelayEditor.svelte: duration/fps/segments/imageSegments/
        # audioSegments), with one segment spanning the full duration so
        # `sort(attribute='start')` and the duration/length math resolve.
        form_data["timeline"] = _fixture_timeline_document()

    bound = bind_form(preset, mode, form_name=None, raw_form_data=form_data, user_id=None, storage_dir=None)
    return dict(bound.values)


def _mode_references_timeline(mode_data: ModeTemplate) -> bool:
    """Whether any pipe in this mode reads `form.timeline` - a static text
    scan of each pipe's (still-unrendered) `enabled`/`configuration`
    templates, since `timeline` is never a declared form field (see
    `build_fixture_form_data`'s docstring)."""
    for pipe in mode_data.pipes:
        if "form.timeline" in str(pipe.enabled) or "form.timeline" in str(pipe.configuration):
            return True
    return False


def _fixture_timeline_document() -> Dict[str, Any]:
    return {
        "duration": 5.0,
        "fps": 24,
        "segments": [
            {"id": "seg-0", "start": 0, "end": 5.0, "text": FIXED_POSITIVE_PROMPT},
        ],
        "imageSegments": [],
        "audioSegments": [],
    }


def _mode_references_video_director(mode_data: ModeTemplate) -> bool:
    """Whether any pipe in this mode reads `form.video_director` - a static
    text scan of each pipe's (still-unrendered) `enabled`/`configuration`
    templates, since `video_director` is never a declared form field (see
    `build_fixture_form_data`'s docstring)."""
    for pipe in mode_data.pipes:
        if "video_director" in str(pipe.enabled) or "video_director" in str(pipe.configuration):
            return True
    return False


def _fixture_video_director_document() -> Dict[str, Any]:
    from src.features.video_director.normalize import derive_ltx_media_fields, derive_segment_routing

    doc: Dict[str, Any] = {
        "schema_version": 1,
        "mode": "t2v",
        "settings": {"fps": 24, "duration": 5.0, "resolution": "", "seed": FIXED_SEED, "continuation": None},
        "segments": [{
            "id": "seg-0", "prompt": FIXED_POSITIVE_PROMPT, "negative_prompt": FIXED_NEGATIVE_PROMPT,
            "start": None, "end": None, "frames": None, "seed": None, "steps": None, "cfg": None, "loras": None,
        }],
        "media": [], "audio": [], "ic_lora": [],
    }
    # Wan's `video` mode branches its two model-set loaders on the routing flags
    # (needs_t2v_set/needs_i2v_set) normalize_video_director precomputes; add
    # them so the Director pipeline renders. Harmless for LTX (its pipeline
    # never reads them or the per-segment sub_type this also attaches).
    doc.update(derive_segment_routing(doc["segments"], doc["media"]))
    doc.update(derive_ltx_media_fields(doc["media"], doc["ic_lora"], doc["settings"]["fps"]))
    return doc


def load_fixture_form_data(fixture_path: Path) -> Dict[str, Any]:
    import yaml
    with open(fixture_path, "r") as f:
        data = yaml.load(f, Loader=yaml.FullLoader) or {}
    return data


_PAIRS_INDEX_RE = re.compile(r"prompts\.pairs\[(\d+)\]")


def _max_pairs_index(mode_data: ModeTemplate) -> int:
    """Highest literal `generation.prompts.pairs[N]` index any pipe in this
    mode reads. Some fixed-segment-count presets (e.g. WAN 2.2 SVI20 Pro's
    img2vid, a 6-node ComfyUI workflow) index pairs directly by position
    rather than looping over whatever the real submission sent - a
    single-element fixture `prompts` list would raise "list object has no
    element N" under the strict evaluator, which raises loudly instead of
    rendering garbage on an out-of-range index. Not a templating bug, just
    this harness needing enough (identical, so still deterministic) fixture
    pairs."""
    highest = -1
    for pipe in mode_data.pipes:
        for match in _PAIRS_INDEX_RE.finditer(str(pipe.enabled) + str(pipe.configuration)):
            highest = max(highest, int(match.group(1)))
    return highest


def build_generation_data(mode: str, form_data: Dict[str, Any], mode_data: Optional[ModeTemplate] = None) -> Dict[str, Any]:
    pair = {"positive": FIXED_POSITIVE_PROMPT, "negative": FIXED_NEGATIVE_PROMPT}
    count = 1
    if mode_data is not None:
        count = max(1, _max_pairs_index(mode_data) + 1)
    return {
        "mode": mode,
        "form_data": form_data,
        "prompts": [pair] * count,
        "image": None,
        "mask": None,
        "numpy": None,
    }


# ---------------------------------------------------------------------------
# Rendering + flattening
# ---------------------------------------------------------------------------

def _flatten(value: Any, prefix: str = "") -> Dict[str, Dict[str, Any]]:
    """Flatten a (possibly nested) config dict into dotted-path leaves, each
    recorded as {"value": ..., "type": <python type name>}. Non-dict values
    (including lists) are leaves - printed/stored as a whole, not exploded by
    index, per the CLI's "lists/dicts printed compactly" contract."""
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(value, dict) and value:
        for key, sub in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(sub, dict) and sub:
                out.update(_flatten(sub, path))
            else:
                out[path] = {"value": sub, "type": type(sub).__name__}
    elif prefix:
        out[prefix] = {"value": value, "type": type(value).__name__}
    return out


def _normalize_repo_paths(value: Any) -> Any:
    """Rewrite absolute repo paths to repo-relative in rendered records.

    Rendered configs and error messages can embed filesystem paths resolved
    against the repo checkout; snapshots must be machine-independent.
    """
    if isinstance(value, str):
        return value.replace(str(ROOT) + "/", "").replace(str(ROOT), ".")
    if isinstance(value, dict):
        return {k: _normalize_repo_paths(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_repo_paths(v) for v in value]
    return value


def render_preset_mode(
    processor: PresetProcessor,
    form_serializer: PresetFormSerializer,
    preset: PresetTemplate,
    mode: str,
    form_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Render one preset/mode and return the golden-record dict:
    {preset_id, preset_name, mode, pipes: [{name, id, enabled, config}]} or
    {preset_id, preset_name, mode, error: "..."} if rendering raised.
    All strings are normalized to repo-relative paths."""
    try:
        if form_data is None:
            form_data = build_fixture_form_data(form_serializer, preset, mode)

        generation_data = build_generation_data(mode, form_data, preset.modes.get(mode))
        pipes = processor.process(preset, generation_data)

        rendered_pipes = []
        for pipe in pipes:
            enabled_value = pipe["enabled"]
            rendered_pipes.append({
                "name": pipe["name"],
                "id": pipe["id"],
                "enabled": {"value": enabled_value, "type": type(enabled_value).__name__},
                "config": _flatten(pipe.get("config") or {}),
            })

        return _normalize_repo_paths({
            "preset_id": preset.id,
            "preset_name": preset.name,
            "mode": mode,
            "pipes": rendered_pipes,
        })
    except Exception as e:
        return _normalize_repo_paths({
            "preset_id": preset.id,
            "preset_name": preset.name,
            "mode": mode,
            "error": f"{type(e).__name__}: {e}",
        })


def pipe_label(pipe: Dict[str, Any], index: int) -> str:
    pid = pipe.get("id")
    if pid:
        return str(pid)
    return f"{pipe.get('name', 'pipe')}#{index}"


def print_render(record: Dict[str, Any]) -> None:
    if "error" in record:
        print(f"# {record['preset_name']} ({record['preset_id']}) / {record['mode']}: ERROR: {record['error']}")
        return

    for index, pipe in enumerate(record["pipes"]):
        label = pipe_label(pipe, index)
        enabled = pipe["enabled"]
        print(f"{label}.enabled\t<{enabled['type']}>\t{enabled['value']!r}")
        for dotted_path, leaf in sorted(pipe["config"].items()):
            print(f"{label}.config.{dotted_path}\t<{leaf['type']}>\t{leaf['value']!r}")


# ---------------------------------------------------------------------------
# --golden-all
# ---------------------------------------------------------------------------

def golden_filename(preset: PresetTemplate, mode: str) -> str:
    safe_id = preset.id.replace("/", "_")
    return f"{safe_id}__{mode}.json"


def run_golden_all(out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    presets, load_errors = load_all_presets(include_plugins=False)
    processor = build_processor()
    form_serializer = build_form_serializer()

    written = []
    errored = []
    total_values = 0

    for preset in sorted(presets, key=lambda p: p.id):
        for mode in sorted(preset.modes.keys()):
            record = render_preset_mode(processor, form_serializer, preset, mode)
            if "error" in record:
                errored.append((preset.name, preset.id, mode, record["error"]))
            else:
                for pipe in record["pipes"]:
                    total_values += 1 + len(pipe["config"])  # +1 for `enabled`

            filename = golden_filename(preset, mode)
            path = out_dir / filename
            with open(path, "w") as f:
                json.dump(record, f, indent=2, sort_keys=True, ensure_ascii=True)
                f.write("\n")
            written.append(str(path))

    return {
        "written": written,
        "preset_count": len(presets),
        "snapshot_count": len(written),
        "total_values": total_values,
        "errored": errored,
        "loader_load_errors": load_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("preset", nargs="?", help="Preset id or directory (or suffix of one)")
    parser.add_argument("mode", nargs="?", help="Mode name, e.g. txt2img")
    parser.add_argument("--form", type=Path, default=None, help="YAML file of form_data overrides (replaces the fixture walk)")
    parser.add_argument("--json", action="store_true", help="Print the golden record as JSON instead of the plain-text listing")
    parser.add_argument("--golden-all", action="store_true", help="Render every preset x mode and write golden snapshots")
    parser.add_argument("--golden", action="store_true", help="Write this single preset+mode's render as a golden snapshot instead of printing it; leaves every other golden file untouched")
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "golden" / "preset_renders", help="Output directory for --golden-all / --golden")
    args = parser.parse_args()

    if args.golden_all:
        summary = run_golden_all(args.out)
        print(f"Wrote {summary['snapshot_count']} golden files "
              f"({summary['preset_count']} presets) to {args.out}")
        print(f"Total captured values (enabled + config leaves): {summary['total_values']}")
        if summary["errored"]:
            print(f"Presets/modes that error-render ({len(summary['errored'])}):")
            for name, pid, mode, err in summary["errored"]:
                print(f"  - {name} ({pid}) / {mode}: {err}")
        if summary["loader_load_errors"]:
            print(f"Presets that failed to LOAD at all ({len(summary['loader_load_errors'])}):")
            for path, errs in summary["loader_load_errors"].items():
                print(f"  - {path}: {errs}")
        return 0

    if not args.preset or not args.mode:
        parser.error("preset and mode are required unless --golden-all is given")

    presets, _ = load_all_presets()
    preset = find_preset(presets, args.preset)
    if preset is None:
        print(f"No preset found matching {args.preset!r}", file=sys.stderr)
        return 1
    if args.mode not in preset.modes:
        print(f"Preset {preset.name!r} has no mode {args.mode!r} (available: {sorted(preset.modes.keys())})", file=sys.stderr)
        return 1

    processor = build_processor()
    form_serializer = build_form_serializer()
    form_data = load_fixture_form_data(args.form) if args.form else None
    record = render_preset_mode(processor, form_serializer, preset, args.mode, form_data=form_data)

    if args.golden:
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / golden_filename(preset, args.mode)
        with open(path, "w") as f:
            json.dump(record, f, indent=2, sort_keys=True, ensure_ascii=True)
            f.write("\n")
        print(f"Wrote {path}")
        if "error" in record:
            print(f"  (rendered as an error record: {record['error']})")
        return 0

    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True))
    else:
        print_render(record)

    return 0


if __name__ == "__main__":
    sys.exit(main())
