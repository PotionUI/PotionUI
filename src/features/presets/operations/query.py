"""Preset query operations: list, get, modes, form schema, pipeline preview.

Module-level functions, `PresetCollaborators` as their leading arg - no class
holds them together (see `src.features.presets.collaborators`'s docstring).
"""
import logging
from typing import Any, Dict, List, Optional

from src.features.forms.binding import bind_form
from src.features.forms.exceptions import FormNotFoundException
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.exceptions import (
    InvalidModeDataException,
    ModeNotFoundException,
    NoModesAvailableException,
    PresetNotFoundException,
)
from src.features.presets.templates import ModeTemplate, GenerationMode, sorted_forms, default_form_name
from src.pipelines.graph import build_graph, PipelineGraph
from src.platform.security.user import User, AccountType

logger = logging.getLogger(__name__)


def list_presets(
    collaborators: PresetCollaborators,
    user: User,
    include_uninstalled: bool = False
) -> List[Dict[str, Any]]:
    """List presets available to a user.

    For admin users with include_uninstalled=True, returns all presets
    with installation status. Otherwise, returns only assigned presets.

    Args:
        collaborators: PresetCollaborators
        user: The current user
        include_uninstalled: Whether to include uninstalled presets (admin only)

    Returns:
        List of preset dictionaries
    """
    # Get all presets from files
    all_presets = collaborators.file_repo.list_all_presets()

    if include_uninstalled and user.account_type == AccountType.ADMIN:
        # For admin users, show all presets with installation status
        installed_preset_ids = set([
            p.preset_id for p in collaborators.db_repo.get_all_installed_presets()
        ])

        # Add installation status to each preset
        for preset in all_presets:
            preset['installed'] = preset['id'] in installed_preset_ids
            if preset['installed']:
                # Add assignment summary for installed presets
                summary = collaborators.db_repo.get_preset_assignment_summary(preset['id'])
                preset['assignment_count'] = summary['total_assignments']
                preset['preset_db_id'] = summary.get('preset_db_id')
                preset['group_count'] = collaborators.group_repo.get_group_count_for_preset(preset['id'])

        return all_presets
    else:
        # For regular users, only show assigned presets
        available_preset_ids = set(
            collaborators.db_repo.get_available_preset_ids_for_user(user.id)
        )

        # Filter to only assigned presets
        return [
            preset for preset in all_presets
            if preset['id'] in available_preset_ids
        ]


def get_preset(collaborators: PresetCollaborators, preset_id: str) -> Dict[str, Any]:
    """Get detailed information about a preset.

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID to retrieve

    Returns:
        Preset data dictionary

    Raises:
        PresetNotFoundException: If the preset is not found
    """
    found_preset = collaborators.file_repo.find_preset_by_id(preset_id)

    if not found_preset:
        raise PresetNotFoundException(preset_id)

    # Convert to PresetInfo and add vars (detail view: include the full gallery)
    preset_info = collaborators.file_repo.preset_to_info(found_preset, include_gallery=True)
    data = preset_info.dict()

    # Include vars for frontend configuration
    data['vars'] = found_preset.vars or {}

    # Preset/family-level prompting guide + chat-workspace context knobs
    # (see docs/presets.md "LLM context"), for get_preset_info and similar
    # LLM-facing consumers.
    data['llm'] = found_preset.llm or {}

    return data


def get_available_modes(collaborators: PresetCollaborators, preset_id: str) -> Dict[str, Any]:
    """Get available modes for a preset, each with its form "variants".

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID

    Returns:
        Dictionary with preset_id, modes list (each carrying a `variants`
        list - see docs/presets.md "Variants" - and `source_plugin`, the
        contributing plugin's id when the mode came from a plugin's
        `preset_modes:`, `null` for a mode the preset declares
        itself), and default_mode

    Raises:
        PresetNotFoundException: If the preset is not found
    """
    found_preset = collaborators.file_repo.find_preset_by_id(preset_id)

    if not found_preset:
        raise PresetNotFoundException(preset_id)

    # Get available modes (modes are always plain string keys)
    available_modes = [
        {
            'name': mode_name,
            'label': mode_name.replace('_', ' ').title(),
            'variants': _build_variants(mode_data),
            'source_plugin': mode_data.source_plugin,
        }
        for mode_name, mode_data in found_preset.modes.items()
    ]

    return {
        'preset_id': preset_id,
        'modes': available_modes,
        'default_mode': available_modes[0]['name'] if available_modes else None
    }


