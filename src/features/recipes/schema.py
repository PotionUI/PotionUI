"""Recipe schema: the declarative shape of a recipe.

A recipe is a versioned YAML document under `content/recipes/` describing the ordered
journey from "a fresh claim" to "a first real generation" for one target
(e.g. SDXL on the native engine): which bundled plugins must be enabled, which
backend engine to ensure, which artifacts (model files) it needs, which
preset(s) to install and hand to the owner, a smoke-generation reference, and
the ordered `steps` a recipe run actually executes.

Recipes declare artifacts by *capability* (see `RecipeArtifact.capability`),
never by a concrete plugin id - resolving "how do we fetch this file" is core's
job (a later phase), not the recipe's. `plugins:` is different: it names
bundled plugin ids directly, because that's data an admin-authored recipe
describes about itself, not logic core hardcodes about a plugin.

This module is intentionally data-only: no dataclass here talks to the
database, a plugin registry, or the filesystem beyond the recipe YAML itself.
`RecipeCatalog` (recipe_catalog.py) owns discovery/IO; the step executors
(executors/) own actually doing something with a parsed `Recipe`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.features.presets.schema import CATEGORIES
from src.platform.runtime.gpu_profile import GPU_GENERATIONS, PRECISIONS

#: `schema_version` values this module knows how to parse. Bump when the YAML
#: shape changes in a way older parsing code cannot handle; existing recipe
#: files keep declaring whichever version they were authored against.
SUPPORTED_SCHEMA_VERSIONS = (1,)

#: Step kinds with a real executor.
IMPLEMENTED_STEP_KINDS = frozenset(
    {
        "plugins.ensure",
        "backend.ensure",
        "backend.detect",
        "models.index",
        "models.index_backend",
        "preset.ensure",
        "pipeline.render",
        "artifacts.plan",
        "artifacts.fetch",
        "generation.smoke",
        "workspace.activate",
    }
)

#: Step kinds the schema recognizes so a recipe referencing them lints fine,
#: but whose real work has not shipped yet. The executor registry resolves
#: these to a clear "coming in a later update" failure rather than an
#: unknown-kind error. Empty today - kept as a seam for the next wave rather
#: than removed outright.
DEFERRED_STEP_KINDS = frozenset()

#: The kinds core itself ships. A plugin adds more through its manifest's
#: `recipe_steps:` section; those reach validation as `extra_kinds` (see
#: `validate_recipe_dict`), so a recipe using an unregistered kind still lints
#: as an error.
RECOGNIZED_STEP_KINDS = IMPLEMENTED_STEP_KINDS | DEFERRED_STEP_KINDS

#: Where a recipe was discovered: the two core roots, or a plugin's own
#: `recipes:` root.
SOURCE_MARKETPLACE = "marketplace"
SOURCE_LOCAL = "local"
SOURCE_PLUGIN = "plugin"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VARIANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class RecipeError(Exception):
    """Raised by `parse_recipe` when asked to parse a dict `validate_recipe_dict`
    already found invalid. Callers should always validate first (see
    `RecipeCatalog.reload`) - this is a programming-error guard, not a
    user-facing error path."""


# --- parsed shape ------------------------------------------------------------


@dataclass(frozen=True)
class RecipePluginRequirement:
    id: str
    reason: str = ""


@dataclass(frozen=True)
class RecipeBackendRequirement:
    engine: str


@dataclass(frozen=True)
class RecipeChecksum:
    algorithm: str
    value: Optional[str] = None


@dataclass(frozen=True)
class RecipeVariantRule:
    min_vram_gb: Optional[float] = None
    generations: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RecipeArtifactVariant:
    id: str
    label: str
    precision: str
    filename: str
    size_bytes: Optional[int] = None
    checksum: Optional[RecipeChecksum] = None
    provider_hint: Dict[str, Any] = field(default_factory=dict)
    gated: bool = False
    license_url: Optional[str] = None
    uploader: str = ""
    source_url: Optional[str] = None
    recommended_for: Tuple[RecipeVariantRule, ...] = ()
    default: bool = False


@dataclass(frozen=True)
class RecipeArtifact:
    """A concrete file this recipe needs available before its content is
    usable (a checkpoint, LoRA, VAE, ...). `capability` is the declared
    capability (see plugin manifest `capabilities:`, e.g. "model-lookup")
    core resolves a download source through in a later wave - never a
    concrete plugin id. `checksum.value` is legitimately `None` when the
    recipe author doesn't know it yet."""

    id: str
    kind: str
    model_type: str
    filename: str
    display_name: str = ""
    required: bool = True
    size_bytes: Optional[int] = None
    checksum: Optional[RecipeChecksum] = None
    capability: Optional[str] = None
    provider_hint: Dict[str, Any] = field(default_factory=dict)
    #: Whether the source requires an accepted licence / access token before
    #: it will serve this file (e.g. a gated Hugging Face repo). Purely
    #: advisory - it changes what a recipe run tells the owner, never whether
    #: the download is attempted.
    gated: bool = False
    #: Where to go accept that licence, shown alongside the `gated` warning.
    license_url: Optional[str] = None
    variants: Tuple[RecipeArtifactVariant, ...] = ()

    def get_variant(self, variant_id: Optional[str]) -> Optional[RecipeArtifactVariant]:
        for variant in self.variants:
            if variant.id == variant_id:
                return variant
        return None

    @property
    def default_variant(self) -> Optional[RecipeArtifactVariant]:
        for variant in self.variants:
            if variant.default:
                return variant
        return self.variants[0] if self.variants else None

    def resolve(self, variant_id: Optional[str]) -> "RecipeArtifact":
        variant = self.get_variant(variant_id)
        if variant is None:
            return self
        return replace(
            self,
            filename=variant.filename,
            display_name=f"{self.display_name or self.id} ({variant.label}, {variant.precision})",
            size_bytes=variant.size_bytes,
            checksum=variant.checksum,
            provider_hint=dict(variant.provider_hint),
            gated=variant.gated,
            license_url=variant.license_url,
            variants=(),
        )


