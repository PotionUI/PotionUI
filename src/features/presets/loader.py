"""
Preset template loader - loads preset configurations from YAML files.

This module provides the PresetTemplateLoader class that handles loading,
schema-validating, and caching preset templates from the filesystem.

Canonical preset layout (see docs/presets.md / src/features/presets/schema.py):

    content/presets/marketplace/<Model>/<variant>/  # shipped, tracked presets
    content/presets/local/<Model>/<variant>/        # user-owned, .gitignored, scanned the same way
        preset.yml            # manifest: schema, id, name, version, category,
                               # engine, tags, vars, modes (list)
        description.md         # optional (inline `description:` no longer supported)
        modes/<mode>/
            pipeline.yml        # {pipeline: [...]}
            form.yml            # the DEFAULT form variant (+ optional tabs/)
            variants/<name>/    # additional form variants (optional)
                form.yml

Invalid presets are skipped (not raised); every validation error is retained
in ``self.load_errors`` keyed by preset.yml path, for the preset lint tooling
and the developer lint endpoint.
"""

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from src.platform.observability.logger import logger
from src.features.presets.templates import PresetTemplate, PipeTemplate, FieldTemplate, FormTemplate, ModeTemplate
from .schema import (
    validate_manifest,
    validate_pipeline_file,
    validate_form_file,
    validate_field_list,
)

# The only variable ever used in an external `children:` fragment path today
# (verified against every built-in/custom preset). Resolved here, at load
# time, rather than through the full Jinja context - `paths.preset` is the
# one piece of that context knowable before any form/request data exists.
_CHILDREN_PATH_VAR_RE = re.compile(r"\{\{\s*paths\.preset\s*\}\}")


def plugin_preset_roots(manifests) -> List[Path]:
    """Resolve the preset roots contributed by a set of plugin manifests.

    Each manifest's ``presets:`` entries name a directory (relative to the
    plugin dir) scanned like the core ``presets/`` tree. Returns absolute
    directory paths; manifests without a ``presets`` section contribute
    nothing.
    """
    roots: List[Path] = []
    for manifest in manifests:
        entries = getattr(manifest, "presets", None) or []
        plugin_dir = getattr(manifest, "plugin_dir", None)
        if not entries or not plugin_dir:
            continue
        base = Path(plugin_dir).resolve()
        for entry in entries:
            path = entry.get("path") if isinstance(entry, dict) else None
            if path:
                roots.append(base / path)
    return roots


@dataclass
class PresetModeContribution:
    """One resolved `preset_modes:` entry - a plugin's `modes_root`
    (absolute) targeting an existing preset by id."""

    plugin_id: str
    target_preset_id: str
    modes_root: Path


def plugin_preset_mode_contributions(manifests) -> List[PresetModeContribution]:
    """Resolve `preset_modes:` entries across a set of plugin manifests.

    Ordered by plugin id (ascending), then by declaration order within each
    manifest's `preset_modes:` list - the deterministic "stable order" the
    collision rule (docs/presets.md "Plugin-contributed modes") resolves
    ties by. Manifest/plugin discovery order is NOT used: directory iteration
    order isn't a portable guarantee, so sorting by plugin id here is what
    actually makes collision resolution reproducible across runs/platforms.
    """
    contributions: List[PresetModeContribution] = []
    for manifest in sorted(manifests, key=lambda m: getattr(m, "id", "")):
        entries = getattr(manifest, "preset_modes", None) or []
        plugin_dir = getattr(manifest, "plugin_dir", None)
        if not entries or not plugin_dir:
            continue
        base = Path(plugin_dir).resolve()
        for entry in entries:
            target = entry.get("target") if isinstance(entry, dict) else None
            modes_root = entry.get("modes_root") if isinstance(entry, dict) else None
            if target and modes_root:
                contributions.append(
                    PresetModeContribution(
                        plugin_id=manifest.id,
                        target_preset_id=target,
                        modes_root=base / modes_root,
                    )
                )
    return contributions