def _build_variants(mode_data: ModeTemplate) -> List[Dict[str, Any]]:
    """Project a mode's forms into the `variants` contract: sorted by
    (order, name), with exactly one flagged `default: true` (the
    variant's own flag if set, else the first after sorting)."""
    forms = sorted_forms(mode_data)
    default_name = default_form_name(mode_data)
    return [
        {
            'name': form.name,
            'label': form.label or form.name.replace('_', ' ').title(),
            'description': form.description,
            'examples': form.examples or [],
            'default': form.name == default_name,
            'order': form.order,
        }
        for form in forms
    ]


def get_form_schema(
    collaborators: PresetCollaborators,
    preset_id: str,
    mode: Optional[str] = None,
    form_name: Optional[str] = None
) -> Dict[str, Any]:
    """Get form schema for a preset mode.

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID
        mode: The mode name (optional, defaults to first available)
        form_name: The form name within the mode (optional)

    Returns:
        Form schema dictionary

    Raises:
        PresetNotFoundException: If the preset is not found
        NoModesAvailableException: If no modes are defined
        ModeNotFoundException: If the specified mode is not found
        FormNotFoundException: If the form is not found
    """
    found_preset = collaborators.file_repo.find_preset_by_id(preset_id)

    if not found_preset:
        raise PresetNotFoundException(preset_id)

    # Determine which mode to use (modes are always plain string keys)
    if mode is None:
        mode_keys = list(found_preset.modes.keys())
        if not mode_keys:
            raise NoModesAvailableException(preset_id)
        mode = mode_keys[0]

    mode_data = found_preset.modes.get(mode)
    if mode_data is None:
        raise ModeNotFoundException(preset_id, mode)

    # Get form configuration
    form_config = _get_form_config(mode_data, mode, form_name, preset_id)

    if not form_config:
        raise FormNotFoundException(preset_id, mode, form_name)

    # Admin per-field overrides - applied for every caller (admin and
    # non-admin alike) on this user-facing endpoint via the single
    # serializer funnel (right after @loop/external-children expansion -
    # see PresetFormSerializer.process_form_fields). There is no
    # client-controllable flag to skip this: the admin tab reads the
    # unmerged inventory from get_form_overrides_inventory() instead.
    stored_overrides = collaborators.db_repo.get_preset_form_overrides(preset_id).get(mode, {})

    # Process form fields
    form_schema = collaborators.form_serializer.process_form_fields(form_config, preset_id, overrides=stored_overrides)

    return {
        'preset_id': preset_id,
        'form_schema': form_schema,
        'debug_info': {
            'preset_name': found_preset.name,
            'has_form': hasattr(found_preset, 'form'),
            'form_name': form_config.name if hasattr(form_config, 'name') else None,
            'has_fields': hasattr(form_config, 'fields'),
            'fields_count': len(form_config.fields) if hasattr(form_config, 'fields') else 0,
            'fields_types': [f.type for f in form_config.fields] if hasattr(form_config, 'fields') else []
        }
    }


def _get_form_config(
    mode_data: ModeTemplate,
    mode: str,
    form_name: Optional[str],
    preset_id: str
):
    """Extract form configuration from a mode's forms.

    Args:
        mode_data: The mode data
        mode: The mode name
        form_name: Optional specific form name
        preset_id: The preset ID (for error messages)

    Returns:
        Form configuration or None
    """
    if not mode_data.forms:
        return None

    if form_name:
        for form in mode_data.forms:
            if form.name == form_name:
                return form
        raise FormNotFoundException(preset_id, mode, form_name)

    target_name = default_form_name(mode_data)
    for form in mode_data.forms:
        if form.name == target_name:
            return form
    return mode_data.forms[0]