@dataclass(frozen=True)
class RecipePresetRef:
    preset_id: str
    path_hint: str = ""
    assign_to_owner: bool = True


@dataclass(frozen=True)
class RecipeSmokeRef:
    """What `generation.smoke` actually runs: real preset + mode, on tiny/
    fast form values so the first real generation an owner ever sees is quick.
    `form` is a plain field-name -> value overlay on top of the mode's normal
    fixture defaults (see `executors/_fixture_form.py`) - only the fields the
    recipe cares about pinning (resolution, steps, cfg, ...) need appear
    here; everything else still gets a sensible default."""

    preset_id: str
    mode: str
    prompt: str = ""
    negative_prompt: str = ""
    seed: Optional[int] = None
    form: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecipeStep:
    """One entry in the recipe's ordered execution plan. `key` is the stable
    identifier persisted as `setup_step_attempts.step_key`; `kind` dispatches
    to a `StepExecutor` (see executors/registry.py); `params` is kind-specific
    and cross-checked against the recipe's own declarations by
    `validate_recipe_dict` (e.g. a `preset.ensure` step's `preset_id` must be
    one this recipe actually declares under `presets:`)."""

    key: str
    kind: str
    title: str
    params: Dict[str, Any] = field(default_factory=dict)
    #: An onboarding-only step is skipped when the recipe is run from the
    #: admin Recipes page - it only makes sense during the first-run wizard
    #: (e.g. `workspace.activate`, which marks onboarding complete).
    onboarding_only: bool = False


