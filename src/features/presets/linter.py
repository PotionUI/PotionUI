"""
Preset lint: schema validation plus cross-checks that the schema itself
cannot express (things on disk, not inside a single YAML file).

Used by `scripts/preset_lint.py` and `GET /api/developer/presets/lint`.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from src.features.media.image_processor import ImageProcessor

from ..fields.camera_shot_taxonomy import CATEGORY_KEYS, valid_shot_keys
from src.platform.runtime.native.sampling.registry import (
    ANY_FAMILY,
    sampler_registry,
    schedule_registry,
)
from .loader import discover_form_variants, plugin_preset_mode_contributions, _CHILDREN_PATH_VAR_RE
from .schema import (
    SPEED_PROFILE_KNOWN_KEYS,
    validate_manifest,
    validate_form_file,
    validate_field_list,
    validate_styles_file,
    _format_errors,
)
from .tests_schema import NEEDS_MODEL_TAG, PLACEHOLDER_SHA256, validate_tests_yml


def _is_exact_expression(value: str) -> bool:
    """True if ``value`` is exactly one ``{{ expression }}`` block.

    Mirrors ``src/platform/templating/processor._extract_exact_expression``'s shape
    test (kept as a tiny local copy so the linter doesn't drag in the whole
    Jinja/injector-backed processor just to answer a yes/no question): after
    stripping, it must start ``{{`` and end ``}}`` with no further
    ``{{``/``}}``/``{%``/``%}`` inside - i.e. one expression, no surrounding
    text, no second block, no statement tag. A value shaped this way is
    natively evaluated to a typed Python value; anything else is string-rendered.
    """
    stripped = value.strip()
    if not (stripped.startswith("{{") and stripped.endswith("}}")):
        return False
    inner = stripped[2:-2]
    return not ("{{" in inner or "}}" in inner or "{%" in inner or "%}" in inner)


# Model families a `sampler`/`schedule` field's `configuration.family` may
# name, unioned at check time with whatever families the live registry's
# entries actually declare (a plugin-contributed family not yet in this fixed
# list is still accepted) - see `_lint_sampling_fields`.
_KNOWN_SAMPLING_FAMILIES = frozenset({
    "anima", "flux", "krea2", "ltx", "minimax_h3", "qwen_image", "wan",
    "z_image", "seedvr2", "minimax_music3",
})

# Runtime documents the orchestrator injects into the `form` context that are
# not declared form fields (Video Director timeline, Music Director document,
# prompt timeline, LLM block). A `{{ form.<key> }}` reference to one of these
# is legitimate even though no field defines it, so the form-reference check
# treats them as known.
_INJECTED_FORM_KEYS = frozenset({"video_director", "music_director", "timeline", "llm", "prompt_timeline"})

# `{{ loop.index }}` / `{{ loop.index0 }}` inside a form `@loop` template's
# field names, expanded statically (1..count / 0..count-1) so the generated
# per-iteration field names are known to the form-reference check.
_LOOP_INDEX_RE = re.compile(r"\{\{\s*loop\.index\s*\}\}")
_LOOP_INDEX0_RE = re.compile(r"\{\{\s*loop\.index0\s*\}\}")

# A `{{ ... }}` or `{% ... %}` region inside a scalar. Deleted-context tokens
# and `form.<name>` references are only meaningful inside these - never in the
# surrounding literal text (a `file_path: "input.png"` is not a legacy
# `input.` context reference).
_TEMPLATE_REGION_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)

# Top-level `form.<name>` reference (captures the first identifier after
# `form.`; `form.loras[0].strength` and `form.a.b` both capture the root name).
_FORM_REF_RE = re.compile(r"\bform\.([A-Za-z_][A-Za-z0-9_]*)")

# Deleted render globals/filters (call form) that the templating rework
# removed - a leftover is a hard build error under strict eval, so lint them
# as errors. `(?<![.\w])` avoids flagging a method/attribute that merely
# shares the name; it also lets a filter usage (`| matches(...)`) match, since
# the char right before the name is `|`/whitespace, never `.`/a word char.
# `path`/`get_path_for`/`icon`/`get_icon` (globals) and `matches`/`regex_search`
# (filter) never had a real preset consumer and were dropped alongside
# `preset.speed_profiles`/`preset.configuration`/`generation.seed`/
# `generation.quantity` below.
_DELETED_CALL_RE = re.compile(
    r"(?<![.\w])(get_form|get_is_in|contains|value|setting|config"
    r"|path|get_path_for|icon|get_icon|matches|regex_search)\s*\("
)
# Deleted `input.*` context (input.form.x / input['form']['x']).
_DELETED_INPUT_RE = re.compile(r"(?<![.\w])input\s*[.\[]")
# Deleted dotted context paths: `preset.speed_profiles`/`preset.configuration`
# direct access, and `generation.seed`/`generation.quantity` (the seed_generator
# pipe's own `seed`/`quantity` config, fed from `form.seed`/`form.quantity`,
# is the only real consumer either ever had).
_DELETED_DOTTED_RE = re.compile(
    r"(?<![.\w])(preset\.speed_profiles|preset\.configuration"
    r"|generation\.seed|generation\.quantity)\b"
)

# Per-token migration hint appended to the generic deleted-context message
# below - a token with no entry here falls back to the generic context-roots
# list the message already names.
_DELETED_TOKEN_MIGRATION_HINTS = {
    "preset.speed_profiles": "use get_speed_profile(name) for an explicit lookup, "
        "or generation.profile for the profile resolved for this request",
    "preset.configuration": "use \"@config:<key>\" indirection on the field's own "
        "configuration instead (see docs/presets.md 'Configuration (admin-set)')",
    "generation.seed": "use form.seed - the seed_generator pipe's own `seed` config "
        "is the only real consumer",
    "generation.quantity": "use form.quantity - the seed_generator pipe's own "
        "`quantity` config is the only real consumer",
    "path(": "no real preset ever used it - resolve the path some other way",
    "get_path_for(": "no real preset ever used it - resolve the path some other way",
    "icon(": "use a literal icon name (every shipped preset already does)",
    "get_icon(": "use a literal icon name (every shipped preset already does)",
    "matches(": "no real preset ever used it - use another filter or a plain comparison",
    "regex_search(": "no real preset ever used it - use another filter or a plain comparison",
}

# NAG (docs/techniques/nag.md): a generator pipe's `nag_scale` only takes
# effect if the mode's `prompt_encoder` pipe mirrors it - `prompt_encoder.
# _do_cfg()` encodes a negative pass on `guidance_scale > 1.0` OR
# `nag_scale > 1.0`, so an un-mirrored generator `nag_scale` never gets a
# negative conditioning to attach to and silently does nothing.
_NAG_MIRROR_KEY = "nag_scale"

# SLG (`slg_*`) and RIFLEx (`riflex*`) are only honored by WanModel's forward
# (src/platform/runtime/native/arch/wan/model.py) - see
# guidance_options.slg_settings_config_specs/riflex_config_specs docstrings.
# `validate_pipe_configuration` preserves any key not in a pipe's own
# `configuration()` spec as an "injected parameter" rather than rejecting it,
# so setting these on a non-Wan generator pipe is accepted at load time and
# then never read anywhere - accepted-but-inert, not a build error.
_SLG_RIFLEX_KEYS = ("slg_scale", "slg_layers", "slg_sigma_start", "slg_sigma_end", "riflex", "riflex_trained_frames")
_WAN_GUIDANCE_GENERATOR_PIPES = frozenset({
    "generator/txt2vid_wan22", "generator/chain_video_wan22", "generator/img2vid_wan22",
})

# Above this, warn - mirrors the generation thumbnail sizes (image_handler.py), just
# applied to the source file instead of a generated derivative.
_MEDIA_MAX_BYTES = 2 * 1024 * 1024
_MEDIA_MAX_DIMENSION = 4096
_MEDIA_RESIZABLE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# tests.yml: a real sha256 is exactly 64 hex digits. PLACEHOLDER_SHA256 (all
# zeros) also matches this shape by construction - it is a deliberate,
# never-collides-with-a-real-hash sentinel, not something the regex needs to
# special-case (see tests_schema.py's module docstring for the convention).
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# `form.<name> | default(<expr>)` - the same shape extract_defaults.py-style
# tooling looks for. The default's argument allows one level of nested
# parens (a call like `default(get_speed_profile('x').steps)`), mirroring
# the balanced-paren trick used to build this pattern.
_GUARD_DEFAULT_RE = re.compile(
    r"\bform\.([A-Za-z_][A-Za-z0-9_]*)\s*\|\s*default\(([^()]*(?:\([^()]*\)[^()]*)*)\)"
)

# A `default(...)` argument recognized as a plain literal (number, quoted
# string, bool, null, or an empty list) - anything else (a dotted lookup like
# `preset.vars.default_cfg`, a function call, a non-empty list/dict) is an
# EXPRESSION, not a value the guard/default-mismatch check can compare
# against a field's own `default:` without evaluating Jinja.
_GUARD_LITERAL_RE = re.compile(
    r"^(?:-?\d+\.\d+|-?\d+|'[^']*'|\"[^\"]*\"|true|false|True|False|null|None|\[\s*\])$"
)


def _parse_guard_literal(expr: str) -> Tuple[bool, Any]:
    """`(is_literal, value)` for one `default(<expr>)` argument's raw text."""
    text = expr.strip()
    if not _GUARD_LITERAL_RE.match(text):
        return False, None
    try:
        return True, yaml.safe_load(text)
    except Exception:
        return False, None


def _literal_eq(a: Any, b: Any) -> bool:
    """`a == b`, but bool/int never cross-match - Python's `bool` is an `int`
    subclass, so bare `==` would let a guard `default(0)` "match" a
    checkbox's `default: false` (mirrors `_js_strict_eq`'s reasoning in
    src/features/forms/binding.py for the same hazard)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


@dataclass
class LintIssue:
    level: str  # "error" | "warning" | "info"
    preset_path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.preset_path}: {self.message}"


class _UncheckedRequirementSchema(BaseModel):
    """Accepts any entry shape - used only when a plugin-declared checker's
    backend class couldn't be imported for a standalone lint run (see
    `PresetLinter._requirement_checker_registry`), so there is no real schema
    left to validate an entry's own arguments against."""

    model_config = ConfigDict(extra="allow")


class _UncheckedPluginRequirementChecker:
    """Placeholder registered under a plugin-declared `requirements:` type
    name when that plugin's checker backend class couldn't be imported here
    (lint-only - never asked to actually `check()` anything)."""

    schema = _UncheckedRequirementSchema

    def __init__(self, type_name: str):
        self.type = type_name

    async def check(self, spec, ctx):
        raise NotImplementedError("lint-only placeholder - never evaluated at runtime")