def get_pipeline(
    collaborators: PresetCollaborators,
    preset_id: str,
    mode: str = "txt2img",
    form_data: Optional[Dict[str, Any]] = None,
    form_name: Optional[str] = None,
    user_id: Optional[str] = None,
) -> PipelineGraph:
    """Get pipeline graph preview for a preset.

    Builds the pipeline through the SAME canonical PipelineBuilder used to
    start real generations, then projects the resulting processed pipes
    into a graph - so the preview can never diverge from execution.

    When `user_id` is supplied, `form_data` is bound through the same
    `bind_form` boundary generation uses (spec §3/§8: preview and
    execution share one validated path) - unknown keys stripped, typed
    defaults applied, required/range/option validated, media containment
    checked. Without a `user_id` (the caller has no authenticated
    context - e.g. an anonymous template preview), `form_data` is passed
    through unbound, exactly as before.

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID
        mode: The generation mode
        form_data: Optional form data for rendering
        form_name: Optional form variant name (validated when `user_id`
            is given; otherwise informational only)
        user_id: Optional authenticated user, to enable `bind_form`

    Returns:
        PipelineGraph with nodes and connections

    Raises:
        PresetNotFoundException: If the preset is not found
        ModeNotFoundException: If the mode is not found
        InvalidModeDataException: If the mode data is malformed
        FormNotFoundException: If `form_name` doesn't exist (only checked
            when `user_id` is given)
    """
    found_preset = collaborators.file_repo.find_preset_by_id(preset_id)

    if not found_preset:
        raise PresetNotFoundException(preset_id)

    _validate_mode(found_preset, mode)

    bound_values = form_data or {}
    if user_id is not None:
        storage_dir = collaborators.settings.get_file_storage_directory(user_id)
        field_overrides = collaborators.db_repo.get_preset_form_overrides(preset_id).get(mode, {})
        bound = bind_form(
            found_preset, mode, form_name, form_data, user_id,
            storage_dir=storage_dir, field_overrides=field_overrides,
        )
        bound_values = bound.values

    built_pipeline = collaborators.pipeline_builder.build_pipeline(
        found_preset,
        form_data=bound_values,
        mode=mode
    )

    return build_graph(
        built_pipeline.pipes,
        collaborators.pipe_catalog,
        preset_id=found_preset.id,
        mode=mode
    )


def _validate_mode(preset_template, mode: str) -> None:
    """Validate that a mode exists and is well-formed for a preset.

    This mirrors the mode resolution PresetProcessor performs internally,
    but raises the richer domain exceptions the preview API surfaces
    (PresetProcessor itself only raises a bare KeyError on an unknown mode).

    Args:
        preset_template: The preset template
        mode: The mode name

    Raises:
        ModeNotFoundException: If the mode is not found
        InvalidModeDataException: If the mode data is in an invalid format
    """
    mode_data = None

    if isinstance(preset_template.modes, dict):
        if mode in preset_template.modes:
            mode_data = preset_template.modes[mode]
        else:
            try:
                generation_mode = GenerationMode(mode)
                if generation_mode in preset_template.modes:
                    mode_data = preset_template.modes[generation_mode]
            except ValueError:
                pass

    if mode_data is None:
        available_modes = []
        for key in preset_template.modes.keys():
            if isinstance(key, GenerationMode):
                available_modes.append(key.value)
            else:
                available_modes.append(str(key))

        raise ModeNotFoundException(
            preset_id=preset_template.id,
            mode=mode,
            available_modes=available_modes
        )

    if not isinstance(mode_data, (ModeTemplate, list)):
        raise InvalidModeDataException(preset_id=preset_template.id, mode=mode)


def reload_preset(collaborators: PresetCollaborators, preset_id: str) -> Dict[str, Any]:
    """Reload a preset from disk.

    Args:
        collaborators: PresetCollaborators
        preset_id: The preset ID to reload

    Returns:
        Updated preset data dictionary

    Raises:
        PresetNotFoundException: If the preset is not found
    """
    # Force reload from disk by clearing any caches
    collaborators.preset_loader.clear_cache()

    # Reload all presets from disk
    collaborators.preset_loader.load_presets()

    # Find the preset
    found_preset = collaborators.file_repo.find_preset_by_id(preset_id)

    if not found_preset:
        raise PresetNotFoundException(preset_id)

    # Convert to PresetInfo (detail view: include the full gallery)
    preset_info = collaborators.file_repo.preset_to_info(found_preset, include_gallery=True)
    return preset_info.dict()