@dataclass(frozen=True)
class Recipe:
    """A fully parsed, already-valid recipe. Construct only via `parse_recipe`
    on a dict `validate_recipe_dict` returned no issues for."""

    id: str
    schema_version: int
    version: int
    name: str
    engine: str
    summary: str = ""
    description: str = ""
    category: str = ""
    plugins: List[RecipePluginRequirement] = field(default_factory=list)
    backend: Optional[RecipeBackendRequirement] = None
    artifacts: List[RecipeArtifact] = field(default_factory=list)
    presets: List[RecipePresetRef] = field(default_factory=list)
    smoke: Optional[RecipeSmokeRef] = None
    steps: List[RecipeStep] = field(default_factory=list)
    source_path: str = ""
    #: Which root this recipe was discovered under - see `SOURCE_*`.
    source: str = SOURCE_MARKETPLACE
    #: The plugin that shipped it, when `source` is `plugin`.
    plugin_id: Optional[str] = None

    def steps_for_mode(self, mode: str) -> List[RecipeStep]:
        """The steps a run in `mode` executes. An admin run skips every
        `onboarding_only` step; an onboarding run executes all of them."""
        if mode == "onboarding":
            return list(self.steps)
        return [s for s in self.steps if not s.onboarding_only]

    def get_step(self, key: str) -> Optional[RecipeStep]:
        for step in self.steps:
            if step.key == key:
                return step
        return None

    def next_step_after(self, key: str, mode: str = "onboarding") -> Optional[RecipeStep]:
        """The step that follows `key` in `mode`'s plan, or `None` if `key` is
        the last step (or isn't found at all)."""
        steps = self.steps_for_mode(mode)
        keys = [s.key for s in steps]
        try:
            idx = keys.index(key)
        except ValueError:
            return None
        if idx + 1 < len(steps):
            return steps[idx + 1]
        return None

    def get_artifact(self, artifact_id: str) -> Optional[RecipeArtifact]:
        for artifact in self.artifacts:
            if artifact.id == artifact_id:
                return artifact
        return None


# --- validation ---------------------------------------------------------


def _err(issues: List[str], path: str, message: str) -> None:
    issues.append(f"{path}: {message}")


def _require_str(data: Dict[str, Any], key: str, path: str, issues: List[str], required: bool = True) -> Optional[str]:
    value = data.get(key)
    if value is None:
        if required:
            _err(issues, path, f"'{key}' is required")
        return None
    if not isinstance(value, str) or not value.strip():
        _err(issues, path, f"'{key}' must be a non-empty string")
        return None
    return value