class PresetLinter:
    """
    Lints one or more preset root directories.

    Reuses schema.py for per-file validation, and additionally checks:
    - mode directories present on disk but missing from preset.yml's `modes:` list
    - option-file references (`files/form/*.yml` and `files/*.yml` paths embedded
      in field configuration) that don't exist on disk
    - duplicate preset ids across the scanned tree
    """

    def __init__(
        self,
        paths: List[str],
        plugin_manifests: Optional[List[Any]] = None,
        requirement_checker_registry: Optional[Any] = None,
        pipe_catalog: Optional[Any] = None,
    ):
        """`plugin_manifests`: discovered `PluginManifest`s (see
        `scripts/preset_lint.py`), used only to cross-check `preset_modes:`
        contributions against the presets found under `paths` - a
        plugin outside `paths` can still target a preset inside them. `None`/
        empty skips that cross-check entirely (mirrors how an explicit
        `paths` invocation already skips plugin-owned `presets:` roots). Also
        feeds the pipe catalog built by `_pipe_catalog` (below) so a
        plugin-contributed pipe is checked against too.

        `requirement_checker_registry`: an already-built
        `RequirementCheckerRegistry` to validate `requirements:` entries
        against - pass the live one (`get_container().requirement_checker_registry`)
        when linting inside a running app, so a plugin enabled at runtime (not
        just at boot) is seen. Takes precedence over `plugin_manifests` for
        that purpose; `None` falls back to building one from
        `plugin_manifests` (the standalone-script path).

        `pipe_catalog`: an already-built `PipeCatalog` to check pipeline
        `name:`/`configuration:` entries against - pass the live one
        (`get_container().pipe_catalog`) when linting inside a running app,
        same rationale as `requirement_checker_registry`. `None` falls back to
        building one from `plugin_manifests` lazily (see `_pipe_catalog`)."""
        self.paths = [Path(p) for p in paths]
        self.plugin_manifests = plugin_manifests or []
        self._image_processor = ImageProcessor()
        # type_name -> set(declared FieldConfigSpec names) | None (no backend
        # schema class - see `_field_config_spec_names`). Memoized per linter
        # run since `configuration()` is a pure classmethod call.
        self._config_spec_cache: Dict[str, Optional[set]] = {}
        # Built lazily by `_requirement_checker_registry`, memoized per linter
        # run - `_req_checker_warnings` is folded into `lint()`'s result once
        # the registry has been built. Pre-set here when the caller passed
        # one in explicitly.
        self._req_checker_registry = requirement_checker_registry
        self._req_checker_warnings: List[LintIssue] = []
        # Built lazily by `_pipe_catalog`, memoized per linter run. Pre-set
        # here when the caller passed one in explicitly.
        self._pipe_catalog_instance = pipe_catalog
        # pipe name -> imported class | None (unknown to the catalog, or its
        # module failed to import) - memoized per linter run by `_load_pipe_class`.
        self._pipe_class_cache: Dict[str, Optional[type]] = {}
        # Pipe names already reported as import failures this run, so
        # `_load_pipe_class` queues one informational LintIssue per name, not
        # one per preset that happens to reference it.
        self._pipe_import_notes_seen: set = set()
        self._pipe_import_notes: List[LintIssue] = []

    def lint(self) -> List[LintIssue]:
        issues: List[LintIssue] = []
        seen_ids: Dict[str, str] = {}
        # target preset id -> (preset.yml path, validated manifest) - only for
        # presets that loaded cleanly enough to have a manifest at all, fed to
        # the preset_modes cross-check after every core preset is known.
        loaded_manifests: Dict[str, Tuple[Path, Any]] = {}

        for base_path in self.paths:
            if not base_path.exists():
                continue

            for preset_file in sorted(base_path.rglob("preset.yml")):
                file_issues, manifest = self._lint_preset(preset_file, seen_ids)
                issues.extend(file_issues)
                if manifest is not None:
                    loaded_manifests[manifest.id] = (preset_file, manifest)

        issues.extend(self._lint_preset_mode_contributions(loaded_manifests))
        issues.extend(self._req_checker_warnings)
        issues.extend(self._pipe_import_notes)

        return issues

    def _pipe_catalog(self):
        """The `PipeCatalog` to check pipeline `name:`/`configuration:`
        entries against. If `__init__` was given one explicitly, that is
        returned as-is (the in-app, live-container path - see `__init__`'s
        docstring). Otherwise built once per linter run and memoized on
        `self`, treating every discovered manifest as enabled - the same
        no-DB posture `scripts/preset_render.py`'s `_plugin_registry_stub`
        uses, since a standalone lint run has no DB to ask which plugins are
        really enabled.

        Falls back to discovering plugins from disk (`PluginLoader.
        discover_plugins()` - a read-only manifest.yml scan, no imports, no
        app boot) when `self.plugin_manifests` is empty, rather than trusting
        the caller remembered to pass it: several `PresetLinter(...)` call
        sites (a plugin's own preset-import flow via `lint_preset_dir`, a
        bare test construction) don't, and a plugin-contributed pipe name -
        the ComfyUI backend plugin's `comfyui` pipe is the load-bearing
        example - must never be mistaken for a typo just because the caller
        didn't discover plugins itself. `scripts/preset_lint.py`'s own CLI
        entry point already always runs this same scan unconditionally; this
        just means every caller gets it, not only that one.

        Uses the SAME relative paths `build_container()` does
        (`src/bootstrap/container.py`) - correct when run from the repo root,
        which is how `scripts/preset_lint.py` and pytest are always invoked.
        """
        if self._pipe_catalog_instance is not None:
            return self._pipe_catalog_instance

        from src.pipelines.catalog import PipeCatalog

        manifests = self.plugin_manifests
        if not manifests:
            from src.platform.plugins.loader import PluginLoader

            manifests = PluginLoader().discover_plugins()

        plugin_registry = None
        if manifests:
            plugin_registry = SimpleNamespace(get_enabled_plugins=lambda: manifests)

        self._pipe_catalog_instance = PipeCatalog(
            "src/pipelines/pipes", "pipes/custom", plugin_registry=plugin_registry
        )
        return self._pipe_catalog_instance

    def _load_pipe_class(self, name: str) -> Optional[type]:
        """Import and return the pipe class registered as `name`, or `None`
        if the catalog's light-scan tier doesn't know this name at all, or
        importing its module fails.

        `PipeCatalog.get_pipe()` never raises on a broken import - it logs
        and returns `None` (see `_load_pipe_module`) - so a location known
        but unimportable is reported here as ONE informational `LintIssue`
        per pipe NAME per linter run (`_pipe_import_notes`, folded into
        `lint()`'s result), not a crash: several pipe families can't import
        outside a real app/GPU environment (docs/testing-notes.md), and the
        config-key/wiring checks simply skip a pipe they can't inspect rather
        than failing the whole lint pass over an environment gap.

        Memoized on `self` for the life of one linter run - `configuration()`/
        `inputs()`/`outputs()` are pure classmethod calls once a class is
        imported, and importing is the expensive, one-time part.
        """
        if name in self._pipe_class_cache:
            return self._pipe_class_cache[name]

        catalog = self._pipe_catalog()
        if catalog.get_pipe_source(name) is None:
            self._pipe_class_cache[name] = None
            return None

        pipe_class = catalog.get_pipe(name)
        if pipe_class is None and name not in self._pipe_import_notes_seen:
            self._pipe_import_notes_seen.add(name)
            self._pipe_import_notes.append(
                LintIssue(
                    "info",
                    "<pipe catalog>",
                    f"pipe '{name}': its module could not be imported for lint (see the process "
                    f"log for the exception) - config-key and wiring checks are skipped for it here; "
                    f"docs/testing-notes.md catalogues known-broken imports in this environment",
                )
            )

        self._pipe_class_cache[name] = pipe_class
        return pipe_class

    def _lint_preset(self, preset_file: Path, seen_ids: Dict[str, str]) -> Tuple[List[LintIssue], Any]:
        issues: List[LintIssue] = []
        preset_str = str(preset_file)

        try:
            with open(preset_file, 'r') as f:
                data = yaml.load(f, Loader=yaml.FullLoader) or {}
        except Exception as e:
            issues.append(LintIssue("error", preset_str, f"preset.yml: failed to parse: {e}"))
            return issues, None

        manifest, errors = validate_manifest(data)
        for err in errors:
            issues.append(LintIssue("error", preset_str, err))

        if manifest is None:
            return issues, None

        # Duplicate id check
        if manifest.id in seen_ids and seen_ids[manifest.id] != preset_str:
            issues.append(
                LintIssue(
                    "error",
                    preset_str,
                    f"duplicate id '{manifest.id}' also used by {seen_ids[manifest.id]}",
                )
            )
        else:
            seen_ids[manifest.id] = preset_str

        # Orphaned mode dirs: present on disk but not declared in modes:
        modes_root = preset_file.parent / "modes"
        if modes_root.exists():
            declared = set(manifest.modes)
            for mode_dir in modes_root.iterdir():
                if mode_dir.is_dir() and mode_dir.name not in declared:
                    issues.append(
                        LintIssue(
                            "warning",
                            preset_str,
                            f"modes/{mode_dir.name}: directory exists on disk but is not listed in preset.yml modes:",
                        )
                    )

        # Missing mode dirs: declared but not on disk (loader would already skip these,
        # surfaced here too for a single lint pass)
        for mode_name in manifest.modes:
            mode_dir = preset_file.parent / "modes" / mode_name
            if not mode_dir.exists():
                issues.append(
                    LintIssue("error", preset_str, f"modes/{mode_name}: directory not found")
                )
                continue
            issues.extend(self._lint_option_file_refs(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_form_variants(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_configuration_refs(preset_file, mode_dir, mode_name, manifest))
            issues.extend(self._lint_field_defaults(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_camera_shot_fields(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_pipeline_templates(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_field_config_keys(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_sampling_fields(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_alert_field_config(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_pipe_names(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_pipe_config_keys(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_pipeline_wiring(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_config_exact_expression(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_variant_form_refs(preset_file, mode_dir, mode_name))
            issues.extend(self._lint_guard_default_mismatch(preset_file, mode_dir, mode_name))

        issues.extend(self._lint_media_refs(preset_file, manifest))

        issues.extend(self._lint_styles(preset_file))

        issues.extend(self._lint_engine_matches_pipes(preset_file, manifest))

        issues.extend(self._lint_speed_profiles(preset_file, manifest))

        issues.extend(self._lint_segment_templates(preset_file, manifest))

        issues.extend(self._lint_requirements(preset_file, manifest))

        issues.extend(self._lint_tests_yml(preset_file, manifest))

        return issues, manifest

    def _lint_preset_mode_contributions(
        self, loaded_manifests: Dict[str, Tuple[Path, Any]]
    ) -> List[LintIssue]:
        """Cross-check plugin `preset_modes:` contributions against the
        presets found under `self.paths` - the same collision rules
        `PresetTemplateLoader._apply_preset_mode_contributions` enforces at
        runtime, run here so a broken contribution shows up in `preset_lint`
        without needing the app booted. Each contributed mode is validated
        through the SAME per-mode checks a core mode gets (option-file refs,
        form variants, `@config:` refs against the TARGET's declared
        configuration, field defaults, pipeline templates) - no second
        validator, just pointed at the plugin's directory instead of the
        target preset's.
        """
        issues: List[LintIssue] = []
        if not self.plugin_manifests:
            return issues

        claimed: Dict[Tuple[str, str], str] = {}
        for contribution in plugin_preset_mode_contributions(self.plugin_manifests):
            target = loaded_manifests.get(contribution.target_preset_id)
            if target is None:
                continue
            _, target_manifest = target

            modes_dir = contribution.modes_root / "modes"
            location = f"plugin '{contribution.plugin_id}' preset_modes -> '{contribution.target_preset_id}'"
            if not modes_dir.exists():
                issues.append(
                    LintIssue("error", location, f"modes_root '{contribution.modes_root}' has no modes/ directory")
                )
                continue

            # Synthetic, never-read preset.yml path rooted at the plugin's
            # modes_root - the reused per-mode checks below only ever use its
            # `.parent` (to resolve this contribution's OWN relative
            # examples/option-file/children-fragment paths), never open it.
            synthetic_preset_file = contribution.modes_root / "preset.yml"

            for mode_dir in sorted(p for p in modes_dir.iterdir() if p.is_dir()):
                mode_name = mode_dir.name
                mode_location = f"{location} modes/{mode_name}"

                if mode_name in target_manifest.modes:
                    issues.append(
                        LintIssue(
                            "error",
                            mode_location,
                            f"collides with a core mode already declared by preset "
                            f"'{contribution.target_preset_id}' - the core mode wins, "
                            f"this contribution is rejected",
                        )
                    )
                    continue

                claim_key = (contribution.target_preset_id, mode_name)
                prior_claimant = claimed.get(claim_key)
                if prior_claimant is not None:
                    issues.append(
                        LintIssue(
                            "error",
                            mode_location,
                            f"mode '{mode_name}' on preset '{contribution.target_preset_id}' was "
                            f"already contributed by plugin '{prior_claimant}' - first contributor "
                            f"(by plugin id, then declaration order) wins, this contribution is rejected",
                        )
                    )
                    continue
                claimed[claim_key] = contribution.plugin_id

                if not (mode_dir / "pipeline.yml").exists():
                    issues.append(LintIssue("error", mode_location, "pipeline.yml: file not found"))
                    continue

                issues.extend(self._lint_option_file_refs(synthetic_preset_file, mode_dir, mode_name))
                issues.extend(self._lint_form_variants(synthetic_preset_file, mode_dir, mode_name))
                issues.extend(self._lint_configuration_refs(synthetic_preset_file, mode_dir, mode_name, target_manifest))
                issues.extend(self._lint_field_defaults(synthetic_preset_file, mode_dir, mode_name))
                issues.extend(self._lint_pipeline_templates(synthetic_preset_file, mode_dir, mode_name))
                issues.extend(self._lint_field_config_keys(synthetic_preset_file, mode_dir, mode_name))
                issues.extend(self._lint_alert_field_config(synthetic_preset_file, mode_dir, mode_name))

        return issues

    def _lint_speed_profiles(self, preset_file: Path, manifest) -> List[LintIssue]:
        """Cross-checks for `speed_profiles:` (roadmap 3.6) the schema itself
        cannot express without making a typo'd/forward-compat key or an
        unused block a hard preset-load failure:

        - unknown keys per profile (schema allows them structurally so the
          preset still loads; this is where they actually get flagged);
        - a preset that declares profiles nothing in its modes ever reads
          (textual scan, same reasoning as `_lint_engine_matches_pipes`:
          pipeline.yml is a Jinja template, not guaranteed to parse as plain
          YAML, so this can't be a structural check). Three access idioms are
          recognized: the `get_speed_profile('name')` helper call, `generation.profile`
          (the request-resolved profile - see docs/presets.md "Speed profiles"),
          and a legacy direct Jinja lookup on `preset.speed_profiles`
          (`preset.speed_profiles.name` dot access or
          `preset.speed_profiles['name']` subscript) — the latter has no
          quoted profile-name literal in the dot form, so it needs its own
          pattern rather than relying on the generic quoted-name scan below.
          `preset.speed_profiles` direct access is itself now a hard build
          error (`_lint_pipeline_templates`'s deleted-context check) - it's
          still recognized here too so a not-yet-migrated preset gets that
          error instead of ALSO this warning piled on top.

        Type errors (e.g. `steps: "fast"`) are NOT handled here - they are
        already schema-level errors surfaced by `validate_manifest` above,
        since `SpeedProfile`'s known fields are typed.
        """
        issues: List[LintIssue] = []
        profiles = getattr(manifest, "speed_profiles", None)
        if not profiles:
            return issues

        preset_str = str(preset_file)
        known = SPEED_PROFILE_KNOWN_KEYS - {"extra"}

        for profile_name, profile in profiles.items():
            unknown_keys = sorted((profile.model_extra or {}).keys())
            if unknown_keys:
                issues.append(
                    LintIssue(
                        "warning",
                        preset_str,
                        f"speed_profiles.{profile_name}: unknown key(s) {unknown_keys} - "
                        f"move forward-compat data under 'extra:' or use a known key "
                        f"({sorted(known)})",
                    )
                )

        profile_names = set(profiles.keys())
        referenced = False
        for mode_name in manifest.modes:
            mode_dir = preset_file.parent / "modes" / mode_name
            if not mode_dir.exists():
                continue
            for text_file in mode_dir.rglob("*.yml"):
                try:
                    raw = text_file.read_text()
                except Exception:
                    continue
                if "get_speed_profile" in raw or "generation.profile" in raw or any(
                    re.search(rf'["\']{re.escape(name)}["\']', raw)
                    # direct Jinja lookup: preset.speed_profiles.NAME (dot access)
                    # or preset.speed_profiles['NAME']/["NAME"] (subscript) -- the
                    # dot form has no quoted literal, so the generic quoted-name
                    # scan above never sees it.
                    or re.search(rf'speed_profiles\.{re.escape(name)}\b', raw)
                    or re.search(rf'speed_profiles\[["\']{re.escape(name)}["\']\]', raw)
                    for name in profile_names
                ):
                    referenced = True
                    break
            if referenced:
                break

        if not referenced:
            issues.append(
                LintIssue(
                    "warning",
                    preset_str,
                    f"speed_profiles declares {sorted(profile_names)} but no form field or "
                    f"pipeline.yml under modes/ appears to reference them (no get_speed_profile() "
                    f"call, no generation.profile use, and no literal profile name found)",
                )
            )

        return issues

    def _lint_segment_templates(self, preset_file: Path, manifest) -> List[LintIssue]:
        """Cross-checks `vars.prompt.segment_templates` and its per-mode override
        under `vars.prompt.modes.<mode>.segment_templates` (see
        docs/presets/manifest.md "Prompt segment templates") - `vars:` is
        deliberately untyped (schema.py, `PresetManifest.vars`), so this soft
        check is the only place a malformed declaration gets caught before it
        silently fails to render in the segment picker."""
        issues: List[LintIssue] = []
        preset_str = str(preset_file)

        prompt_vars = manifest.vars.get("prompt")
        if not isinstance(prompt_vars, dict):
            return issues

        flat = prompt_vars.get("segment_templates")
        if flat is not None:
            issues.extend(
                self._lint_segment_template_list(preset_str, "vars.prompt.segment_templates", flat)
            )

        modes = prompt_vars.get("modes")
        if modes is None:
            return issues
        if not isinstance(modes, dict):
            issues.append(LintIssue("warning", preset_str, "vars.prompt.modes: must be a mapping"))
            return issues

        declared_modes = set(manifest.modes)
        for mode_key, mode_val in modes.items():
            path = f"vars.prompt.modes.{mode_key}"
            if mode_key not in declared_modes:
                issues.append(
                    LintIssue(
                        "warning",
                        preset_str,
                        f"{path}: mode '{mode_key}' is not declared in preset.yml modes:",
                    )
                )
            if not isinstance(mode_val, dict):
                issues.append(LintIssue("warning", preset_str, f"{path}: must be a mapping"))
                continue
            mode_templates = mode_val.get("segment_templates")
            if mode_templates is not None:
                issues.extend(
                    self._lint_segment_template_list(preset_str, f"{path}.segment_templates", mode_templates)
                )

        return issues

    def _lint_segment_template_list(self, preset_str: str, path: str, value: Any) -> List[LintIssue]:
        """Validates one `segment_templates:` list (either the flat
        `vars.prompt.segment_templates` or a per-mode override) against the
        shape documented in docs/presets/manifest.md - shared by
        `_lint_segment_templates` for both call sites so the same entry rules
        apply flat and per-mode."""
        issues: List[LintIssue] = []
        if not isinstance(value, list):
            issues.append(LintIssue("warning", preset_str, f"{path}: must be a list"))
            return issues

        for i, entry in enumerate(value):
            entry_path = f"{path}[{i}]"
            if not isinstance(entry, dict):
                issues.append(LintIssue("warning", preset_str, f"{entry_path}: must be a mapping"))
                continue
            if not entry.get("name"):
                issues.append(LintIssue("warning", preset_str, f"{entry_path}: missing 'name'"))

            segments = entry.get("segments")
            if not isinstance(segments, list) or not segments:
                issues.append(
                    LintIssue("warning", preset_str, f"{entry_path}: 'segments' must be a non-empty list")
                )
                continue

            for j, segment in enumerate(segments):
                segment_path = f"{entry_path}.segments[{j}]"
                if not isinstance(segment, dict):
                    issues.append(LintIssue("warning", preset_str, f"{segment_path}: must be a mapping"))
                    continue
                if "chips" in segment:
                    issues.append(
                        LintIssue(
                            "warning",
                            preset_str,
                            f"{segment_path}: 'chips' is not allowed in a preset-declared segment "
                            f"(a preset cannot know a user's phrasebook)",
                        )
                    )
                segment_type = segment.get("type", "content")
                if segment_type not in ("content", "break"):
                    issues.append(
                        LintIssue(
                            "warning",
                            preset_str,
                            f"{segment_path}: type must be 'content' or 'break', got {segment_type!r}",
                        )
                    )

        return issues

    def _lint_tests_yml(self, preset_file: Path, manifest) -> List[LintIssue]:
        """Cross-checks for `tests.yml` (preset E2E test suite, see
        docs/presets.md "Testing presets") that the schema itself cannot
        express without either sibling-case context (duplicate names) or the
        preset's OTHER file (`modes:` from preset.yml, to check a case's
        `mode:` is real):

        - no tests.yml at all: informational warning only - most presets
          don't have one yet;
        - malformed tests.yml (fails to parse or fails schema validation):
          error, one LintIssue per formatted validation message;
        - duplicate case names within the file: error (case reports and the
          HTML gallery the runner produces are keyed by name);
        - sha256 not 64 hex digits (the documented all-zero placeholder,
          tests_schema.PLACEHOLDER_SHA256, matches this shape by
          construction so it never trips this check on its own): error;
        - `mode:` not among the preset's declared modes: error (the runner
          has nothing to submit against);
        - `form:` keys colliding with `models:` keys: error - models:
          injects the resolved local path into `form` under the same key,
          so a form: entry for that key would be silently clobbered (or
          would silently win, depending on injection order) rather than
          erroring at runtime, which is worse than catching it here.

        Also warns (not errors, since it doesn't block the runner) when a
        case uses the placeholder sha256 without the 'needs-model' tag - the
        convention exists so a placeholder case is never mistaken for a
        real, passing regression test.
        """
        issues: List[LintIssue] = []
        preset_dir = preset_file.parent
        preset_str = str(preset_file)
        tests_file = preset_dir / "tests.yml"

        if not tests_file.exists():
            issues.append(
                LintIssue(
                    "warning",
                    preset_str,
                    "no tests.yml: this preset has no E2E test suite (informational only)",
                )
            )
            return issues

        try:
            with open(tests_file, 'r') as f:
                data = yaml.load(f, Loader=yaml.FullLoader) or {}
        except Exception as e:
            issues.append(LintIssue("error", preset_str, f"tests.yml: failed to parse: {e}"))
            return issues

        if not isinstance(data, dict):
            issues.append(
                LintIssue(
                    "error", preset_str,
                    f"tests.yml: top level must be a mapping, got {type(data).__name__}",
                )
            )
            return issues

        tests, errors = validate_tests_yml(data, "tests.yml")
        for err in errors:
            issues.append(LintIssue("error", preset_str, err))
        if tests is None:
            return issues

        declared_modes = set(manifest.modes)
        seen_names: Dict[str, int] = {}

        for idx, case in enumerate(tests.cases):
            if case.name in seen_names:
                issues.append(
                    LintIssue(
                        "error",
                        preset_str,
                        f"tests.yml: case '{case.name}' (index {idx}) duplicates the name of "
                        f"case index {seen_names[case.name]} - case names must be unique "
                        f"within a file",
                    )
                )
            else:
                seen_names[case.name] = idx

            if case.mode not in declared_modes:
                issues.append(
                    LintIssue(
                        "error",
                        preset_str,
                        f"tests.yml: case '{case.name}': mode '{case.mode}' is not declared "
                        f"in preset.yml modes: {sorted(declared_modes)}",
                    )
                )

            colliding = sorted(set(case.form.keys()) & set(case.models.keys()))
            if colliding:
                issues.append(
                    LintIssue(
                        "error",
                        preset_str,
                        f"tests.yml: case '{case.name}': form key(s) {colliding} also appear "
                        f"in models: - models: injects the resolved path under the same key, "
                        f"so this either clobbers or is clobbered depending on injection order",
                    )
                )

            for field_name, ref in case.models.items():
                if not _SHA256_RE.match(ref.sha256):
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"tests.yml: case '{case.name}': models.{field_name}.sha256 "
                            f"'{ref.sha256}' is not 64 hex digits",
                        )
                    )
                elif ref.sha256 == PLACEHOLDER_SHA256 and NEEDS_MODEL_TAG not in case.tags:
                    issues.append(
                        LintIssue(
                            "warning",
                            preset_str,
                            f"tests.yml: case '{case.name}': models.{field_name} uses the "
                            f"placeholder sha256 but is missing the '{NEEDS_MODEL_TAG}' tag "
                            f"(see tests_schema.py's module docstring for the convention)",
                        )
                    )

        return issues

    def _lint_engine_matches_pipes(self, preset_file: Path, manifest) -> List[LintIssue]:
        """
        A preset's pipes must speak the engine it declares.

        Only the `comfyui` engine is structurally detectable from the pipeline
        (it is the one that requires a `comfyui` pipe). Other engines cannot be
        told apart from the manifest alone, so this check is one-directional.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)

        has_comfyui_pipe = False
        for mode_name in manifest.modes:
            pipeline_file = preset_file.parent / "modes" / mode_name / "pipeline.yml"
            if not pipeline_file.exists():
                continue
            try:
                with open(pipeline_file, 'r') as f:
                    raw = f.read()
            except Exception:
                continue
            # pipeline.yml is a Jinja2 template, so it may not parse as YAML here.
            # A `name: comfyui` pipe declaration is detectable textually either way.
            if re.search(r"^\s*-?\s*name:\s*[\"']?comfyui[\"']?\s*$", raw, re.MULTILINE):
                has_comfyui_pipe = True
                break

        if manifest.engine == "comfyui" and not has_comfyui_pipe:
            issues.append(
                LintIssue(
                    "error",
                    preset_str,
                    "engine: comfyui but no pipe named 'comfyui' in any mode's pipeline.yml",
                )
            )
        elif manifest.engine != "comfyui" and has_comfyui_pipe:
            issues.append(
                LintIssue(
                    "error",
                    preset_str,
                    f"engine: {manifest.engine} but pipeline.yml declares a 'comfyui' pipe "
                    f"(should this preset declare engine: comfyui?)",
                )
            )

        return issues

    def _lint_field_defaults(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Schema-validate a mode's external tab/children fragments so a bad
        field `default:` surfaces as a lint error before it becomes a preset
        LOAD failure.

        `form.yml`'s own fields are already schema-validated by
        `_lint_form_variants` (via `validate_form_file`), which is where a
        quoted numeric/boolean default or Jinja-in-`default` on a directly
        declared field is caught. But most fields live in external
        `tabs/*.yml` fragments referenced by `children: "{{ paths.preset
        }}/.../tab.yml"`, and `validate_form_file` only sees the string path,
        not the file. This walks those fragments and runs them through the
        exact same `FieldSpec` validation the loader uses (`validate_field_list`),
        so the schema's typed-default message (rework §4: "default for a
        'checkbox' field must be a bool", "Jinja is not rendered in form
        definitions", ...) shows up in `preset_lint` output instead of only at
        load time.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)
        preset_root = preset_file.parent

        for variant_name, form_dir in discover_form_variants(mode_dir):
            loc = f"modes/{mode_name}" if form_dir == mode_dir else f"modes/{mode_name}/variants/{variant_name}"
            form_file = form_dir / "form.yml"
            try:
                with open(form_file, 'r') as f:
                    form_data = yaml.safe_load(f) or {}
            except Exception:
                continue

            for ref in self._iter_external_children_refs(form_data):
                frag_path = self._resolve_children_path(ref, preset_root)
                if not frag_path.exists():
                    continue  # missing fragment is a load error surfaced elsewhere
                try:
                    with open(frag_path, 'r') as f:
                        frag_data = yaml.safe_load(f) or {}
                except Exception as e:
                    issues.append(
                        LintIssue("error", preset_str, f"{loc}: children fragment {ref}: failed to parse: {e}")
                    )
                    continue
                _, errors = validate_field_list(
                    frag_data.get("fields", []) or [], prefix=f"{loc} -> {frag_path.name}"
                )
                for err in errors:
                    issues.append(LintIssue("error", preset_str, err))

        return issues

    def _lint_camera_shot_fields(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Flag `camera_shot` fields whose configuration names a shot/category key
        that isn't in the canonical taxonomy.

        The field itself ignores unknown `vocabulary`/`categories` keys at render
        (they simply don't appear), so a typo like `over_shoulder` vs the wrong
        spelling is silent otherwise. Catching it at lint time is the authoring
        safety the preset author needs. Walks the mode's form.yml and every
        external children fragment, since most fields live in fragments.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)
        preset_root = preset_file.parent
        known_shots = valid_shot_keys()
        known_categories = set(CATEGORY_KEYS)

        def check(node: Any, loc: str) -> None:
            for _path, sub in self._iter_nodes(node, ""):
                if not isinstance(sub, dict) or sub.get("type") != "camera_shot":
                    continue
                config = sub.get("configuration") or {}
                vocabulary = config.get("vocabulary")
                if isinstance(vocabulary, dict):
                    for key in vocabulary:
                        if key not in known_shots:
                            issues.append(LintIssue(
                                "warning", preset_str,
                                f"{loc}: camera_shot vocabulary has unknown shot key '{key}'",
                            ))
                categories = config.get("categories")
                if isinstance(categories, list):
                    for key in categories:
                        if key not in known_categories:
                            issues.append(LintIssue(
                                "warning", preset_str,
                                f"{loc}: camera_shot categories has unknown category '{key}'",
                            ))

        for variant_name, form_dir in discover_form_variants(mode_dir):
            loc = f"modes/{mode_name}" if form_dir == mode_dir else f"modes/{mode_name}/variants/{variant_name}"
            form_file = form_dir / "form.yml"
            try:
                with open(form_file, 'r') as f:
                    form_data = yaml.safe_load(f) or {}
            except Exception:
                continue

            check(form_data, f"{loc}/form.yml")

            for ref in self._iter_external_children_refs(form_data):
                frag_path = self._resolve_children_path(ref, preset_root)
                if not frag_path.exists():
                    continue
                try:
                    with open(frag_path, 'r') as f:
                        frag_data = yaml.safe_load(f) or {}
                except Exception:
                    continue
                check(frag_data, f"{loc} -> {frag_path.name}")

        return issues

    def _lint_pipeline_templates(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Structured checks over a mode's `pipeline.yml`, parsed as YAML.

        Every shipped pipeline.yml is plain YAML pre-render (the loader
        `yaml.safe_load`s it before iterating `pipeline:`); template expressions
        live inside scalar VALUES. Parsing here - instead of scanning raw text -
        means comments never trip a check, and the exact-expression contract can
        be applied structurally. If a pipeline somehow doesn't parse as YAML it
        wouldn't load at all; this method best-effort-skips it (the parse failure
        is a load error, not a template-lint concern).

        Rules (templating rework §1/§6):
        - (a) a pipe `enabled:` that is a STRING but not an exact `{{ expr }}`:
          error. It would string-render (never a bool), so `enabled is True`
          is never satisfied and the pipe silently never runs.
        - (c) a config-expansion `@loop` whose `items:` is not an exact
          `{{ expr }}`: error. `items` is evaluated natively to a list; a
          string template or bare literal raises at build time.
        - (d) any scalar referencing DELETED template context (`get_form(`,
          `value(`, `setting(`, `@object:`, `@dict:`, `input.*`, ...): error
          with a migration hint. Strict eval turns a leftover into a hard
          build failure.
        - (form refs) a `{{ form.<name> }}` reference naming a field missing
          from a form variant, and (guard/default mismatch) a
          `| default(<literal>)` disagreeing with the field's own `default:`,
          are handled separately by `_lint_variant_form_refs` /
          `_lint_guard_default_mismatch` - both need EACH variant's own field
          set individually (`bind_form` binds exactly one variant per
          request), not the union this method used to check against.
        - (nag) a `generator/*` pipe setting `configuration.nag_scale` with no
          `prompt_encoder` pipe in the same pipeline mirroring it: error (see
          `_NAG_MIRROR_KEY` module docstring).
        - (slg/riflex) a `generator/*` pipe outside the Wan family setting any
          SLG/RIFLEx config key: warning, accepted but inert (see
          `_SLG_RIFLEX_KEYS` module docstring).
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)
        pipeline_file = mode_dir / "pipeline.yml"
        if not pipeline_file.exists():
            return issues

        try:
            with open(pipeline_file, 'r') as f:
                data = yaml.safe_load(f)
        except Exception:
            return issues
        if not isinstance(data, dict):
            return issues

        loc = f"modes/{mode_name}/pipeline.yml"

        # (a) pipe-level enabled: must be a real bool or an exact expression.
        pipes = data.get("pipeline")
        if isinstance(pipes, list):
            for idx, pipe in enumerate(pipes):
                if not isinstance(pipe, dict):
                    continue
                enabled = pipe.get("enabled")
                if isinstance(enabled, str) and not _is_exact_expression(enabled):
                    pname = pipe.get("name") or pipe.get("id") or f"[{idx}]"
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{loc}: pipe '{pname}': enabled: {enabled!r} is a string but not an "
                            f"exact '{{{{ expression }}}}' - it string-renders instead of "
                            f"evaluating to a bool, so the pipe is never enabled. Use a YAML "
                            f"bool (true/false) or a single '{{{{ ... }}}}' expression.",
                        )
                    )

        # (nag) / (slg/riflex): reuse the already-parsed pipe list.
        if isinstance(pipes, list):
            prompt_encoder_mirrors_nag = False
            nag_generator_pipes: List[str] = []
            for pipe in pipes:
                if not isinstance(pipe, dict):
                    continue
                name = str(pipe.get("name") or "")
                config = pipe.get("configuration")
                if not isinstance(config, dict):
                    continue
                if name == "prompt_encoder" and _NAG_MIRROR_KEY in config:
                    prompt_encoder_mirrors_nag = True
                if name.startswith("generator/"):
                    if _NAG_MIRROR_KEY in config:
                        nag_generator_pipes.append(name)
                    if name not in _WAN_GUIDANCE_GENERATOR_PIPES:
                        inert_keys = [k for k in _SLG_RIFLEX_KEYS if k in config]
                        if inert_keys:
                            issues.append(
                                LintIssue(
                                    "warning",
                                    preset_str,
                                    f"{loc}: pipe '{name}': sets {', '.join(inert_keys)} but only the "
                                    f"Wan generator pipes ({', '.join(sorted(_WAN_GUIDANCE_GENERATOR_PIPES))}) "
                                    f"honor Skip-Layer Guidance / RIFLEx - accepted but silently inert here.",
                                )
                            )
            if nag_generator_pipes and not prompt_encoder_mirrors_nag:
                for name in nag_generator_pipes:
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{loc}: pipe '{name}': sets configuration.nag_scale but no 'prompt_encoder' "
                            f"pipe in this mode mirrors nag_scale - prompt_encoder._do_cfg() only encodes "
                            f"a negative pass when guidance_scale > 1.0 or nag_scale > 1.0, so without the "
                            f"mirror NAG silently does nothing (docs/techniques/nag.md).",
                        )
                    )

        # Walk every node once for (c) and (d).
        for path, node in self._iter_nodes(data, "pipeline.yml"):
            if isinstance(node, dict) and "@loop" in node:
                loop_cfg = node["@loop"]
                if isinstance(loop_cfg, dict) and "items" in loop_cfg:
                    items = loop_cfg["items"]
                    # A literal YAML list/dict is fine (_resolve_loop_items uses
                    # it as-is); only a STRING must be an exact expression -
                    # anything else (mixed text, multiple blocks) raises at
                    # build time under the native evaluator.
                    if isinstance(items, str) and not _is_exact_expression(items):
                        issues.append(
                            LintIssue(
                                "error",
                                preset_str,
                                f"{loc}: {path}.@loop.items: {items!r} must be an exact "
                                f"'{{{{ expression }}}}' yielding a list - `items` is evaluated "
                                f"natively, not string-rendered.",
                            )
                        )
            elif isinstance(node, str):
                for token in self._deleted_context_hits(node):
                    hint = _DELETED_TOKEN_MIGRATION_HINTS.get(token)
                    hint_suffix = f" {hint}." if hint else ""
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{loc}: {path}: uses deleted template context '{token}'. The pipeline "
                            f"context is now form.* / request.* / generation.* / preset.* / "
                            f"runtime.settings.* / paths.* (see docs/presets.md 'Migrating the old "
                            f"template syntax').{hint_suffix}",
                        )
                    )

        return issues

    def _lint_variant_form_refs(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """`{{ form.<name> }}` references in a mode's pipeline.yml, checked
        against EACH form variant's own field set individually - not their
        union (the check this replaces). `bind_form` (src/features/forms/
        binding.py) binds exactly one variant per request, so a reference
        that's only safe because a DIFFERENT variant happens to declare that
        field still fails strict evaluation for every submission that picked
        THIS one.

        A block carrying `| default(...)` is skipped (same as before) - the
        default makes a missing field safe under strict eval regardless of
        variant.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)
        preset_root = preset_file.parent
        pipeline_file = mode_dir / "pipeline.yml"
        if not pipeline_file.exists():
            return issues

        try:
            with open(pipeline_file, 'r') as f:
                data = yaml.safe_load(f)
        except Exception:
            return issues
        if not isinstance(data, dict):
            return issues

        loc = f"modes/{mode_name}/pipeline.yml"
        variants = list(discover_form_variants(mode_dir))

        for variant_name, form_dir in variants:
            vloc = f"modes/{mode_name}" if form_dir == mode_dir else f"modes/{mode_name}/variants/{variant_name}"
            known_fields = set(_INJECTED_FORM_KEYS)
            try:
                with open(form_dir / "form.yml", 'r') as f:
                    form_data = yaml.safe_load(f) or {}
            except Exception:
                continue
            self._collect_field_names(form_data.get("fields", []), preset_root, known_fields)

            for path, node in self._iter_nodes(data, "pipeline.yml"):
                if not isinstance(node, str):
                    continue
                for name in self._missing_form_refs(node, known_fields):
                    issues.append(
                        LintIssue(
                            "warning",
                            preset_str,
                            f"{loc}: {path}: references form.{name} but no field named '{name}' "
                            f"exists in form variant '{variant_name}' ({vloc}/form.yml) and the "
                            f"expression has no | default(...) - a submission using this variant "
                            f"would fail strict evaluation. Add the field to this variant or a "
                            f"| default(...) fallback.",
                        )
                    )

        return issues

    def _lint_guard_default_mismatch(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """`{{ form.<name> | default(<literal>) }}` guards whose literal
        disagrees with the field's own `default:` in a form variant.

        `bind_form` already fills in a missing key with the field's declared
        `default:` before the pipeline ever renders (src/features/forms/
        binding.py), so the pipeline's own `| default(...)` almost never
        actually fires - it's a belt-and-braces fallback for the rare
        caller that bypasses binding. When its literal disagrees with the
        field's real default, that fallback is either stale (the field's
        default changed and this wasn't updated) or was always wrong -
        either way, worth a warning.

        A guard that AGREES with the field default is deliberately not
        reported at all, even at `info`: prototyping this against the full
        preset tree turned up ~450 agreeing guards against ~30 real
        mismatches - agreement is the intended, healthy convention (a
        belt-and-braces literal mirroring the field default on purpose), so
        flagging every instance would bury the handful of actual bugs this
        check exists to find under 9x its own noise.

        Only LITERAL default() arguments are checked (numbers, quoted
        strings, true/false/null, `[]` - see `_parse_guard_literal`); an
        expression like `default(preset.vars.default_cfg)` is skipped, since
        comparing it would mean evaluating Jinja.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)
        preset_root = preset_file.parent
        pipeline_file = mode_dir / "pipeline.yml"
        if not pipeline_file.exists():
            return issues

        try:
            with open(pipeline_file, 'r') as f:
                data = yaml.safe_load(f)
        except Exception:
            return issues
        if not isinstance(data, dict):
            return issues

        loc = f"modes/{mode_name}/pipeline.yml"
        guards: set = set()
        for _path, node in self._iter_nodes(data, "pipeline.yml"):
            if not isinstance(node, str):
                continue
            for m in _GUARD_DEFAULT_RE.finditer(node):
                guards.add((m.group(1), m.group(2)))
        if not guards:
            return issues

        for variant_name, form_dir in discover_form_variants(mode_dir):
            vloc = f"modes/{mode_name}" if form_dir == mode_dir else f"modes/{mode_name}/variants/{variant_name}"
            try:
                with open(form_dir / "form.yml", 'r') as f:
                    form_data = yaml.safe_load(f) or {}
            except Exception:
                continue
            defaults: Dict[str, Any] = {}
            self._collect_field_defaults(form_data.get("fields", []), preset_root, defaults)

            for field_name, guard_text in sorted(guards):
                if field_name not in defaults:
                    continue
                is_literal, guard_value = _parse_guard_literal(guard_text)
                if not is_literal:
                    continue
                field_default = defaults[field_name]
                if _literal_eq(guard_value, field_default):
                    continue  # agreeing guard - the healthy, intended case (see docstring)
                issues.append(
                    LintIssue(
                        "warning",
                        preset_str,
                        f"{loc}: form.{field_name} | default({guard_text.strip()}) does not match "
                        f"the field's own default {field_default!r} in {vloc}/form.yml - bind_form "
                        f"already fills in the field default before the pipeline ever renders, so "
                        f"this guard literal is stale or wrong and will never actually be used",
                    )
                )

        return issues

    def _lint_pipe_names(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """A pipe's `name:` must resolve to something in the pipe catalog
        (core pipes, `pipes/custom`, or an enabled plugin) -
        `GenerationEngine.validate_pipeline` (src/features/generation/
        engine.py) raises `Pipe '<name>' not found in pipe registry` for a
        typo here, at generation-RUN time. Checked via the catalog's
        light-scan tier (`PipeCatalog.get_pipe_source`), which never imports
        a pipe module just to answer "does this name exist". A templated
        `name:` (contains `{{`) is skipped - its real value isn't known
        until render.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)
        pipeline_file = mode_dir / "pipeline.yml"
        if not pipeline_file.exists():
            return issues

        try:
            with open(pipeline_file, 'r') as f:
                data = yaml.safe_load(f)
        except Exception:
            return issues
        if not isinstance(data, dict):
            return issues
        pipes = data.get("pipeline")
        if not isinstance(pipes, list):
            return issues

        loc = f"modes/{mode_name}/pipeline.yml"
        catalog = self._pipe_catalog()

        for idx, pipe in enumerate(pipes):
            if not isinstance(pipe, dict):
                continue
            name = pipe.get("name")
            if not isinstance(name, str) or "{{" in name:
                continue
            if catalog.get_pipe_source(name) is None:
                issues.append(
                    LintIssue(
                        "error",
                        preset_str,
                        f"{loc}: pipeline[{idx}]: pipe name '{name}' not found in the pipe catalog "
                        f"(checked core pipes, pipes/custom, and enabled plugins) - check for a typo",
                    )
                )

        return issues

    def _lint_pipe_config_keys(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Cross-check each pipe's `configuration:` keys against the pipe
        class's declared `PipeConfigSpec`s - the pipeline-side counterpart of
        `_lint_field_config_keys`.

        Warning, not error: `validate_pipe_configuration`
        (src/features/generation/engine.py) silently PRESERVES an unknown key
        as an "injected parameter" (e.g. `backend_config`) rather than
        rejecting it, so an undeclared key is never a load-time failure -
        just possibly dead weight or an undocumented parameter a pipe reads
        straight off `self.config`.

        Needs the pipe CLASS (`configuration()` is a classmethod), so it
        goes through `_load_pipe_class` - a pipe whose module can't be
        imported here is skipped with one informational note (see that
        method), not a crash.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)
        pipeline_file = mode_dir / "pipeline.yml"
        if not pipeline_file.exists():
            return issues

        try:
            with open(pipeline_file, 'r') as f:
                data = yaml.safe_load(f)
        except Exception:
            return issues
        if not isinstance(data, dict):
            return issues
        pipes = data.get("pipeline")
        if not isinstance(pipes, list):
            return issues

        loc = f"modes/{mode_name}/pipeline.yml"

        for pipe in pipes:
            if not isinstance(pipe, dict):
                continue
            name = pipe.get("name")
            config = pipe.get("configuration")
            if not isinstance(name, str) or "{{" in name or not isinstance(config, dict):
                continue

            pipe_class = self._load_pipe_class(name)
            if pipe_class is None:
                continue
            try:
                specs = pipe_class.configuration() or []
            except Exception:
                continue

            allowed = {s.name for s in specs}
            unknown_keys = sorted(k for k in config.keys() if k not in allowed)
            if unknown_keys:
                identifier = pipe.get("id") or name
                issues.append(
                    LintIssue(
                        "warning",
                        preset_str,
                        f"{loc}: pipe '{identifier}' ({name}) configuration has key(s) not declared "
                        f"in its PipeConfigSpec: {unknown_keys} (declared: {sorted(allowed)})",
                    )
                )

        return issues

    def _lint_pipeline_wiring(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Statically mirrors `GenerationEngine.validate_pipeline`
        (src/features/generation/engine.py) over the DECLARED pipe list, so a
        fake provider, a fake output variable, or a missing required input
        surfaces at lint time instead of raising at generation-run time.

        Same identifier resolution as the engine: a pipe is addressed by its
        `id:` if set, else its `name:` (only when that name is unique in the
        pipeline - a repeated name can only be addressed by `id:`), and
        `available_outputs` accumulates in DECLARATION ORDER exactly like the
        engine's own loop, so a forward reference (a provider declared LATER
        in the list) is correctly flagged as unresolved here too.

        Only a wired `input:` entry that matches one of the CONSUMING pipe's
        own declared, required input specs (by `name`) is checked at all -
        exactly what the engine's own loop does (`for input_spec in
        required_inputs: ... for input_config in pipe_config['input']: if
        param_name == input_spec.name: ...`). A pipe with dynamic/free-form
        inputs (an empty `inputs()` declaration - e.g. `from_iotype`,
        `param_emitter`) accepts ANY wiring unchecked by design; validating
        every wired entry regardless of whether the consumer even declares
        that parameter would flag this legitimate pattern as broken.

        A provider/pipe `name:` containing `{{`/`{%` (an expression or a
        statement tag, e.g. a Jinja `{% if %}...{% endif %}` picking the
        provider conditionally) is skipped - not known until render. A pipe
        whose class can't be imported here is skipped for the checks that
        need its inputs()/outputs() (see `_load_pipe_class` - one
        informational note per pipe name, not a crash); pure existence
        checks (does this identifier appear in the pipeline at all) don't
        need an import and still run. Disabled pipes (`enabled: false`
        LITERAL) are excluded, exactly like the engine's `[p for p in pipes
        if p['enabled']]` filter - anything else (missing, `true`, or a
        template string) is conservatively treated as enabled, since its
        real value isn't known until render.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)
        pipeline_file = mode_dir / "pipeline.yml"
        if not pipeline_file.exists():
            return issues

        try:
            with open(pipeline_file, 'r') as f:
                data = yaml.safe_load(f)
        except Exception:
            return issues
        if not isinstance(data, dict):
            return issues
        pipes = data.get("pipeline")
        if not isinstance(pipes, list):
            return issues

        loc = f"modes/{mode_name}/pipeline.yml"
        enabled_pipes = [p for p in pipes if isinstance(p, dict) and p.get("enabled") is not False]

        name_counts: Dict[str, int] = {}
        for p in enabled_pipes:
            name = p.get("name")
            if isinstance(name, str):
                name_counts[name] = name_counts.get(name, 0) + 1

        identifier_map: Dict[str, str] = {}
        for p in enabled_pipes:
            name = p.get("name")
            pid = p.get("id")
            identifier = pid if pid else name
            if isinstance(name, str) and name_counts.get(name) == 1:
                identifier_map[name] = identifier
            if pid and pid != name:
                identifier_map[pid] = identifier

        available_outputs: Dict[str, Optional[set]] = {}

        for idx, pipe in enumerate(enabled_pipes):
            name = pipe.get("name")
            identifier = pipe.get("id") or name or f"[{idx}]"

            pipe_class = None
            if isinstance(name, str) and "{{" not in name:
                pipe_class = self._load_pipe_class(name)

            required_inputs = []
            if pipe_class is not None:
                try:
                    required_inputs = [
                        s for s in (pipe_class.inputs() or [])
                        if s.required and s.io_type.name != "SERVICE"
                    ]
                except Exception:
                    required_inputs = []

            parsed_inputs: List[Tuple[Any, Any, Any]] = []  # (param_name, provider, output_var)
            for input_entry in pipe.get("input") or []:
                if isinstance(input_entry, dict):
                    parsed_inputs.append((
                        input_entry.get("name"), input_entry.get("provider"), input_entry.get("output_var"),
                    ))
                elif isinstance(input_entry, list) and len(input_entry) >= 2:
                    parsed_inputs.append((
                        input_entry[0], input_entry[1], input_entry[2] if len(input_entry) > 2 else None,
                    ))

            for input_spec in required_inputs:
                matched = next((e for e in parsed_inputs if e[0] == input_spec.name), None)
                if matched is None:
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{loc}: pipe '{identifier}' requires input '{input_spec.name}' but it is "
                            f"not wired in this pipe's `input:` list",
                        )
                    )
                    continue

                _param_name, provider, output_var = matched
                if not isinstance(provider, str) or "{{" in provider or "{%" in provider:
                    continue  # templated provider - not resolvable statically

                actual_provider = identifier_map.get(provider, provider)
                if actual_provider not in available_outputs:
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{loc}: pipe '{identifier}' input '{input_spec.name}' references non-existent "
                            f"provider pipe '{provider}'",
                        )
                    )
                    continue

                provider_outputs = available_outputs[actual_provider]
                if provider_outputs is None:
                    continue  # provider's class couldn't be imported - can't check its outputs
                if (
                    isinstance(output_var, str)
                    and "{{" not in output_var
                    and "{%" not in output_var
                    and output_var not in provider_outputs
                ):
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{loc}: pipe '{identifier}' input '{input_spec.name}' references non-existent "
                            f"output '{output_var}' from pipe '{provider}'",
                        )
                    )

            outputs_for_this = None
            if pipe_class is not None:
                try:
                    outputs_for_this = {s.name for s in (pipe_class.outputs() or [])}
                except Exception:
                    outputs_for_this = None
            available_outputs[identifier] = outputs_for_this

        return issues

    def _lint_config_exact_expression(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Extends the exact-expression contract (`_lint_pipeline_templates`'s
        (a)/(c) rules) to every `configuration:` key whose pipe declares it as
        `bool`/`list` in its `PipeConfigSpec` - a string value there that
        isn't a single `{{ expression }}` block string-renders instead of
        evaluating natively, exactly the same hazard as `enabled:`/`@loop
        items:`. Needs the pipe class (same lazy, memoized lookup as
        `_lint_pipe_config_keys` - one informational note per unimportable
        pipe, not a crash).
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)
        pipeline_file = mode_dir / "pipeline.yml"
        if not pipeline_file.exists():
            return issues

        try:
            with open(pipeline_file, 'r') as f:
                data = yaml.safe_load(f)
        except Exception:
            return issues
        if not isinstance(data, dict):
            return issues
        pipes = data.get("pipeline")
        if not isinstance(pipes, list):
            return issues

        loc = f"modes/{mode_name}/pipeline.yml"

        for pipe in pipes:
            if not isinstance(pipe, dict):
                continue
            name = pipe.get("name")
            config = pipe.get("configuration")
            if not isinstance(name, str) or "{{" in name or not isinstance(config, dict):
                continue

            pipe_class = self._load_pipe_class(name)
            if pipe_class is None:
                continue
            try:
                specs = pipe_class.configuration() or []
            except Exception:
                continue

            typed_keys = {s.name: s.param_type for s in specs if s.param_type in (bool, list)}
            identifier = pipe.get("id") or name

            for key, value in config.items():
                param_type = typed_keys.get(key)
                if param_type is None or not isinstance(value, str):
                    continue
                if not _is_exact_expression(value):
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{loc}: pipe '{identifier}': configuration.{key}: {value!r} is a string "
                            f"but not an exact '{{{{ expression }}}}' - it renders to a string, not a "
                            f"{param_type.__name__}, so this value is never correct at runtime. Use a "
                            f"YAML literal or a single '{{{{ ... }}}}' expression.",
                        )
                    )

        return issues

    def _collect_field_names(self, node, preset_root: Path, acc: set) -> None:
        """Recursively add every declared field `name:` under ``node`` to
        ``acc``, following external `children:` fragments and expanding a
        form `@loop` (type: "@loop", configuration.count + configuration.template)
        into its per-iteration field names."""
        if isinstance(node, list):
            for item in node:
                self._collect_field_names(item, preset_root, acc)
            return
        if not isinstance(node, dict):
            return

        if node.get("type") == "@loop":
            cfg = node.get("configuration") or {}
            template = cfg.get("template")
            count = cfg.get("count")
            if template is not None and isinstance(count, int) and not isinstance(count, bool):
                for i in range(1, count + 1):
                    self._collect_field_names(self._expand_loop_indices(template, i), preset_root, acc)
            return

        name = node.get("name")
        if isinstance(name, str) and "{{" not in name:
            acc.add(name)

        children = node.get("children")
        if isinstance(children, str):
            frag_path = self._resolve_children_path(children, preset_root)
            try:
                with open(frag_path, 'r') as f:
                    frag_data = yaml.safe_load(f) or {}
            except Exception:
                return
            self._collect_field_names(frag_data.get("fields", []), preset_root, acc)
        elif isinstance(children, list):
            self._collect_field_names(children, preset_root, acc)

    def _collect_field_defaults(self, node, preset_root: Path, acc: Dict[str, Any]) -> None:
        """Same recursion as `_collect_field_names`, but records each named
        field's own `default:` value instead of just its name - a field with
        no `default:` key is simply absent from ``acc``, not `None`. Used by
        `_lint_guard_default_mismatch`."""
        if isinstance(node, list):
            for item in node:
                self._collect_field_defaults(item, preset_root, acc)
            return
        if not isinstance(node, dict):
            return

        if node.get("type") == "@loop":
            cfg = node.get("configuration") or {}
            template = cfg.get("template")
            count = cfg.get("count")
            if template is not None and isinstance(count, int) and not isinstance(count, bool):
                for i in range(1, count + 1):
                    self._collect_field_defaults(self._expand_loop_indices(template, i), preset_root, acc)
            return

        name = node.get("name")
        if isinstance(name, str) and "{{" not in name and "default" in node:
            acc[name] = node["default"]

        children = node.get("children")
        if isinstance(children, str):
            frag_path = self._resolve_children_path(children, preset_root)
            try:
                with open(frag_path, 'r') as f:
                    frag_data = yaml.safe_load(f) or {}
            except Exception:
                return
            self._collect_field_defaults(frag_data.get("fields", []), preset_root, acc)
        elif isinstance(children, list):
            self._collect_field_defaults(children, preset_root, acc)

    @staticmethod
    def _expand_loop_indices(template, index: int):
        """Deep-copy ``template`` substituting `{{ loop.index }}` -> index and
        `{{ loop.index0 }}` -> index-1 in every string, so a templated field
        name like `controlnet_{{ loop.index }}_model` becomes a concrete name."""
        def walk(n):
            if isinstance(n, str):
                n = _LOOP_INDEX_RE.sub(str(index), n)
                n = _LOOP_INDEX0_RE.sub(str(index - 1), n)
                return n
            if isinstance(n, list):
                return [walk(x) for x in n]
            if isinstance(n, dict):
                return {k: walk(v) for k, v in n.items()}
            return n
        return walk(template)

    @staticmethod
    def _resolve_children_path(children_ref: str, preset_root: Path) -> Path:
        """Resolve an external `children:` path string ("{{ paths.preset
        }}/...") to a filesystem path (the one Jinja variable knowable at load
        time, per loader.py).

        The substitution already yields the complete path (`paths.preset` is
        the whole prefix), so - like the loader - the result is used as-is; a
        rare ref without the variable is left relative to the cwd rather than
        doubled onto ``preset_root``.
        """
        return Path(_CHILDREN_PATH_VAR_RE.sub(str(preset_root), children_ref))

    def _iter_external_children_refs(self, node) -> List[str]:
        """Collect every `children:` value that is an external-file path string."""
        refs: List[str] = []
        if isinstance(node, dict):
            children = node.get("children")
            if isinstance(children, str):
                refs.append(children)
            for value in node.values():
                refs.extend(self._iter_external_children_refs(value))
        elif isinstance(node, list):
            for item in node:
                refs.extend(self._iter_external_children_refs(item))
        return refs

    @staticmethod
    def _iter_nodes(node, path: str):
        """Yield ``(path, node)`` for ``node`` and every nested dict/list/scalar,
        building a dotted/indexed ``path`` for diagnostics."""
        yield path, node
        if isinstance(node, dict):
            for k, v in node.items():
                child = f"{path}.{k}" if path else str(k)
                yield from PresetLinter._iter_nodes(v, child)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from PresetLinter._iter_nodes(v, f"{path}[{i}]")

    def _deleted_context_hits(self, scalar: str) -> List[str]:
        """Return the deleted-context tokens present in ``scalar``.

        `@object:`/`@dict:` were bare string directives (often the whole
        value), so they're matched anywhere. The deleted call globals/filters,
        the `input.*` context, and the deleted dotted context paths
        (`preset.speed_profiles`/`preset.configuration`/`generation.seed`/
        `generation.quantity`) are only meaningful inside a `{{ }}`/`{% %}`
        region - matching there (never in surrounding literal text) keeps a
        data value like `file_path: "input.png"` from being mistaken for a
        legacy context reference.
        """
        hits: List[str] = []
        if "@object:" in scalar:
            hits.append("@object:")
        if "@dict:" in scalar:
            hits.append("@dict:")

        regions = " ".join(m.group(0) for m in _TEMPLATE_REGION_RE.finditer(scalar))
        if regions:
            for m in _DELETED_CALL_RE.finditer(regions):
                token = f"{m.group(1)}("
                if token not in hits:
                    hits.append(token)
            if _DELETED_INPUT_RE.search(regions) and "input." not in hits:
                hits.append("input.")
            for m in _DELETED_DOTTED_RE.finditer(regions):
                token = m.group(1)
                if token not in hits:
                    hits.append(token)
        return hits

    @staticmethod
    def _missing_form_refs(scalar: str, known_fields: set) -> List[str]:
        """Names referenced as `{{ form.<name> }}` in ``scalar`` that are not a
        known field AND whose enclosing `{{ }}` block has no `| default(...)`
        fallback. A block that carries a `default(` is skipped entirely - the
        default makes a missing field safe under strict eval."""
        missing: List[str] = []
        for m in _TEMPLATE_REGION_RE.finditer(scalar):
            region = m.group(0)
            if "default(" in region:
                continue
            for name in _FORM_REF_RE.findall(region):
                if name not in known_fields and name not in missing:
                    missing.append(name)
        return missing

    def _lint_form_variants(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Cross-checks for form "variant" metadata (`label`/`description`/
        `examples`/`default`/`order` on a mode's `form.yml` /
        `variants/<name>/form.yml`, see docs/presets.md "Variants") that the
        schema itself cannot express:

        - schema-level errors on the form file (e.g. an `examples` entry not
          shaped like `public/...`) are surfaced here too, since the general
          per-preset lint pass otherwise never schema-validates form.yml
          (only preset.yml's manifest);
        - `examples` entries that are shaped correctly but don't exist on
          disk: error (mirrors `_lint_media_refs`'s existence check);
        - more than one form in a mode marked `default: true`: error - a
          mode has exactly one default variant, whether from an explicit
          flag or (with none set) the first form after sorting.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)

        default_forms: List[str] = []

        for variant_name, form_dir in discover_form_variants(mode_dir):
            loc = f"modes/{mode_name}" if form_dir == mode_dir else f"modes/{mode_name}/variants/{variant_name}"
            form_file = form_dir / "form.yml"
            try:
                with open(form_file, 'r') as f:
                    form_data = yaml.load(f, Loader=yaml.FullLoader) or {}
            except Exception:
                continue

            form, errors = validate_form_file(
                form_data, prefix=f"{loc}/form.yml"
            )
            for err in errors:
                issues.append(LintIssue("error", preset_str, err))
            if form is None:
                continue

            if form.default:
                default_forms.append(variant_name)

            for example in form.examples:
                candidate = preset_file.parent / example
                if not candidate.exists():
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{loc}: examples entry not "
                            f"found: {example}",
                        )
                    )

        if len(default_forms) > 1:
            issues.append(
                LintIssue(
                    "error",
                    preset_str,
                    f"modes/{mode_name}: multiple forms marked default: true "
                    f"({sorted(default_forms)}) - only one form variant per mode may be "
                    f"the default",
                )
            )

        return issues

    def _lint_configuration_refs(
        self, preset_file: Path, mode_dir: Path, mode_name: str, manifest
    ) -> List[LintIssue]:
        """Cross-check `"@config:<key>"` indirection (e.g. a `model` field's
        `filter_tags: "@config:checkpoint_tags"`) against preset.yml's declared
        `configuration:` block.

        Unknown configuration *types* can't reach here at all - `ConfigurationEntry`
        (schema.py) rejects them at manifest validation, before this method's
        `manifest` argument would even exist (see `_lint_preset`'s early return when
        `manifest is None`). This only checks the other direction: a field
        referencing a key `configuration:` never declared.
        """
        issues: List[LintIssue] = []
        if not (mode_dir / "forms").exists() and not (mode_dir / "form.yml").exists():
            return issues

        declared_keys = set((getattr(manifest, "configuration", None) or {}).keys())

        # Scan every YAML that belongs to a form variant, not just form.yml -
        # `@config:` refs usually live in external tab files (tabs/*.yml).
        for form_file in self._iter_form_yaml_files(mode_dir):
            try:
                with open(form_file, 'r') as f:
                    form_data = yaml.load(f, Loader=yaml.FullLoader) or {}
            except Exception:
                continue

            rel = form_file.relative_to(mode_dir)
            for key in self._extract_config_refs(form_data):
                if key not in declared_keys:
                    issues.append(
                        LintIssue(
                            "error",
                            str(preset_file),
                            f"modes/{mode_name}/{rel}: references "
                            f"'@config:{key}' but preset.yml declares no configuration "
                            f"entry '{key}'",
                        )
                    )

        return issues

    def _lint_field_config_keys(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Cross-check each field's `configuration:` keys against the field type's
        declared `FieldConfigSpec`s (`BaseField.configuration()`, dispatched via
        `FieldTypeRegistry` - see `src/platform/plugins/field_types.py`).

        Warning, not error: several core field types intentionally pass their
        whole `configuration:` dict through to the frontend schema unfiltered
        (Slider, Container) rather than building a whitelisted dict, so a key
        can be genuinely consumed by a Svelte component with no backend-declared
        spec at all (confirmed for real preset content: `tab`'s `icon_display`,
        `image`'s `allow_inpaint`, `lora_picker`'s `filter_tags` - now added to
        their specs precisely so they stop tripping this warning). A live audit
        of the full preset tree also turned up plenty of keys that are neither
        consumed by the backend NOR read by the matching frontend component
        (e.g. `description:` authored inside `configuration:` instead of at the
        field's top level, `model`'s legacy `display_civitai_image` predating
        the provider-generalization rename to `display_provider_image`) - those
        are real authoring bugs, but flagging them as hard errors would fail the
        build on a large pre-existing backlog this check was never scoped to
        fix in one pass. Warning keeps the signal visible without doing that.

        A field type with no backend schema class - `schema_cls is None`
        (plain string/textbox/number/integer, which fall through to
        `DefaultField` and never echo `configuration:` to the frontend at all)
        or any type unknown to this registry (every plugin-contributed field
        type, when linting runs outside a full app boot - see
        `_field_type_registry`) - is skipped entirely: there is no declared
        contract to check it against.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)
        registry = self._field_type_registry()

        for form_file in self._iter_form_yaml_files(mode_dir):
            try:
                with open(form_file, 'r') as f:
                    form_data = yaml.load(f, Loader=yaml.FullLoader) or {}
            except Exception:
                continue

            rel = form_file.relative_to(mode_dir)
            for path, node in self._iter_nodes(form_data, str(rel)):
                if not isinstance(node, dict):
                    continue
                field_type = node.get("type")
                config = node.get("configuration")
                if not isinstance(field_type, str) or not isinstance(config, dict):
                    continue

                allowed = self._field_config_spec_names(registry, field_type)
                if allowed is None:
                    continue

                unknown_keys = sorted(k for k in config.keys() if k not in allowed)
                if unknown_keys:
                    issues.append(
                        LintIssue(
                            "warning",
                            preset_str,
                            f"modes/{mode_name}/{path}: type '{field_type}' configuration "
                            f"has key(s) not declared in its FieldConfigSpec: {unknown_keys} "
                            f"(declared: {sorted(allowed)})",
                        )
                    )

        return issues

    def _lint_sampling_fields(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Cross-check `sampler`/`schedule` field configuration against the
        sampler/schedule registries (`src/platform/runtime/native/sampling/registry.py`):
        `configuration.family` must be a known family, every
        `configuration.include`/`exclude` key must be a registered key
        (error/warning respectively - include picks the field's whole option
        set, exclude only trims it), and the field's top-level `default` must
        resolve to one of the options `configuration` would actually produce
        (or be empty when `configuration.allow_empty` is set).

        Only runs when the relevant registry is non-empty: a bare `PresetLinter`
        constructed before core registration ran (this module imported
        standalone, outside `scripts/preset_lint.py`/the live app, both of
        which register core samplers/schedules before linting) has nothing to
        check field configuration against, and flagging every family/key as
        unknown against an empty registry would be a false positive, not a
        real finding.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)

        for form_file in self._iter_form_yaml_files(mode_dir):
            try:
                with open(form_file, 'r') as f:
                    form_data = yaml.load(f, Loader=yaml.FullLoader) or {}
            except Exception:
                continue

            rel = form_file.relative_to(mode_dir)
            for path, node in self._iter_nodes(form_data, str(rel)):
                if not isinstance(node, dict):
                    continue
                field_type = node.get("type")
                if field_type not in ("sampler", "schedule"):
                    continue

                registry = sampler_registry if field_type == "sampler" else schedule_registry
                if not registry.definitions():
                    continue

                config = node.get("configuration")
                if not isinstance(config, dict):
                    config = {}

                location = f"modes/{mode_name}/{path}"
                field_name = node.get("name", "<unnamed>")

                known_families = _KNOWN_SAMPLING_FAMILIES | {
                    f for d in registry.definitions() for f in d.families if f != ANY_FAMILY
                }

                family = config.get("family") or None
                family_valid = True
                if family and family not in known_families:
                    family_valid = False
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{location}: {field_type} field '{field_name}' configuration.family "
                            f"'{family}' is not a known sampling family (known: {sorted(known_families)})",
                        )
                    )

                include = config.get("include") or []
                for key in include:
                    if not registry.has(key):
                        issues.append(
                            LintIssue(
                                "error",
                                preset_str,
                                f"{location}: {field_type} field '{field_name}' configuration.include "
                                f"names unknown {field_type} '{key}'",
                            )
                        )

                exclude = config.get("exclude") or []
                for key in exclude:
                    if not registry.has(key):
                        issues.append(
                            LintIssue(
                                "warning",
                                preset_str,
                                f"{location}: {field_type} field '{field_name}' configuration.exclude "
                                f"names unknown {field_type} '{key}'",
                            )
                        )

                default = node.get("default")
                if default is None:
                    continue
                if default == "" and config.get("allow_empty"):
                    continue
                if not family_valid:
                    # Family error already reported - checking default against
                    # a registry narrowed by a bogus family would just add
                    # noise on top of it.
                    continue

                options = registry.select(family=family, include=include or None, exclude=exclude or None)
                option_keys = {d.key for d in options}
                if default not in option_keys:
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{location}: {field_type} field '{field_name}' default '{default}' "
                            f"is not one of the resolved options ({sorted(option_keys)})",
                        )
                    )

        return issues

    def _lint_alert_field_config(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Flag `alert` fields authored with the pre-fix, silently-dropped shape.

        `src/features/fields/alert.py` reads `configuration.variant` (falls
        back to `'default'`) and `configuration.content` (falls back to the
        field's own `description`), and never reads `configuration.type` or
        `configuration.message` at all. Presets originally authored `type:`
        (colliding in name with the field's own outer `type: "alert"`
        discriminator) and `message:` for the body - both silently dropped,
        rendering every alert as an untitled gray box. `_lint_field_config_keys`
        already catches these as unknown keys, but only at warning severity
        (it carries a large pre-existing backlog across every field type). This
        check is scoped to `alert` alone, where the tree is verified clean, so
        it can be an error: a regression here is a hard authoring bug, not
        backlog noise.
        """
        issues: List[LintIssue] = []
        preset_str = str(preset_file)

        for form_file in self._iter_form_yaml_files(mode_dir):
            try:
                with open(form_file, 'r') as f:
                    form_data = yaml.load(f, Loader=yaml.FullLoader) or {}
            except Exception:
                continue

            rel = form_file.relative_to(mode_dir)
            for path, node in self._iter_nodes(form_data, str(rel)):
                if not isinstance(node, dict) or node.get("type") != "alert":
                    continue
                config = node.get("configuration")
                if not isinstance(config, dict):
                    config = {}

                field_name = node.get("name", "<unnamed>")
                location = f"modes/{mode_name}/{path}"

                misauthored = sorted(k for k in ("type", "message") if k in config)
                if misauthored:
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{location}: alert field '{field_name}' configuration has "
                            f"{misauthored} - alert.py never reads these (variant/content "
                            f"are the real keys), so this content is silently dropped",
                        )
                    )

                if "variant" not in config and "content" not in config:
                    issues.append(
                        LintIssue(
                            "error",
                            preset_str,
                            f"{location}: alert field '{field_name}' configuration has "
                            f"neither 'variant' nor 'content' - it will render with the "
                            f"default gray variant and only the field's top-level "
                            f"description (if any) as its body",
                        )
                    )

        return issues

    @staticmethod
    def _field_type_registry():
        """The `FieldTypeRegistry` to check field configurations against.

        Reuses the shared `field_type_registry` singleton (populated with
        plugin-contributed types too, when this runs inside a live app via
        `GET /api/developer/presets/lint`) and lazily seeds it with the core
        types if empty - the same guard `FieldFactory.__init__` uses, so a bare
        `scripts/preset_lint.py` run (no app boot, no plugins loaded) still
        checks core field types correctly.
        """
        from src.platform.plugins.field_types import field_type_registry
        if not field_type_registry.all():
            from src.features.fields.builtin import register_builtin_fields
            register_builtin_fields(field_type_registry)
        return field_type_registry

    def _requirement_checker_registry(self):
        """The `RequirementCheckerRegistry` to validate `requirements:`
        entries against. If `__init__` was given one explicitly, that is
        returned as-is (see its docstring - this is how an in-app caller
        wires in the live process-wide registry). Otherwise built once per
        linter run and memoized on `self`, never the process-wide
        `requirement_checker_registry` singleton, so one lint pass can't leak
        checkers into another or into whatever else shares that singleton.

        Unlike `_field_type_registry` (an unknown field type is skipped, not
        an error - see `_lint_field_config_keys`), an unregistered
        `requirements:` type IS a hard lint error (`_lint_requirements`), so
        core-only seeding isn't enough: `scripts/preset_lint.py` never boots
        the app or enables a plugin, so a preset declaring e.g. `comfyui_node`
        would fail lint even though the ComfyUI plugin's manifest declares
        that checker. Seeded from `self.plugin_manifests` instead (already
        discovered by the caller - see `__init__`'s docstring and
        `_lint_preset_mode_contributions`, which needs the same list for the
        same reason): every manifest's `requirement_checkers:` entries are
        registered by type name, core first so a plugin colliding with a core
        type is silently shadowed here exactly as `PluginRegistry.
        _register_plugin_requirement_checkers` would refuse it at real
        enable time (that collision is reported there, not by this linter).

        A plugin's checker backend class can fail to import here even when it
        would load fine in a real app (the standalone script has no plugin
        dependencies installed, no container, no DB) - that failure is
        accepted by *type name* with a WARNING rather than left unregistered,
        so a preset that correctly uses the type isn't hard-failed by an
        environment gap: the in-app lint (which really loads the plugin) still
        validates the entry's arguments for real.
        """
        if self._req_checker_registry is not None:
            return self._req_checker_registry

        from src.features.presets.requirements.builtin import register_builtin_requirement_checkers
        from src.platform.plugins.loader import PluginLoader
        from src.platform.plugins.requirement_checkers import (
            DuplicateRequirementCheckerError,
            RequirementCheckerRegistration,
            RequirementCheckerRegistry,
        )

        registry = RequirementCheckerRegistry()
        register_builtin_requirement_checkers(registry)

        loader = PluginLoader()
        for manifest in self.plugin_manifests:
            for entry in getattr(manifest, "requirement_checkers", None) or []:
                type_name = entry.get("type")
                backend_ref = entry.get("backend")
                if not type_name or not backend_ref or registry.get(type_name) is not None:
                    continue

                location = f"plugin '{manifest.id}' requirement_checkers -> '{type_name}'"
                checker_cls = loader.load_class(manifest, backend_ref)
                checker = None
                if checker_cls is None:
                    self._req_checker_warnings.append(LintIssue(
                        "warning", location,
                        f"could not import checker backend '{backend_ref}' for standalone lint - "
                        f"argument validation for this type is skipped here (the in-app lint, "
                        f"which loads the plugin for real, still validates it)",
                    ))
                else:
                    try:
                        checker = checker_cls()
                    except Exception as e:
                        self._req_checker_warnings.append(LintIssue(
                            "warning", location,
                            f"could not instantiate checker backend '{backend_ref}' for standalone "
                            f"lint ({e}) - argument validation for this type is skipped here",
                        ))

                try:
                    registry.register(RequirementCheckerRegistration(
                        type_name=type_name,
                        checker=checker if checker is not None else _UncheckedPluginRequirementChecker(type_name),
                        source=manifest.id,
                    ))
                except DuplicateRequirementCheckerError:
                    continue

        self._req_checker_registry = registry
        return registry

    def _lint_requirements(self, preset_file: Path, manifest) -> List[LintIssue]:
        """Validate `requirements:` entries (see docs/presets.md
        "Requirements"): an unregistered `type:` is an error, and an entry
        whose own arguments fail its checker's `schema` is an error citing
        the pydantic message."""
        issues: List[LintIssue] = []
        requirements = getattr(manifest, "requirements", None)
        if not requirements:
            return issues

        preset_str = str(preset_file)
        registry = self._requirement_checker_registry()

        for index, entry in enumerate(requirements):
            type_name = entry.type
            registration = registry.get(type_name)
            if registration is None:
                issues.append(
                    LintIssue(
                        "error",
                        preset_str,
                        f"requirements[{index}]: unknown type '{type_name}' - no checker "
                        f"is registered for it (core types: binary, python_package, model, "
                        f"vram_min_gb, platform)",
                    )
                )
                continue

            entry_dict = entry.model_dump(exclude_none=True)
            try:
                registration.checker.schema.model_validate(entry_dict)
            except ValidationError as exc:
                issues.extend(
                    LintIssue("error", preset_str, f"requirements[{index}] ({type_name}): {msg}")
                    for msg in _format_errors("requirements", exc)
                )

        return issues

    def _field_config_spec_names(self, registry, type_name: str) -> Optional[set]:
        """Declared `FieldConfigSpec` names for `type_name`, or `None` if this
        field type has no backend schema class to check against (see
        `_lint_field_config_keys`). Memoized on `self` for the life of one
        linter run."""
        if type_name not in self._config_spec_cache:
            definition = registry.get(type_name)
            if definition.schema_cls is None:
                self._config_spec_cache[type_name] = None
            else:
                try:
                    specs = definition.schema_cls.configuration()
                except Exception:
                    specs = []
                self._config_spec_cache[type_name] = {s.name for s in specs}
        return self._config_spec_cache[type_name]

    @staticmethod
    def _iter_form_yaml_files(mode_dir: Path):
        """Yield every YAML file owned by one of a mode's form variants.

        Under the flattened layout the default variant's form files (form.yml
        + tabs/ fragments) sit directly in the mode dir alongside pipeline.yml
        and the mode-level files/ option dir; additional variants live under
        variants/<name>/. This yields the form-owned files only, skipping
        pipeline.yml and the mode-level files/ dir, so the config-ref scan is
        scoped to form content exclusively.
        """
        for variant_name, form_dir in discover_form_variants(mode_dir):
            if form_dir == mode_dir:
                for yml in sorted(mode_dir.rglob("*.yml")):
                    rel = yml.relative_to(mode_dir)
                    if rel.parts[0] in ("variants", "files"):
                        continue
                    if rel.name == "pipeline.yml":
                        continue
                    yield yml
            else:
                yield from sorted(form_dir.rglob("*.yml"))

    def _extract_config_refs(self, node) -> List[str]:
        """Walk a parsed YAML structure collecting `"<key>"` from any
        `"@config:<key>"` string value."""
        refs: List[str] = []
        if isinstance(node, str):
            if node.startswith("@config:"):
                refs.append(node[len("@config:"):])
        elif isinstance(node, dict):
            for value in node.values():
                refs.extend(self._extract_config_refs(value))
        elif isinstance(node, list):
            for item in node:
                refs.extend(self._extract_config_refs(item))
        return refs

    def _lint_option_file_refs(self, preset_file: Path, mode_dir: Path, mode_name: str) -> List[LintIssue]:
        """Check that `files/form/*.yml`-style option file references used in field
        configuration actually exist on disk (only literal, non-templated paths
        can be checked - Jinja-templated paths are skipped)."""
        issues: List[LintIssue] = []

        for variant_name, form_dir in discover_form_variants(mode_dir):
            loc = f"modes/{mode_name}" if form_dir == mode_dir else f"modes/{mode_name}/variants/{variant_name}"
            form_file = form_dir / "form.yml"
            try:
                with open(form_file, 'r') as f:
                    form_data = yaml.load(f, Loader=yaml.FullLoader) or {}
            except Exception:
                continue

            for path_str in self._extract_literal_file_refs(form_data):
                candidate = Path(path_str)
                if not candidate.is_absolute():
                    candidate = preset_file.parent / path_str
                if not candidate.exists():
                    issues.append(
                        LintIssue(
                            "warning",
                            str(preset_file),
                            f"{loc}: "
                            f"referenced option file not found: {path_str}",
                        )
                    )

        return issues

    def _lint_media_refs(self, preset_file: Path, manifest) -> List[LintIssue]:
        """Cross-check `media:` against the files on disk.

        The schema already validated each src's shape (relative, under public/,
        allowed extension). What it cannot see is whether the file exists, how big
        it is, or whether a gallery entry names a mode this preset declares -
        deliberately, since a schema failure would make the preset unloadable.
        """
        issues: List[LintIssue] = []
        media = getattr(manifest, "media", None)
        if media is None:
            return issues

        preset_str = str(preset_file)
        preset_dir = preset_file.parent

        refs: List[tuple] = []
        if media.cover:
            refs.append(("media.cover", media.cover, None))
        for idx, item in enumerate(media.gallery):
            refs.append((f"media.gallery[{idx}]", item.src, item.mode))

        declared_modes = set(manifest.modes)

        for label, src, mode in refs:
            path = preset_dir / src
            if not path.exists():
                issues.append(
                    LintIssue("error", preset_str, f"{label}: referenced file not found: {src}")
                )
            else:
                issues.extend(self._lint_media_weight(preset_str, label, src, path))

            if mode is not None and mode not in declared_modes:
                issues.append(
                    LintIssue(
                        "warning",
                        preset_str,
                        f"{label}: mode '{mode}' is not declared in preset.yml modes:",
                    )
                )

        return issues

    def _lint_media_weight(
        self, preset_str: str, label: str, src: str, path: Path
    ) -> List[LintIssue]:
        """Warn about assets too heavy to ship in a preset."""
        issues: List[LintIssue] = []

        size = path.stat().st_size
        if size > _MEDIA_MAX_BYTES:
            issues.append(
                LintIssue(
                    "warning",
                    preset_str,
                    f"{label}: {src} is {size // 1024} KiB, "
                    f"over the {_MEDIA_MAX_BYTES // 1024} KiB budget",
                )
            )

        if path.suffix.lower() in _MEDIA_RESIZABLE_SUFFIXES:
            try:
                width, height = self._image_processor.get_image_dimensions(path)
            except Exception:
                return issues  # Unreadable image; existence is all lint can assert.
            if max(width, height) > _MEDIA_MAX_DIMENSION:
                issues.append(
                    LintIssue(
                        "warning",
                        preset_str,
                        f"{label}: {src} is {width}x{height}, "
                        f"longest side over {_MEDIA_MAX_DIMENSION}px",
                    )
                )

        return issues

    def _lint_styles(self, preset_file: Path) -> List[LintIssue]:
        """Validate the optional `styles.yml` next to `preset.yml`.

        Re-parses the file independently of `PresetTemplateLoader` (which
        loads styles leniently - see `PresetTemplateLoader._load_styles`),
        so schema errors, a style missing `example_prompt` on both itself and
        the top-level `preview:` default, and missing `preview:` files
        surface here even though they never block the preset from loading.
        """
        issues: List[LintIssue] = []
        styles_file = preset_file.with_name("styles.yml")
        if not styles_file.exists():
            return issues

        preset_str = str(preset_file)

        try:
            with open(styles_file, "r") as f:
                data = yaml.load(f, Loader=yaml.FullLoader) or {}
        except Exception as e:
            issues.append(LintIssue("error", preset_str, f"styles.yml: failed to parse: {e}"))
            return issues

        parsed, errors = validate_styles_file(data)
        for err in errors:
            issues.append(LintIssue("error", preset_str, err))

        if parsed is None:
            return issues

        preset_dir = preset_file.parent
        for style in parsed.styles:
            if not (style.example_prompt or parsed.preview.example_prompt):
                issues.append(
                    LintIssue(
                        "error",
                        preset_str,
                        f"styles.yml: style '{style.id}' has no example_prompt - set it on the "
                        "style, or add one to the top-level preview: block as a shared default",
                    )
                )

            if not style.preview:
                continue
            path = preset_dir / style.preview
            if not path.exists():
                issues.append(
                    LintIssue(
                        "error",
                        preset_str,
                        f"styles.yml: style '{style.id}' preview file not found: {style.preview}",
                    )
                )

        return issues

    def _extract_literal_file_refs(self, node) -> List[str]:
        """Walk a parsed YAML structure looking for {"file": "..."} / {"path": "..."}
        entries whose value contains no Jinja template markers."""
        refs: List[str] = []
        if isinstance(node, dict):
            for key in ("file", "path"):
                value = node.get(key)
                if isinstance(value, str) and "{{" not in value and "{%" not in value:
                    refs.append(value)
            for value in node.values():
                refs.extend(self._extract_literal_file_refs(value))
        elif isinstance(node, list):
            for item in node:
                refs.extend(self._extract_literal_file_refs(item))
        return refs
