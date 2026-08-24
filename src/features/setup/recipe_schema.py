"""Recipe schema: the declarative shape of a Phase-3 setup recipe.

A recipe is a versioned YAML document under `content/recipes/` describing the ordered
journey from "a fresh claim" to "a first real generation" for one target
(e.g. SDXL on the native engine): which bundled plugins must be enabled, which
backend engine to ensure, which artifacts (model files) it needs, which
preset(s) to install and hand to the owner, a smoke-generation reference, and
the ordered `steps` a setup run actually executes.

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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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

RECOGNIZED_STEP_KINDS = IMPLEMENTED_STEP_KINDS | DEFERRED_STEP_KINDS

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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

    def get_step(self, key: str) -> Optional[RecipeStep]:
        for step in self.steps:
            if step.key == key:
                return step
        return None

    def next_step_after(self, key: str) -> Optional[RecipeStep]:
        """The step that follows `key`, or `None` if `key` is the last step
        (or isn't found at all)."""
        keys = [s.key for s in self.steps]
        try:
            idx = keys.index(key)
        except ValueError:
            return None
        if idx + 1 < len(self.steps):
            return self.steps[idx + 1]
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


def validate_recipe_dict(data: Any) -> List[str]:
    """Validate a raw recipe dict (as loaded from YAML) against the schema.

    Returns a list of human-readable issue strings; an empty list means the
    dict is safe to hand to `parse_recipe`. Never raises - a malformed recipe
    is reported, not fatal (mirrors `PresetTemplateLoader.load_errors`).
    """
    issues: List[str] = []

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
        filename = _require_str(entry, "filename", path, issues)
        if filename and ("/" in filename or "\\" in filename or ".." in filename):
            _err(issues, path, "'filename' must be a bare file name (no path separators)")
        if aid:
            if aid in artifact_ids:
                _err(issues, path, f"duplicate artifact id '{aid}'")
            artifact_ids.add(aid)

        size_bytes = entry.get("size_bytes")
        if size_bytes is not None and (not isinstance(size_bytes, int) or size_bytes < 0):
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
        if kind not in RECOGNIZED_STEP_KINDS:
            _err(
                issues,
                path,
                f"unknown step kind '{kind}' (recognized: {sorted(RECOGNIZED_STEP_KINDS)})",
            )
            continue

        _validate_step_params(kind, params, path, issues, plugin_ids, artifact_ids, preset_ids)

    return issues


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


def parse_recipe(data: Dict[str, Any], source_path: str = "") -> Recipe:
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

    artifacts = []
    for a in data.get("artifacts", []):
        checksum_data = a.get("checksum")
        checksum = (
            RecipeChecksum(algorithm=checksum_data["algorithm"], value=checksum_data.get("value"))
            if checksum_data
            else None
        )
        artifacts.append(
            RecipeArtifact(
                id=a["id"],
                kind=a["kind"],
                model_type=a["model_type"],
                filename=a["filename"],
                display_name=a.get("display_name", ""),
                required=bool(a.get("required", True)),
                size_bytes=a.get("size_bytes"),
                checksum=checksum,
                capability=a.get("capability"),
                provider_hint=dict(a.get("provider_hint") or {}),
            )
        )

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
        category=data.get("category", ""),
        plugins=plugins,
        backend=backend,
        artifacts=artifacts,
        presets=presets,
        smoke=smoke,
        steps=steps,
        source_path=source_path,
    )