def validate_recipe_dict(data: Any, extra_kinds: Optional[Iterable[str]] = None) -> List[str]:
    """Validate a raw recipe dict (as loaded from YAML) against the schema.

    Returns a list of human-readable issue strings; an empty list means the
    dict is safe to hand to `parse_recipe`. Never raises - a malformed recipe
    is reported, not fatal (mirrors `PresetTemplateLoader.load_errors`).

    `extra_kinds` are the step kinds plugins registered on this instance (see
    the manifest `recipe_steps:` section); anything outside core's own
    `RECOGNIZED_STEP_KINDS` plus those is still an unknown-kind error.
    """
    issues: List[str] = []
    known_kinds = RECOGNIZED_STEP_KINDS | set(extra_kinds or ())

    if not isinstance(data, dict):
        return ["recipe file must be a YAML mapping at the top level"]

    schema_version = data.get("schema_version")
    if not isinstance(schema_version, int) or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        _err(
            issues,
            "schema_version",
            f"must be one of {list(SUPPORTED_SCHEMA_VERSIONS)}, got {schema_version!r}",
        )

    recipe_id = data.get("id")
    if not isinstance(recipe_id, str) or not _SLUG_RE.match(recipe_id):
        _err(issues, "id", "must be a lowercase slug matching ^[a-z0-9][a-z0-9-]*$")

    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        _err(issues, "version", "must be an integer >= 1")

    _require_str(data, "name", "name", issues)
    engine = _require_str(data, "engine", "engine", issues)

    category = data.get("category")
    if category not in CATEGORIES:
        _err(issues, "category", f"must be one of {list(CATEGORIES)}, got {category!r}")

    # --- plugins ---
    plugin_ids = set()
    plugins = data.get("plugins", [])
    if not isinstance(plugins, list):
        _err(issues, "plugins", "must be a list")
        plugins = []
    for i, entry in enumerate(plugins):
        path = f"plugins[{i}]"
        if not isinstance(entry, dict):
            _err(issues, path, "must be a mapping")
            continue
        pid = _require_str(entry, "id", path, issues)
        if pid:
            if pid in plugin_ids:
                _err(issues, path, f"duplicate plugin id '{pid}'")
            plugin_ids.add(pid)

    # --- backend ---
    backend = data.get("backend")
    if backend is not None:
        if not isinstance(backend, dict):
            _err(issues, "backend", "must be a mapping")
        else:
            backend_engine = _require_str(backend, "engine", "backend", issues)
            if backend_engine and engine and backend_engine != engine:
                _err(
                    issues,
                    "backend.engine",
                    f"must match the recipe's top-level engine ('{engine}'), got '{backend_engine}'",
                )

    # --- artifacts ---
    artifact_ids = set()
    artifacts = data.get("artifacts", [])
    if not isinstance(artifacts, list):
        _err(issues, "artifacts", "must be a list")
        artifacts = []
    for i, entry in enumerate(artifacts):
        path = f"artifacts[{i}]"
        if not isinstance(entry, dict):
            _err(issues, path, "must be a mapping")
            continue
        aid = _require_str(entry, "id", path, issues)
        _require_str(entry, "kind", path, issues)
        _require_str(entry, "model_type", path, issues)
        if aid:
            if aid in artifact_ids:
                _err(issues, path, f"duplicate artifact id '{aid}'")
            artifact_ids.add(aid)
        if "variants" in entry:
            _validate_variant_slot(entry, path, issues)
        else:
            _validate_file_fields(entry, path, issues)

    # --- presets ---
    preset_ids = set()
    presets = data.get("presets", [])
    if not isinstance(presets, list):
        _err(issues, "presets", "must be a list")
        presets = []
    if not presets:
        _err(issues, "presets", "must declare at least one preset")
    for i, entry in enumerate(presets):
        path = f"presets[{i}]"
        if not isinstance(entry, dict):
            _err(issues, path, "must be a mapping")
            continue
        pid = _require_str(entry, "preset_id", path, issues)
        if pid:
            preset_ids.add(pid)

    # --- smoke (optional) ---
    smoke = data.get("smoke")
    if smoke is not None:
        if not isinstance(smoke, dict):
            _err(issues, "smoke", "must be a mapping if given")
        else:
            _require_str(smoke, "preset_id", "smoke", issues)
            _require_str(smoke, "mode", "smoke", issues)
            seed = smoke.get("seed")
            if seed is not None and not isinstance(seed, int):
                _err(issues, "smoke", "'seed' must be an integer if given")
            form = smoke.get("form")
            if form is not None and not isinstance(form, dict):
                _err(issues, "smoke", "'form' must be a mapping if given")

    # --- steps ---
    steps = data.get("steps", [])
    if not isinstance(steps, list):
        _err(issues, "steps", "must be a list")
        steps = []
    if not steps:
        _err(issues, "steps", "must declare at least one step")

    step_keys = set()
    for i, entry in enumerate(steps):
        path = f"steps[{i}]"
        if not isinstance(entry, dict):
            _err(issues, path, "must be a mapping")
            continue

        key = _require_str(entry, "key", path, issues)
        if key:
            if key in step_keys:
                _err(issues, path, f"duplicate step key '{key}'")
            step_keys.add(key)

        kind = _require_str(entry, "kind", path, issues)
        _require_str(entry, "title", path, issues)

        params = entry.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            _err(issues, path, "'params' must be a mapping if given")
            params = {}

        if kind is None:
            continue
        if kind not in known_kinds:
            _err(
                issues,
                path,
                f"unknown step kind '{kind}' (recognized: {sorted(known_kinds)})",
            )
            continue

        onboarding_only = entry.get("onboarding_only")
        if onboarding_only is not None and not isinstance(onboarding_only, bool):
            _err(issues, path, "'onboarding_only' must be a boolean if given")

        _validate_step_params(kind, params, path, issues, plugin_ids, artifact_ids, preset_ids)

    return issues