def discover_form_variants(mode_dir: Path) -> List[Tuple[str, Path]]:
    """Discover a mode's form variants under the flattened layout.

    The mode dir itself holds the DEFAULT variant's `form.yml` (+ optional
    `tabs/` etc.); additional variants live under `variants/<name>/form.yml`.

    Returns ``(variant_name, form_dir)`` pairs, default first then variants
    sorted by directory name. ``form_dir`` is the directory containing that
    variant's ``form.yml`` (the mode dir for the default, ``variants/<name>/``
    for the rest). ``variant_name`` is the directory-derived fallback
    (``"default"`` for the default variant, the subdir name otherwise);
    ``form.yml``'s own ``name:`` still overrides it when the template is built.
    """
    variants: List[Tuple[str, Path]] = []
    if (mode_dir / "form.yml").exists():
        variants.append(("default", mode_dir))
    variants_dir = mode_dir / "variants"
    if variants_dir.exists():
        for variant_dir in sorted(variants_dir.iterdir()):
            if variant_dir.is_dir() and (variant_dir / "form.yml").exists():
                variants.append((variant_dir.name, variant_dir))
    return variants


def _known_field_types() -> set:
    """The set of registered field-type names, for the loader's cross-check.

    Prefers the shared, plugin-extended registry (populated by app boot). If
    it hasn't been populated yet (e.g. a preset loaded before/without app
    boot, such as in a standalone test), falls back to a private registry
    seeded with only the builtin types - still catches the mistakes the
    audit found (unregistered `text`/`info`) without making preset loading
    depend on import/boot ordering.
    """
    from src.platform.plugins.field_types import FieldTypeRegistry, field_type_registry

    if field_type_registry.all():
        return {d.type_name for d in field_type_registry.all()}

    from src.features.fields.builtin import register_builtin_fields

    private = FieldTypeRegistry()
    register_builtin_fields(private)
    return {d.type_name for d in private.all()}


