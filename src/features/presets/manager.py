"""
Preset manager - business logic layer for preset operations.

This module provides the PresetManager class that orchestrates all preset-related
business logic, including listing, installation, assignments, and plugin hook execution.
"""

import logging
from typing import Any, Dict, List, Optional


from src.features.presets import PresetTemplateLoader, PresetProcessor
from src.platform.templating import TemplateProcessor
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.features.forms.binding import bind_form
from src.features.presets.hooks import PRESET_HOOKS
from src.features.presets.pipeline_assembler import PipelineAssembler
from src.features.presets.templates import ModeTemplate, GenerationMode, sorted_forms, default_form_name
from src.pipelines.catalog import PipeCatalog
from src.platform.settings.settings import SettingsManager
from src.pipelines.graph import build_graph, PipelineGraph
from src.platform.security.user import User, AccountType
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.repository import DatabasePresetRepository
from src.features.users.repository import UserRepository
from src.features.user_groups.repository import UserGroupRepository
from src.features.presets.form_serializer import PresetFormSerializer

from src.features.forms.exceptions import FormNotFoundException
from .exceptions import (
    PresetNotFoundException,
    ModeNotFoundException,
    NoModesAvailableException,
    PresetNotInstalledException,
    PresetAlreadyInstalledException,
    PresetNotAssignedException,
    UserNotFoundException,
    InvalidUsersException,
    PermissionDeniedException,
    InvalidModeDataException,
    InvalidConfigurationException,
    InvalidFormOverridesException,
)
from src.features.presets.configuration import merge_configuration_schema, validate_configuration_value
from src.features.presets.form_overrides import (
    build_inventory_entries,
    validate_form_overrides,
)

logger = logging.getLogger(__name__)