_FILE_FIELDS = ("filename", "size_bytes", "checksum", "provider_hint", "gated", "license_url")
_VARIANT_KEYS = frozenset(
    {"id", "label", "precision", "uploader", "source_url", "recommended_for", "default"} | set(_FILE_FIELDS)
)


def _validate_file_fields(entry: Dict[str, Any], path: str, issues: List[str]) -> None:
    filename = _require_str(entry, "filename", path, issues)
    if filename and ("/" in filename or "\\" in filename or ".." in filename):
        _err(issues, path, "'filename' must be a bare file name (no path separators)")

    size_bytes = entry.get("size_bytes")
    if size_bytes is not None and (not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0):
        _err(issues, path, "'size_bytes' must be a non-negative integer if given")

    checksum = entry.get("checksum")
    if checksum is not None:
        if not isinstance(checksum, dict):
            _err(issues, path, "'checksum' must be a mapping if given")
        else:
            _require_str(checksum, "algorithm", f"{path}.checksum", issues)
            value = checksum.get("value")
            if value is not None and not isinstance(value, str):
                _err(issues, f"{path}.checksum", "'value' must be a string or null")

    provider_hint = entry.get("provider_hint")
    if provider_hint is not None and not isinstance(provider_hint, dict):
        _err(issues, path, "'provider_hint' must be a mapping if given")

    gated = entry.get("gated")
    if gated is not None and not isinstance(gated, bool):
        _err(issues, path, "'gated' must be a boolean if given")

    license_url = entry.get("license_url")
    if license_url is not None and (not isinstance(license_url, str) or not license_url.strip()):
        _err(issues, path, "'license_url' must be a non-empty string if given")


def _validate_variant_rule(rule: Any, path: str, issues: List[str]) -> None:
    if not isinstance(rule, dict):
        _err(issues, path, "must be a mapping")
        return
    unknown = sorted(set(rule) - {"min_vram_gb", "generations"})
    if unknown:
        _err(issues, path, f"unknown keys {unknown} (allowed: min_vram_gb, generations)")
    if not rule:
        _err(issues, path, "must declare 'min_vram_gb' and/or 'generations'")
    min_vram = rule.get("min_vram_gb")
    if min_vram is not None and (
        isinstance(min_vram, bool) or not isinstance(min_vram, (int, float)) or min_vram < 0
    ):
        _err(issues, path, "'min_vram_gb' must be a non-negative number")
    generations = rule.get("generations")
    if generations is not None:
        if not isinstance(generations, list) or not generations:
            _err(issues, path, "'generations' must be a non-empty list")
        else:
            for generation in generations:
                if generation not in GPU_GENERATIONS:
                    _err(issues, path, f"unknown GPU generation {generation!r} (known: {list(GPU_GENERATIONS)})")


def _validate_variant_slot(entry: Dict[str, Any], path: str, issues: List[str]) -> None:
    for key in _FILE_FIELDS:
        if key in entry:
            _err(issues, path, f"'{key}' belongs on each variant when 'variants' is given")
    variants = entry.get("variants")
    if not isinstance(variants, list) or not variants:
        _err(issues, path, "'variants' must be a non-empty list")
        return
    variant_ids = set()
    filenames = set()
    defaults = 0
    for j, variant in enumerate(variants):
        vpath = f"{path}.variants[{j}]"
        if not isinstance(variant, dict):
            _err(issues, vpath, "must be a mapping")
            continue
        vid = _require_str(variant, "id", vpath, issues)
        if vid:
            if not _VARIANT_ID_RE.match(vid):
                _err(issues, vpath, "'id' must be a lowercase slug (letters, digits, '-', '_')")
            if vid in variant_ids:
                _err(issues, vpath, f"duplicate variant id '{vid}'")
            variant_ids.add(vid)
        _require_str(variant, "label", vpath, issues)
        precision = _require_str(variant, "precision", vpath, issues)
        if precision and precision not in PRECISIONS:
            _err(issues, vpath, f"'precision' must be one of {list(PRECISIONS)}, got {precision!r}")
        _validate_file_fields(variant, vpath, issues)
        filename = variant.get("filename")
        if isinstance(filename, str):
            if filename in filenames:
                _err(issues, vpath, f"duplicate variant filename '{filename}'")
            filenames.add(filename)
        for key in ("uploader", "source_url"):
            value = variant.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                _err(issues, vpath, f"'{key}' must be a non-empty string if given")
        default = variant.get("default")
        if default is not None and not isinstance(default, bool):
            _err(issues, vpath, "'default' must be a boolean if given")
        elif default:
            defaults += 1
        rules = variant.get("recommended_for")
        if rules is not None:
            if not isinstance(rules, list):
                _err(issues, vpath, "'recommended_for' must be a list if given")
            else:
                for k, rule in enumerate(rules):
                    _validate_variant_rule(rule, f"{vpath}.recommended_for[{k}]", issues)
        unknown = sorted(set(variant) - _VARIANT_KEYS)
        if unknown:
            _err(issues, vpath, f"unknown keys {unknown}")
    if defaults != 1:
        _err(issues, path, f"exactly one variant must be marked 'default: true' (found {defaults})")