class PresetTemplateLoader:
    """
    Loads preset templates from YAML files.

    Responsibilities:
    - Load preset configurations from one or more directories
    - Validate them against the canonical schema (src/features/presets/schema.py)
    - Parse validated YAML into PresetTemplate objects
    - Cache loaded presets with thread-safe lazy loading
    """

    def __init__(self, preset_files_paths: list, plugin_registry=None, shared_path=None):
        """
        Initialize the preset loader with one or more preset directories.

        Args:
            preset_files_paths: List of paths to preset directories (e.g.,
                ["content/presets/marketplace", "content/presets/local"])
            plugin_registry: Optional plugin registry. When set, enabled plugins
                that declare a ``presets:`` root in their manifest contribute
                those roots at load time, so a preset can live inside its owning
                plugin.
            shared_path: Where ``paths._shared`` resolves to. Defaults to the
                core ``content/presets/_shared`` tree - independent of
                ``preset_files_paths`` (which roots get scanned, and in what
                order, has no bearing on where the shared vocabulary lives).
        """
        if isinstance(preset_files_paths, str):
            preset_files_paths = [preset_files_paths]
        self.preset_files_paths = [Path(p) for p in preset_files_paths]
        self.plugin_registry = plugin_registry
        # Keep preset_files_path for backward compatibility (first path)
        self.preset_files_path = self.preset_files_paths[0] if self.preset_files_paths else Path("content/presets")
        self.shared_path = Path(shared_path) if shared_path is not None else Path("content/presets/_shared")
        self.presets: List[PresetTemplate] = []
        # path (str) -> list of "<file>: <path>: <message>" validation errors
        self.load_errors: Dict[str, List[str]] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def all_preset_roots(self) -> List[Path]:
        """The core preset paths plus enabled-plugin preset roots.

        The same set of roots ``_do_load_presets`` scans - so tooling (the
        linter, the developer lint endpoint) can cover exactly the presets the
        loader loads, including plugin-owned ones.
        """
        roots = list(self.preset_files_paths)
        if self.plugin_registry is not None:
            roots.extend(plugin_preset_roots(self.plugin_registry.get_enabled_plugins()))
        return roots

    def _load_preset_file(
        self, preset_path: Path, base_path: Path, errors_out: Dict[str, List[str]]
    ) -> Optional[PresetTemplate]:
        """Load and validate a preset from a preset.yml file.

        Args:
            preset_path: Full path to the preset.yml file
            base_path: Base directory this preset is being loaded from (e.g., "content/presets/marketplace")
            errors_out: Dict to record validation errors into, keyed by
                ``str(preset_path)``. Passed in rather than written to
                ``self.load_errors`` directly so a scan can build a whole new
                catalogue into local containers and swap them in atomically
                (see ``_scan_presets``/``reload``) — a caller reading
                ``self.presets``/``self.load_errors`` concurrently, without a
                lock, must never observe a partially-populated scan.

        Returns:
            PresetTemplate if successful, None otherwise (errors recorded in errors_out)
        """
        errors: List[str] = []

        try:
            with open(preset_path, 'r') as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            errors.append(f"preset.yml: failed to read/parse: {e}")
            errors_out[str(preset_path)] = errors
            logger.error(f"Error loading preset {preset_path}: {e}")
            return None

        manifest, manifest_errors = validate_manifest(data)
        errors.extend(manifest_errors)

        modes_dict: Dict[str, ModeTemplate] = {}
        if manifest is not None:
            for mode_name in manifest.modes:
                mode_template, mode_errors = self._load_mode(preset_path, mode_name)
                errors.extend(mode_errors)
                if mode_template is not None:
                    modes_dict[mode_name] = mode_template

        if errors:
            errors_out[str(preset_path)] = errors
            for err in errors:
                logger.error(f"Preset validation error [{preset_path}] {err}")
            return None

        description = data.get('description')
        if description is None:
            description_file = preset_path.with_name('description.md')
            if description_file.exists():
                with open(description_file, 'r') as df:
                    description = df.read()

        speed_profiles = None
        if manifest.speed_profiles:
            speed_profiles = {
                name: profile.model_dump(exclude_none=True)
                for name, profile in manifest.speed_profiles.items()
            }

        configuration = None
        if manifest.configuration:
            configuration = {
                key: entry.model_dump(exclude_none=True)
                for key, entry in manifest.configuration.items()
            }

        llm = manifest.llm.model_dump(exclude_none=True) if manifest.llm else None
        requires = manifest.requires.model_dump(exclude_none=True) if manifest.requires else None

        return PresetTemplate(
            id=manifest.id,
            name=manifest.name,
            version=manifest.version,
            description=description,
            tags=manifest.tags,
            category=manifest.category,
            modes=modes_dict,
            path=str(preset_path.parent),
            vars=manifest.vars,
            speed_profiles=speed_profiles,
            base_path=str(base_path),
            engine=manifest.engine,
            media=manifest.media.model_dump(exclude_none=True) if manifest.media else None,
            configuration=configuration,
            llm=llm,
            requires=requires,
        )

    def _load_mode(self, preset_path: Path, mode_name: str) -> Tuple[Optional[ModeTemplate], List[str]]:
        """Load a single mode (pipeline.yml + form variants) from its directory.

        Args:
            preset_path: Path to preset.yml
            mode_name: Name of the mode to load

        Returns:
            (ModeTemplate or None, list of error strings)
        """
        mode_dir = preset_path.parent / 'modes' / mode_name
        pipeline_file = mode_dir / 'pipeline.yml'

        if not pipeline_file.exists():
            return None, [f"modes/{mode_name}/pipeline.yml: file not found"]

        try:
            with open(pipeline_file, 'r') as pf:
                pipeline_data = yaml.safe_load(pf) or {}
        except Exception as e:
            return None, [f"modes/{mode_name}/pipeline.yml: failed to parse: {e}"]

        _, pipeline_errors = validate_pipeline_file(pipeline_data, prefix=f"modes/{mode_name}/pipeline.yml")

        errors = list(pipeline_errors)

        pipes: List[PipeTemplate] = []
        if not pipeline_errors:
            for pipe in pipeline_data.get('pipeline', []):
                pipes.append(PipeTemplate(**pipe))

        forms, form_errors = self._load_forms_from_mode_dir(mode_dir, mode_name)
        errors.extend(form_errors)

        if errors:
            return None, errors

        return ModeTemplate(forms=forms, pipes=pipes), []

    def _load_forms_from_mode_dir(self, mode_dir: Path, mode_name: str) -> Tuple[List[FormTemplate], List[str]]:
        """Load and validate a mode's form variants (flattened layout).

        The default variant is ``modes/<mode>/form.yml``; additional variants
        live under ``modes/<mode>/variants/<name>/form.yml`` (see
        ``discover_form_variants``).

        Args:
            mode_dir: Path to the mode directory (``modes/<mode>``)
            mode_name: Name of the owning mode (for error messages)

        Returns:
            (list of FormTemplate, list of error strings)
        """
        forms: List[FormTemplate] = []
        errors: List[str] = []

        # `mode_dir` is .../modes/<mode>; the preset root two levels up
        # (mode -> modes -> preset root) is what external
        # `children: "{{ paths.preset }}/..."` paths resolve against.
        preset_root = mode_dir.parent.parent

        for variant_name, form_dir in discover_form_variants(mode_dir):
            # A relative location under the mode, for error messages: the
            # default variant lives at the mode root, variants under variants/.
            loc = "" if form_dir == mode_dir else f"variants/{variant_name}/"
            form_file = form_dir / 'form.yml'

            try:
                with open(form_file, 'r') as ff:
                    form_data = yaml.safe_load(ff) or {}
            except Exception as e:
                errors.append(f"modes/{mode_name}/{loc}form.yml: failed to parse: {e}")
                continue

            prefix = f"modes/{mode_name}/{loc}form.yml"
            validated, form_errors = validate_form_file(form_data, prefix=prefix)
            if form_errors or validated is None:
                errors.extend(form_errors)
                continue

            # Build runtime fields from the VALIDATED model_dump(), not the raw
            # dict - so schema defaults/coercions aren't silently discarded.
            fields = []
            for field_data in validated.model_dump()['fields']:
                try:
                    fields.append(self._build_field_template(field_data, preset_root, prefix))
                except Exception as e:
                    errors.append(f"{prefix}: error building field: {e}")

            forms.append(FormTemplate(
                name=form_data.get('name', variant_name),
                fields=fields,
                label=form_data.get('label'),
                description=form_data.get('description'),
                examples=form_data.get('examples') or [],
                default=form_data.get('default', False),
                order=form_data.get('order', 0),
            ))

        return forms, errors

    def _build_field_template(self, field_data: dict, preset_root: Path, prefix: str) -> FieldTemplate:
        """Recursively convert a validated field dict into a FieldTemplate.

        Resolves `children:` given as an external-file path string ("{{
        paths.preset }}/modes/<mode>/tabs/<tab>.yml") at LOAD
        time: the fragment's own `fields:` list is loaded, schema-validated
        through `FieldSpec` (the same schema as `form.yml`'s own fields, not
        a weaker ad hoc dict walk), and built into real `FieldTemplate`
        children - so a preset's field tree is fully known at load time
        instead of only lazily, per-request.

        Args:
            field_data: A single field's validated dict (from `FormFile`/
                `FieldSpec` model_dump(), or from a validated external
                fragment).
            preset_root: The preset's root directory (what `paths.preset`
                resolves to).
            prefix: Source-file prefix for error messages.

        Raises:
            ValueError: unresolvable/invalid external children file, or a
                field `type` not present in the field-type registry.
        """
        field_data = dict(field_data)

        # "@loop" is a pipeline/form-expansion directive (a field list entry
        # that PresetProcessor expands into N real fields at render time via
        # its own `configuration.template`, not a renderable widget) - it was
        # never meant to be in the field-type registry, so it's exempt from
        # the unregistered-type check.
        field_type = field_data.get('type')
        if field_type != '@loop' and field_type not in _known_field_types():
            raise ValueError(
                f"field '{field_data.get('name')}': unregistered field type '{field_type}'"
            )

        children = field_data.get('children')
        if isinstance(children, str):
            resolved_path = _CHILDREN_PATH_VAR_RE.sub(str(preset_root), children)
            external_fields = self._load_external_children_file(Path(resolved_path), prefix)
            field_data['children'] = [
                self._build_field_template(c, preset_root, prefix) for c in external_fields
            ]
        elif isinstance(children, list):
            field_data['children'] = [
                self._build_field_template(c, preset_root, prefix) for c in children
            ]

        return FieldTemplate(**field_data)

    @staticmethod
    def _load_external_children_file(children_file: Path, prefix: str) -> List[dict]:
        """Load + schema-validate an external tab/children fragment's `fields:`.

        Returns validated field dicts (`FieldSpec.model_dump()`), never the
        raw YAML - so an external fragment goes through the exact same
        validation as `form.yml`'s own fields.
        """
        if not children_file.exists():
            raise ValueError(f"external children file not found: {children_file}")

        with open(children_file, 'r') as f:
            children_data = yaml.safe_load(f) or {}

        raw_fields = children_data.get('fields', [])
        validated, errors = validate_field_list(raw_fields, prefix=f"{prefix} -> {children_file}")
        if errors:
            raise ValueError("; ".join(errors))

        return [f.model_dump() for f in validated]

    def get_all_presets(self) -> List[Tuple[str, str]]:
        """
        Get list of all available presets for UI dropdown.

        Returns:
            List of tuples (preset_name, preset_id)
        """
        self._ensure_loaded()
        return sorted([(preset.name, preset.id) for preset in self.presets], key=lambda x: x[0])

    def _ensure_loaded(self):
        """Ensure presets are loaded (lazy loading with thread safety)"""
        if not self._loaded:
            with self._lock:
                # Double-check pattern for thread safety
                if not self._loaded:
                    self._do_load_presets()
                    self._loaded = True

    def _scan_presets(self) -> Tuple[List[PresetTemplate], Dict[str, List[str]]]:
        """Scan every preset root + apply plugin-contributed modes, returning
        a FRESH ``(presets, load_errors)`` pair.

        Pure with respect to ``self.presets``/``self.load_errors`` - nothing
        here reads or writes them, so the result can be swapped in atomically
        (a single attribute reassignment each, race-safe against an unlocked
        concurrent reader - see ``reload``/``_do_load_presets``) instead of
        being built by clearing and incrementally repopulating the LIVE
        containers a reader might be iterating.
        """
        presets: List[PresetTemplate] = []
        errors: Dict[str, List[str]] = {}

        # Core preset directories plus the roots contributed by enabled plugins
        # (a plugin owning its presets).
        for base_path in self.all_preset_roots():
            if not base_path.exists():
                logger.info(f"Preset directory does not exist, skipping: {base_path}")
                continue

            logger.info(f"Loading presets from: {base_path}")
            for preset_file in base_path.rglob("preset.yml"):
                logger.debug(f"Loading preset file: {preset_file}")
                preset = self._load_preset_file(preset_file, base_path, errors)
                if preset:
                    presets.append(preset)
                    logger.debug(f"Loaded preset: {preset.name} (from {base_path})")
                else:
                    logger.error(f"Failed to load preset: {preset_file}")

        logger.info(f"Preset loading complete. Loaded {len(presets)} presets")

        self._apply_preset_mode_contributions(presets, errors)
        return presets, errors

    def _do_load_presets(self):
        """Scan, then swap the result into ``self.presets``/``self.load_errors``."""
        logger.info("Starting preset loading...")
        presets, errors = self._scan_presets()
        self.presets, self.load_errors = presets, errors

    def _apply_preset_mode_contributions(
        self, presets: List[PresetTemplate], errors: Dict[str, List[str]]
    ) -> None:
        """Merge plugin-contributed modes into already-loaded presets.

        Operates on the ``presets``/``errors`` containers a scan is building
        (see ``_scan_presets``), never ``self.presets``/``self.load_errors``
        directly - same reason as ``_load_preset_file``'s ``errors_out`` param.
        Runs after the main load loop so every core/plugin-owned preset is
        known before any contribution is considered. See docs/presets.md
        "Plugin-contributed modes" for the full contract; summary:

        - A target preset that isn't loaded (not installed, or its owning
          plugin disabled) means the contribution is simply absent - not an
          error, since a plugin targeting a preset the user doesn't have is
          normal.
        - A contributed mode is validated through the SAME `_load_mode` path
          a core mode goes through - no second validator.
        - Collisions are deterministic and never silent: a name already taken
          by a CORE mode always wins (the contribution is rejected, error
          attributed to the plugin); a name already claimed by an earlier
          contribution (same or different plugin) also wins over a later one,
          "earlier" meaning stable-sorted by plugin id then declaration order
          (see `plugin_preset_mode_contributions`).
        """
        if self.plugin_registry is None:
            return

        contributions = plugin_preset_mode_contributions(self.plugin_registry.get_enabled_plugins())
        claimed: Dict[Tuple[str, str], str] = {}

        for contribution in contributions:
            target = next((p for p in presets if p.id == contribution.target_preset_id), None)
            if target is None:
                continue

            modes_dir = contribution.modes_root / "modes"
            error_prefix = (
                f"plugin '{contribution.plugin_id}' preset_modes "
                f"(target '{contribution.target_preset_id}')"
            )
            if not modes_dir.exists():
                errors.setdefault(error_prefix, []).append(
                    f"modes_root '{contribution.modes_root}' has no modes/ directory"
                )
                continue

            # A synthetic, never-opened preset.yml path: `_load_mode` only ever
            # reads `preset_path.parent` to find `modes/<name>/`, so this lets
            # it resolve a contributed mode exactly like a core one, rooted at
            # the plugin's modes_root instead of a preset directory.
            synthetic_preset_path = contribution.modes_root / "preset.yml"

            for mode_dir in sorted(p for p in modes_dir.iterdir() if p.is_dir()):
                mode_name = mode_dir.name
                error_key = f"{error_prefix} modes/{mode_name}"

                mode_template, mode_errors = self._load_mode(synthetic_preset_path, mode_name)
                if mode_errors:
                    errors.setdefault(error_key, []).extend(mode_errors)
                    continue

                existing = target.modes.get(mode_name)
                if existing is not None and existing.source_plugin is None:
                    errors.setdefault(error_key, []).append(
                        f"collides with a core mode already declared by preset "
                        f"'{contribution.target_preset_id}' - the core mode wins, "
                        f"this contribution is rejected"
                    )
                    continue

                claim_key = (contribution.target_preset_id, mode_name)
                prior_claimant = claimed.get(claim_key)
                if prior_claimant is not None:
                    errors.setdefault(error_key, []).append(
                        f"mode '{mode_name}' on preset '{contribution.target_preset_id}' was "
                        f"already contributed by plugin '{prior_claimant}' - first contributor "
                        f"(by plugin id, then declaration order) wins, this contribution is rejected"
                    )
                    continue

                mode_template.source_plugin = contribution.plugin_id
                target.modes[mode_name] = mode_template
                claimed[claim_key] = contribution.plugin_id

    def load_presets(self):
        """Load all presets from both catalogue and custom directories"""
        self._do_load_presets()
        self._loaded = True
        return self.presets

    def clear_cache(self):
        """Clear the preset cache and force reload on next access.

        Legacy LAZY invalidate: empties ``self.presets`` immediately (see
        ``test_clear_cache``'s contract) and marks the loader stale, so the
        NEXT ``_ensure_loaded()`` call rebuilds it - which means there is a
        window, between this call and whatever eventually triggers that next
        access, where ``self.presets`` is observably empty to any concurrent
        reader. Existing caller: ``operations.reload_preset`` (immediately
        follows this with an explicit ``load_presets()`` call, so the window
        is short but real). Prefer :meth:`reload` for a caller that wants the
        rescan to happen NOW with no empty-catalogue window - e.g. a plugin
        enable/disable, where an in-flight generation reading
        presets concurrently must never observe a temporarily-empty catalogue.
        """
        logger.info("Clearing preset cache")
        self.presets.clear()
        self._loaded = False

    def reload(self) -> None:
        """Eagerly rebuild the preset catalogue and atomically swap it in.

        Rescans every core + plugin preset root and reapplies plugin-
        contributed modes, exactly like the first load - but the
        scan runs into local containers (`_scan_presets`) and only takes
        `self._lock` to publish the finished result, so a concurrent reader
        (`get_preset_by_name`/`load_preset_by_id`/`get_all_presets`, none of
        which take the lock, by design, to keep reads fast) always observes
        either the complete OLD catalogue or the complete NEW one - never a
        partially-rescanned one. Contrast `clear_cache()` + `load_presets()`
        (the existing admin "reload one preset" path), which empties
        `self.presets` immediately and rebuilds separately, leaving a window
        where a concurrent reader sees an empty catalogue.

        Used by plugin enable/disable so plugin-shipped presets and
        plugin-contributed modes appear/disappear live, without a backend
        restart. The lock serializes concurrent `reload()` calls against each
        other (e.g. rapid enable/disable toggling) - it does not block readers.
        """
        with self._lock:
            self._do_load_presets()
            self._loaded = True

    def get_preset_by_name(self, preset_name: str) -> Optional[PresetTemplate]:
        """
        Get a specific preset by name.

        Args:
            preset_name (str): The name of the preset

        Returns:
            Optional[PresetTemplate]: The preset configuration or None if not found
        """
        self._ensure_loaded()
        for preset in self.presets:
            if preset.name == preset_name:
                return preset
        return None

    def load_preset_by_id(self, preset_id) -> Optional[PresetTemplate]:
        """Get a specific preset by its id"""
        self._ensure_loaded()
        for preset in self.presets:
            if preset.id == preset_id:
                return preset
        return None