class PresetManager:
    """
    Coordinates preset operations - business logic layer.

    Responsibilities:
    - Query operations (list, get, modes, forms)
    - Installation/uninstallation with hooks
    - User assignment management with hooks
    - Pipeline building delegation
    """

    def __init__(
        self,
        preset_loader: PresetTemplateLoader,
        preset_processor: PresetProcessor,
        template_processor: TemplateProcessor,
        file_preset_repository: FilePresetRepository,
        database_preset_repository: DatabasePresetRepository,
        user_repository: UserRepository,
        user_group_repository: UserGroupRepository,
        pipeline_builder: PipelineAssembler,
        pipe_catalog: PipeCatalog,
        plugin_registry: PluginRegistry,
        settings_manager: SettingsManager
    ):
        """Initialize PresetManager.

        Args:
            preset_loader: Loader for preset templates from files
            preset_processor: Processor for rendering preset configurations
            template_processor: Template processor for Jinja2 rendering
            file_preset_repository: Repository for file-based preset operations
            database_preset_repository: Repository for database preset operations
            user_repository: Repository for user operations
            pipeline_builder: THE canonical pipeline builder (shared with generation execution)
            pipe_catalog: Registry for resolving pipe classes, used to project the pipeline graph
            plugin_registry: Plugin registry for hook execution
            settings_manager: Resolves the user's storage root, so `get_pipeline`'s
                `bind_form` preview call can run the same media containment check
                a real generation does (spec follow-up #1)
        """
        self.preset_loader = preset_loader
        self.preset_processor = preset_processor
        self.file_repo = file_preset_repository
        self.db_repo = database_preset_repository
        self.user_repo = user_repository
        self.group_repo = user_group_repository
        self.pipeline_builder = pipeline_builder
        self.pipe_catalog = pipe_catalog
        self.plugins = plugin_registry
        self.settings_manager = settings_manager
        self.form_serializer = PresetFormSerializer(preset_loader, template_processor)

    # ========== Query Operations ==========

    def list_presets(
        self,
        user: User,
        include_uninstalled: bool = False
    ) -> List[Dict[str, Any]]:
        """List presets available to a user.

        For admin users with include_uninstalled=True, returns all presets
        with installation status. Otherwise, returns only assigned presets.

        Args:
            user: The current user
            include_uninstalled: Whether to include uninstalled presets (admin only)

        Returns:
            List of preset dictionaries
        """
        # Get all presets from files
        all_presets = self.file_repo.list_all_presets()

        if include_uninstalled and user.account_type == AccountType.ADMIN:
            # For admin users, show all presets with installation status
            installed_preset_ids = set([
                p.preset_id for p in self.db_repo.get_all_installed_presets()
            ])

            # Add installation status to each preset
            for preset in all_presets:
                preset['installed'] = preset['id'] in installed_preset_ids
                if preset['installed']:
                    # Add assignment summary for installed presets
                    summary = self.db_repo.get_preset_assignment_summary(preset['id'])
                    preset['assignment_count'] = summary['total_assignments']
                    preset['preset_db_id'] = summary.get('preset_db_id')
                    preset['group_count'] = self.group_repo.get_group_count_for_preset(preset['id'])

            return all_presets
        else:
            # For regular users, only show assigned presets
            available_preset_ids = set(
                self.db_repo.get_available_preset_ids_for_user(user.id)
            )

            # Filter to only assigned presets
            return [
                preset for preset in all_presets
                if preset['id'] in available_preset_ids
            ]

    def get_preset(self, preset_id: str) -> Dict[str, Any]:
        """Get detailed information about a preset.

        Args:
            preset_id: The preset ID to retrieve

        Returns:
            Preset data dictionary

        Raises:
            PresetNotFoundException: If the preset is not found
        """
        found_preset = self.file_repo.find_preset_by_id(preset_id)

        if not found_preset:
            raise PresetNotFoundException(preset_id)

        # Convert to PresetInfo and add vars (detail view: include the full gallery)
        preset_info = self.file_repo.preset_to_info(found_preset, include_gallery=True)
        data = preset_info.dict()

        # Include vars for frontend configuration
        data['vars'] = found_preset.vars or {}

        # Preset/family-level prompting guide + chat-workspace context knobs
        # (see docs/presets.md "LLM context"), for get_preset_info and similar
        # LLM-facing consumers.
        data['llm'] = found_preset.llm or {}

        return data

    def get_available_modes(self, preset_id: str) -> Dict[str, Any]:
        """Get available modes for a preset, each with its form "variants".

        Args:
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
        found_preset = self.file_repo.find_preset_by_id(preset_id)

        if not found_preset:
            raise PresetNotFoundException(preset_id)

        # Get available modes (modes are always plain string keys)
        available_modes = [
            {
                'name': mode_name,
                'label': mode_name.replace('_', ' ').title(),
                'variants': self._build_variants(mode_data),
                'source_plugin': mode_data.source_plugin,
            }
            for mode_name, mode_data in found_preset.modes.items()
        ]

        return {
            'preset_id': preset_id,
            'modes': available_modes,
            'default_mode': available_modes[0]['name'] if available_modes else None
        }

    @staticmethod
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
        self,
        preset_id: str,
        mode: Optional[str] = None,
        form_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get form schema for a preset mode.

        Args:
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
        found_preset = self.file_repo.find_preset_by_id(preset_id)

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
        form_config = self._get_form_config(mode_data, mode, form_name, found_preset, preset_id)

        if not form_config:
            raise FormNotFoundException(preset_id, mode, form_name)

        # Admin per-field overrides - applied for every caller (admin and
        # non-admin alike) on this user-facing endpoint via the single
        # serializer funnel (right after @loop/external-children expansion -
        # see PresetFormSerializer.process_form_fields). There is no
        # client-controllable flag to skip this: the admin tab reads the
        # unmerged inventory from get_form_overrides_inventory() instead.
        stored_overrides = self.db_repo.get_preset_form_overrides(preset_id).get(mode, {})

        # Process form fields
        form_schema = self.form_serializer.process_form_fields(form_config, preset_id, overrides=stored_overrides)

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
        self,
        mode_data: ModeTemplate,
        mode: str,
        form_name: Optional[str],
        found_preset,
        preset_id: str
    ):
        """Extract form configuration from a mode's forms.

        Args:
            mode_data: The mode data
            mode: The mode name
            form_name: Optional specific form name
            found_preset: The preset template (unused, kept for signature stability)
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
        self,
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
        found_preset = self.file_repo.find_preset_by_id(preset_id)

        if not found_preset:
            raise PresetNotFoundException(preset_id)

        self._validate_mode(found_preset, mode)

        bound_values = form_data or {}
        if user_id is not None:
            storage_dir = self.settings_manager.get_file_storage_directory(user_id)
            field_overrides = self.db_repo.get_preset_form_overrides(preset_id).get(mode, {})
            bound = bind_form(
                found_preset, mode, form_name, form_data, user_id,
                storage_dir=storage_dir, field_overrides=field_overrides,
            )
            bound_values = bound.values

        built_pipeline = self.pipeline_builder.build_pipeline(
            found_preset,
            form_data=bound_values,
            mode=mode
        )

        return build_graph(
            built_pipeline.pipes,
            self.pipe_catalog,
            preset_id=found_preset.id,
            mode=mode
        )

    def _validate_mode(self, preset_template, mode: str) -> None:
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

    def reload_preset(self, preset_id: str) -> Dict[str, Any]:
        """Reload a preset from disk.

        Args:
            preset_id: The preset ID to reload

        Returns:
            Updated preset data dictionary

        Raises:
            PresetNotFoundException: If the preset is not found
        """
        # Force reload from disk by clearing any caches
        self.preset_loader.clear_cache()

        # Reload all presets from disk
        self.preset_loader.load_presets()

        # Find the preset
        found_preset = self.file_repo.find_preset_by_id(preset_id)

        if not found_preset:
            raise PresetNotFoundException(preset_id)

        # Convert to PresetInfo (detail view: include the full gallery)
        preset_info = self.file_repo.preset_to_info(found_preset, include_gallery=True)
        return preset_info.dict()

    # ========== Installation Operations ==========

    def install_preset(self, preset_id: str, user: User) -> Dict[str, Any]:
        """Install a preset.

        Executes hooks:
        - preset.before_install: Can modify/validate data or block
        - preset.after_install: Notification of successful installation

        Args:
            preset_id: The preset ID to install
            user: The admin user performing the installation

        Returns:
            Installed preset data

        Raises:
            PermissionDeniedException: If user is not an admin
            PresetNotFoundException: If the preset is not found
            PresetAlreadyInstalledException: If already installed
        """
        # Only admins can install presets
        if user.account_type != AccountType.ADMIN:
            raise PermissionDeniedException("install_preset")

        # Check if preset exists in files
        found_preset = self.file_repo.find_preset_by_id(preset_id)
        if not found_preset:
            raise PresetNotFoundException(preset_id)

        # Check if already installed
        if self.db_repo.is_preset_installed(preset_id):
            raise PresetAlreadyInstalledException(preset_id)

        # Execute before_install hook
        hook_data, blocked = execute_hook(self.plugins,
            PRESET_HOOKS.before_install,
            {
                "preset_id": preset_id,
                "user_id": user.id,
                "preset_name": found_preset.name
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Installation blocked")
            logger.warning(f"Preset installation blocked by plugin: {reason}")
            raise PermissionDeniedException(f"install_preset: {reason}")

        # Install the preset
        installed_preset = self.db_repo.install_preset(preset_id)

        # Execute after_install hook
        execute_hook(self.plugins,
            PRESET_HOOKS.after_install,
            {
                "preset_id": preset_id,
                "user_id": user.id,
                "preset_name": found_preset.name,
                "installed_preset_id": installed_preset.id
            }
        )

        logger.info(f"Preset '{preset_id}' installed by user {user.id}")
        return installed_preset.to_dict()

    def uninstall_preset(self, preset_id: str, user: User) -> str:
        """Uninstall a preset and remove all user assignments.

        Executes hooks:
        - preset.before_uninstall: Can block
        - preset.after_uninstall: Notification

        Args:
            preset_id: The preset ID to uninstall
            user: The admin user performing the uninstallation

        Returns:
            Success message with count of removed assignments

        Raises:
            PermissionDeniedException: If user is not an admin
            PresetNotInstalledException: If preset is not installed
        """
        # Only admins can uninstall presets
        if user.account_type != AccountType.ADMIN:
            raise PermissionDeniedException("uninstall_preset")

        # Check if preset is installed
        if not self.db_repo.is_preset_installed(preset_id):
            raise PresetNotInstalledException(preset_id)

        # Get assignment summary before uninstalling
        summary = self.db_repo.get_preset_assignment_summary(preset_id)

        # Execute before_uninstall hook
        hook_data, blocked = execute_hook(self.plugins,
            PRESET_HOOKS.before_uninstall,
            {
                "preset_id": preset_id,
                "user_id": user.id,
                "assignment_count": summary['total_assignments']
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Uninstallation blocked")
            logger.warning(f"Preset uninstallation blocked by plugin: {reason}")
            raise PermissionDeniedException(f"uninstall_preset: {reason}")

        # Uninstall the preset
        success = self.db_repo.uninstall_preset(preset_id)

        if not success:
            raise PresetNotInstalledException(preset_id)

        # Execute after_uninstall hook
        execute_hook(self.plugins,
            PRESET_HOOKS.after_uninstall,
            {
                "preset_id": preset_id,
                "user_id": user.id,
                "removed_assignments": summary['total_assignments']
            }
        )

        logger.info(f"Preset '{preset_id}' uninstalled by user {user.id}")
        return f"Preset '{preset_id}' uninstalled successfully. Removed {summary['total_assignments']} user assignments."

    # ========== Assignment Operations ==========

    def assign_preset_to_users(
        self,
        preset_id: str,
        user_ids: List[str],
        admin: User
    ) -> Dict[str, Any]:
        """Assign a preset to multiple users.

        Executes hooks:
        - preset.before_assign: Can modify/validate data or block
        - preset.after_assign: Notification

        Args:
            preset_id: The preset ID to assign
            user_ids: List of user IDs to assign to
            admin: The admin user performing the assignment

        Returns:
            Assignment result dictionary

        Raises:
            PermissionDeniedException: If user is not an admin
            PresetNotInstalledException: If preset is not installed
            InvalidUsersException: If any users are invalid
        """
        # Only admins can assign presets
        if admin.account_type != AccountType.ADMIN:
            raise PermissionDeniedException("assign_preset_to_users")

        # Check if preset is installed
        if not self.db_repo.is_preset_installed(preset_id):
            raise PresetNotInstalledException(preset_id)

        # Validate all user IDs exist
        invalid_users = []
        for user_id in user_ids:
            if not self.user_repo.get_by_id(user_id):
                invalid_users.append(user_id)

        if invalid_users:
            raise InvalidUsersException(invalid_users)

        # Execute before_assign hook
        hook_data, blocked = execute_hook(self.plugins,
            PRESET_HOOKS.before_assign,
            {
                "preset_id": preset_id,
                "user_ids": user_ids,
                "admin_id": admin.id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Assignment blocked")
            logger.warning(f"Preset assignment blocked by plugin: {reason}")
            raise PermissionDeniedException(f"assign_preset_to_users: {reason}")

        # Allow hooks to modify user list
        user_ids = hook_data.get("user_ids", user_ids)

        # Assign preset to users
        assignments = self.db_repo.assign_preset_to_users(preset_id, user_ids)

        # Execute after_assign hook
        execute_hook(self.plugins,
            PRESET_HOOKS.after_assign,
            {
                "preset_id": preset_id,
                "user_ids": user_ids,
                "admin_id": admin.id,
                "assigned_count": len(assignments)
            }
        )

        logger.info(f"Preset '{preset_id}' assigned to {len(assignments)} users by admin {admin.id}")
        return {
            'preset_id': preset_id,
            'assigned_count': len(assignments),
            'assignments': [assignment.to_dict() for assignment in assignments]
        }

    def unassign_preset_from_user(
        self,
        preset_id: str,
        user_id: str,
        admin: User
    ) -> str:
        """Unassign a preset from a user.

        Executes hooks:
        - preset.before_unassign: Can block
        - preset.after_unassign: Notification

        Args:
            preset_id: The preset ID to unassign
            user_id: The user ID to unassign from
            admin: The admin user performing the unassignment

        Returns:
            Success message

        Raises:
            PermissionDeniedException: If user is not an admin
            UserNotFoundException: If user is not found
            PresetNotAssignedException: If preset is not assigned to user
        """
        # Only admins can unassign presets
        if admin.account_type != AccountType.ADMIN:
            raise PermissionDeniedException("unassign_preset_from_user")

        # Check if user exists
        if not self.user_repo.get_by_id(user_id):
            raise UserNotFoundException(user_id)

        # This endpoint manages the removable direct link only. Inherited group
        # access may remain after removal and must not make a missing direct
        # assignment look removable (or fire unassignment hooks for one).
        if not self.db_repo.is_preset_directly_assigned_to_user(preset_id, user_id):
            raise PresetNotAssignedException(preset_id, user_id)

        # Execute before_unassign hook
        hook_data, blocked = execute_hook(self.plugins,
            PRESET_HOOKS.before_unassign,
            {
                "preset_id": preset_id,
                "user_id": user_id,
                "admin_id": admin.id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Unassignment blocked")
            logger.warning(f"Preset unassignment blocked by plugin: {reason}")
            raise PermissionDeniedException(f"unassign_preset_from_user: {reason}")

        # Unassign the preset
        success = self.db_repo.unassign_preset_from_user(preset_id, user_id)

        if not success:
            raise PresetNotAssignedException(preset_id, user_id)

        # Execute after_unassign hook
        execute_hook(self.plugins,
            PRESET_HOOKS.after_unassign,
            {
                "preset_id": preset_id,
                "user_id": user_id,
                "admin_id": admin.id
            }
        )

        logger.info(f"Preset '{preset_id}' unassigned from user {user_id} by admin {admin.id}")
        return f"Preset '{preset_id}' unassigned from user '{user_id}'"

    def get_preset_assignments(
        self,
        preset_id: str,
        admin: User
    ) -> Dict[str, Any]:
        """Get assignment summary for a preset.

        Args:
            preset_id: The preset ID
            admin: The admin user requesting the summary

        Returns:
            Assignment summary dictionary

        Raises:
            PermissionDeniedException: If user is not an admin
            PresetNotInstalledException: If preset is not installed
        """
        # Only admins can view assignments
        if admin.account_type != AccountType.ADMIN:
            raise PermissionDeniedException("get_preset_assignments")

        summary = self.db_repo.get_preset_assignment_summary(preset_id)

        if not summary['installed']:
            raise PresetNotInstalledException(preset_id)

        # Add user details to assignments
        for assignment in summary['assignments']:
            user = self.user_repo.get_by_id(assignment['user_id'])
            if user:
                assignment['user'] = user.to_dict()

        return summary

    # ========== Configuration Operations (admin-set) ==========

    def get_preset_configuration(self, preset_id: str) -> Dict[str, Any]:
        """Get a preset's declared configuration schema merged with its stored values.

        Returns `{"preset_id": ..., "entries": [{"key", "type", "label",
        "description", "value"}]}` - `entries` is empty if the preset declares no
        `configuration:` block at all (not an error).

        Raises:
            PresetNotFoundException: If the preset is not found
        """
        found_preset = self.file_repo.find_preset_by_id(preset_id)
        if not found_preset:
            raise PresetNotFoundException(preset_id)

        stored_values = self.db_repo.get_preset_configuration(preset_id)
        entries = merge_configuration_schema(found_preset.configuration, stored_values)

        return {"preset_id": preset_id, "entries": entries}

    def set_preset_configuration(
        self,
        preset_id: str,
        values: Dict[str, Any],
        admin: User,
    ) -> Dict[str, Any]:
        """Set admin-set configuration values for a preset.

        Rejects unknown keys (not declared in preset.yml's `configuration:`) and
        type-invalid values (e.g. a `model_tags` entry naming a tag ID that doesn't
        exist), then persists and returns the same shape as `get_preset_configuration`.

        Args:
            preset_id: The preset ID
            values: `{key: value}` - must be a subset of the preset's declared keys
            admin: The admin user performing the update

        Raises:
            PermissionDeniedException: If user is not an admin
            PresetNotFoundException: If the preset is not found
            PresetNotInstalledException: If the preset is not installed
            InvalidConfigurationException: If any key is unknown or any value is invalid
        """
        if admin.account_type != AccountType.ADMIN:
            raise PermissionDeniedException("set_preset_configuration")

        found_preset = self.file_repo.find_preset_by_id(preset_id)
        if not found_preset:
            raise PresetNotFoundException(preset_id)

        if not self.db_repo.is_preset_installed(preset_id):
            raise PresetNotInstalledException(preset_id)

        declared = found_preset.configuration or {}
        errors: List[str] = []

        unknown_keys = sorted(set(values.keys()) - set(declared.keys()))
        if unknown_keys:
            errors.append(f"unknown configuration key(s): {unknown_keys}")

        if not errors:
            from src.features.tags.repository import tag_repo

            for key, value in values.items():
                config_type = declared[key]["type"]
                error = validate_configuration_value(config_type, value, tag_repo)
                if error:
                    errors.append(f"{key}: {error}")

        if errors:
            raise InvalidConfigurationException(preset_id, errors)

        # Merge into existing stored values rather than replacing wholesale, so a
        # PUT for one key never clobbers another key's previously-set value.
        current = self.db_repo.get_preset_configuration(preset_id)
        current.update(values)
        self.db_repo.set_preset_configuration(preset_id, current)

        logger.info(f"Preset '{preset_id}' configuration updated by admin {admin.id}: {sorted(values.keys())}")

        return self.get_preset_configuration(preset_id)

    # ========== Form Overrides Operations (admin-set) ==========

    def _validate_mode_exists(self, found_preset, preset_id: str, mode: Optional[str]) -> str:
        """Resolve `mode` (defaulting to the preset's first mode) and confirm it exists.

        Raises NoModesAvailableException if the preset declares no modes at
        all, or ModeNotFoundException if `mode` doesn't match one.
        """
        mode_keys = list((found_preset.modes or {}).keys())
        if not mode_keys:
            raise NoModesAvailableException(preset_id)
        if mode is None:
            return mode_keys[0]
        if mode not in mode_keys:
            raise ModeNotFoundException(preset_id, mode, mode_keys)
        return mode

    def get_form_overrides_inventory(
        self, preset_id: str, mode: Optional[str], admin: User,
    ) -> Dict[str, Any]:
        """The admin tab's unmerged view: every field in `mode`'s inventory
        (union across its form variants), each with its preset-declared
        default and its current override (`None` if unset).

        Raises:
            PermissionDeniedException: If user is not an admin
            PresetNotFoundException: If the preset is not found
            NoModesAvailableException: If the preset has no modes
            ModeNotFoundException: If `mode` doesn't exist on the preset
        """
        if admin.account_type != AccountType.ADMIN:
            raise PermissionDeniedException("get_form_overrides_inventory")

        found_preset = self.file_repo.find_preset_by_id(preset_id)
        if not found_preset:
            raise PresetNotFoundException(preset_id)

        mode = self._validate_mode_exists(found_preset, preset_id, mode)

        stored_overrides = self.db_repo.get_preset_form_overrides(preset_id).get(mode, {})
        fields = build_inventory_entries(found_preset, mode, stored_overrides)

        return {
            "preset_id": preset_id,
            "mode": mode,
            "modes": list((found_preset.modes or {}).keys()),
            "fields": fields,
        }

    def set_form_overrides(
        self,
        preset_id: str,
        mode: Optional[str],
        overrides: Dict[str, Any],
        admin: User,
    ) -> Dict[str, Any]:
        """Set admin per-field form overrides for one mode of a preset.

        Applies to every form variant of `mode`. Sending an empty object
        (`{}`) or `null` for a field clears its override. Rejects unknown
        field names (not in the mode's expanded field inventory) and
        type-invalid `default` values, then persists and returns the same
        shape as `get_form_overrides_inventory`.

        Args:
            preset_id: The preset ID
            mode: The mode name
            overrides: `{field_name: {default?, editable?, visible?} | {} | None}`
            admin: The admin user performing the update

        Raises:
            PermissionDeniedException: If user is not an admin
            PresetNotFoundException: If the preset is not found
            NoModesAvailableException: If the preset has no modes
            ModeNotFoundException: If `mode` doesn't exist on the preset
            PresetNotInstalledException: If the preset is not installed
            InvalidFormOverridesException: If any field is unknown or any value is invalid
        """
        if admin.account_type != AccountType.ADMIN:
            raise PermissionDeniedException("set_form_overrides")

        found_preset = self.file_repo.find_preset_by_id(preset_id)
        if not found_preset:
            raise PresetNotFoundException(preset_id)

        mode = self._validate_mode_exists(found_preset, preset_id, mode)

        if not self.db_repo.is_preset_installed(preset_id):
            raise PresetNotInstalledException(preset_id)

        errors = validate_form_overrides(found_preset, mode, overrides)
        if errors:
            raise InvalidFormOverridesException(preset_id, mode, errors)

        # Merge into the existing stored values for this mode rather than
        # replacing wholesale, so a PUT for one field never clobbers another
        # field's previously-set override - same pattern as
        # set_preset_configuration. A `{}`/`None` value clears that field's
        # override instead of storing it.
        stored_all = self.db_repo.get_preset_form_overrides(preset_id)
        stored_mode = dict(stored_all.get(mode, {}))
        for field_name, value in overrides.items():
            if not value:
                stored_mode.pop(field_name, None)
            else:
                stored_mode[field_name] = value

        if stored_mode:
            stored_all[mode] = stored_mode
        else:
            stored_all.pop(mode, None)

        self.db_repo.set_preset_form_overrides(preset_id, stored_all)

        logger.info(
            f"Preset '{preset_id}' mode '{mode}' form overrides updated by admin {admin.id}: "
            f"{sorted(overrides.keys())}"
        )

        return self.get_form_overrides_inventory(preset_id, mode, admin)