def _validate_step_params(
    kind: str,
    params: Dict[str, Any],
    path: str,
    issues: List[str],
    plugin_ids: set,
    artifact_ids: set,
    preset_ids: set,
) -> None:
    """Referential-integrity checks: a step may only reference ids the recipe
    itself declares (in `plugins:`/`artifacts:`/`presets:`)."""

    if kind == "plugins.ensure":
        ids = params.get("plugin_ids")
        if not isinstance(ids, list) or not ids:
            _err(issues, path, "'params.plugin_ids' must be a non-empty list")
            return
        for pid in ids:
            if pid not in plugin_ids:
                _err(issues, path, f"params.plugin_ids references undeclared plugin '{pid}'")

    elif kind == "backend.ensure":
        if not isinstance(params.get("engine"), str) or not params["engine"].strip():
            _err(issues, path, "'params.engine' is required")

    elif kind == "backend.detect":
        if not isinstance(params.get("engine"), str) or not params["engine"].strip():
            _err(issues, path, "'params.engine' is required")
        base_url = params.get("base_url")
        if base_url is not None and (not isinstance(base_url, str) or not base_url.strip()):
            _err(issues, path, "'params.base_url' must be a non-empty string if given")

    elif kind in ("artifacts.plan", "artifacts.fetch"):
        ids = params.get("artifact_ids")
        if not isinstance(ids, list) or not ids:
            _err(issues, path, "'params.artifact_ids' must be a non-empty list")
            return
        for aid in ids:
            if aid not in artifact_ids:
                _err(issues, path, f"params.artifact_ids references undeclared artifact '{aid}'")

    elif kind in ("models.index", "models.index_backend"):
        if not isinstance(params.get("engine"), str) or not params["engine"].strip():
            _err(issues, path, "'params.engine' is required")

    elif kind == "preset.ensure":
        pid = params.get("preset_id")
        if not isinstance(pid, str) or not pid.strip():
            _err(issues, path, "'params.preset_id' is required")
        elif pid not in preset_ids:
            _err(issues, path, f"params.preset_id references undeclared preset '{pid}'")

    elif kind in ("pipeline.render", "generation.smoke"):
        pid = params.get("preset_id")
        if not isinstance(pid, str) or not pid.strip():
            _err(issues, path, "'params.preset_id' is required")
        elif pid not in preset_ids:
            _err(issues, path, f"params.preset_id references undeclared preset '{pid}'")
        if not isinstance(params.get("mode"), str) or not params["mode"].strip():
            _err(issues, path, "'params.mode' is required")


def _parse_checksum(data: Optional[Dict[str, Any]]) -> Optional[RecipeChecksum]:
    if not data:
        return None
    return RecipeChecksum(algorithm=data["algorithm"], value=data.get("value"))


def _parse_variant(data: Dict[str, Any]) -> RecipeArtifactVariant:
    return RecipeArtifactVariant(
        id=data["id"],
        label=data["label"],
        precision=data["precision"],
        filename=data["filename"],
        size_bytes=data.get("size_bytes"),
        checksum=_parse_checksum(data.get("checksum")),
        provider_hint=dict(data.get("provider_hint") or {}),
        gated=bool(data.get("gated", False)),
        license_url=data.get("license_url"),
        uploader=data.get("uploader") or "",
        source_url=data.get("source_url"),
        recommended_for=tuple(
            RecipeVariantRule(
                min_vram_gb=rule.get("min_vram_gb"),
                generations=tuple(rule.get("generations") or ()),
            )
            for rule in data.get("recommended_for") or ()
        ),
        default=bool(data.get("default", False)),
    )


def _parse_artifact(a: Dict[str, Any]) -> RecipeArtifact:
    variants = tuple(_parse_variant(v) for v in a.get("variants") or ())
    artifact = RecipeArtifact(
        id=a["id"],
        kind=a["kind"],
        model_type=a["model_type"],
        filename=a.get("filename", ""),
        display_name=a.get("display_name", ""),
        required=bool(a.get("required", True)),
        size_bytes=a.get("size_bytes"),
        checksum=_parse_checksum(a.get("checksum")),
        capability=a.get("capability"),
        provider_hint=dict(a.get("provider_hint") or {}),
        gated=bool(a.get("gated", False)),
        license_url=a.get("license_url"),
        variants=variants,
    )
    if not variants:
        return artifact
    default = artifact.default_variant
    return replace(
        artifact,
        filename=default.filename,
        size_bytes=default.size_bytes,
        checksum=default.checksum,
        provider_hint=dict(default.provider_hint),
        gated=default.gated,
        license_url=default.license_url,
    )


def parse_recipe(
    data: Dict[str, Any],
    source_path: str = "",
    source: str = SOURCE_MARKETPLACE,
    plugin_id: Optional[str] = None,
) -> Recipe:
    """Parse an already-validated recipe dict into a `Recipe`.

    Callers must run `validate_recipe_dict` first and only call this when it
    returned no issues - this function assumes a well-formed shape and does
    not re-validate.
    """
    plugins = [
        RecipePluginRequirement(id=p["id"], reason=p.get("reason", ""))
        for p in data.get("plugins", [])
    ]

    backend_data = data.get("backend")
    backend = RecipeBackendRequirement(engine=backend_data["engine"]) if backend_data else None

    artifacts = [_parse_artifact(a) for a in data.get("artifacts", [])]

    presets = [
        RecipePresetRef(
            preset_id=p["preset_id"],
            path_hint=p.get("path_hint", ""),
            assign_to_owner=bool(p.get("assign_to_owner", True)),
        )
        for p in data.get("presets", [])
    ]

    smoke_data = data.get("smoke")
    smoke = (
        RecipeSmokeRef(
            preset_id=smoke_data["preset_id"],
            mode=smoke_data["mode"],
            prompt=smoke_data.get("prompt", ""),
            negative_prompt=smoke_data.get("negative_prompt", ""),
            seed=smoke_data.get("seed"),
            form=dict(smoke_data.get("form") or {}),
        )
        if smoke_data
        else None
    )

    steps = [
        RecipeStep(
            key=s["key"],
            kind=s["kind"],
            title=s.get("title", s["key"]),
            params=dict(s.get("params") or {}),
            onboarding_only=bool(s.get("onboarding_only", False)),
        )
        for s in data.get("steps", [])
    ]

    return Recipe(
        id=data["id"],
        schema_version=data["schema_version"],
        version=data["version"],
        name=data["name"],
        engine=data["engine"],
        summary=data.get("summary", ""),
        description=data.get("description", ""),
        category=data["category"],
        plugins=plugins,
        backend=backend,
        artifacts=artifacts,
        presets=presets,
        smoke=smoke,
        steps=steps,
        source_path=source_path,
        source=source,
        plugin_id=plugin_id,
    )
