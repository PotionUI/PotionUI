"""Field-options dispatch and the concrete option loaders behind it.

`get_field_options` is the `POST /api/form/options` entry point: it runs the
`form.before_get_options`/`form.after_get_options` hooks around a lookup in
the `FieldTypeRegistry`'s `options_provider` table. The loaders themselves
(`get_select_options`, `get_model_database_options`, `get_checkbox_options`)
are also registered directly onto that table by `register_builtin_fields`
(`src.features.fields.builtin`) - the registry is the single source of truth
both `get_field_options` and the `/api/fields/types` manifest dispatch off
of, so a field type's options load exactly one way regardless of caller.
"""
import glob
import logging
import os
from typing import Any, Dict, List, Optional

import yaml

from src.platform.plugins.hooks import execute_hook
from src.features.forms.hooks import FORM_HOOKS

logger = logging.getLogger(__name__)

# Field types whose options_provider is `get_model_database_options`. A
# reachable model-listing surface (via GET /api/fields/types), so it must be
# scoped to the caller's model access rather than left unfiltered.
_MODEL_OPTIONS_FIELD_TYPES = frozenset({"model", "models"})


def get_field_options(
    field_registry,
    plugin_registry,
    field_type: str,
    field_config: Dict[str, Any],
    current_user: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Get options for a specific field based on its configuration.

    Executes hooks:
    - form.before_get_options: Can modify field_config or block
    - form.after_get_options: Can modify/filter returned options

    Args:
        field_registry: The `FieldTypeRegistry` whose `options_provider`
            table field types dispatch through.
        plugin_registry: Fires the before/after hooks below.
        field_type: The type of field (select, model_select, model, checkbox_group)
        field_config: Configuration dictionary for the field
        current_user: The requesting user - for `model`/`models`
            field types, scopes the returned options to the user's model
            access via `ModelAccessPolicy` (STRICT: no assignments = no
            options; admins unrestricted). `None` skips scoping - callers
            without a request context (tests) get the unfiltered
            behavior; the router always supplies the authenticated user.

    Returns:
        List of option dictionaries

    Raises:
        ValueError: If field type is unsupported or operation is blocked
    """
    # Execute before hook
    hook_data, blocked = execute_hook(plugin_registry,
        FORM_HOOKS.before_get_options,
        {"field_type": field_type, "field_config": field_config}
    )
    if blocked:
        reason = hook_data.get("block_reason", "Operation blocked")
        logger.warning(f"Field options retrieval blocked by plugin: {reason}")
        raise ValueError(reason)

    # Use potentially modified config from hook
    field_config = hook_data.get("field_config", field_config)

    if field_type in _MODEL_OPTIONS_FIELD_TYPES and current_user is not None:
        field_config = _scope_model_field_config(field_config, current_user)

    options_provider = field_registry.get(field_type).options_provider
    if options_provider is None:
        raise ValueError(f"Field type '{field_type}' does not support dynamic options")
    options = options_provider(field_config)

    # Execute after hook
    after_data, _ = execute_hook(plugin_registry,
        FORM_HOOKS.after_get_options,
        {"field_type": field_type, "options": options}
    )
    options = after_data.get("options", options)

    return options


def _scope_model_field_config(config: Dict[str, Any], current_user: Any) -> Dict[str, Any]:
    """Inject the caller's allowed model ids into `config` under the
    private `_allowed_model_ids` key `get_model_database_options` reads.

    A fresh `ModelAccessPolicy`/`ModelRepository` is built here rather than
    injected as a collaborator (deferred import, matching this codebase's
    convention for cross-cutting singletons - e.g.
    src/features/models/availability.py) so this doesn't need a new
    composition-root dependency for one call site.
    """
    from src.features.models.repository import model_repo
    from src.features.models.access_policy import ModelAccessPolicy

    # all_models=True: an admin gets None (unrestricted); everyone else
    # gets their real (possibly empty) assigned-model list regardless of
    # the flag - see ModelAccessPolicy.get_allowed_model_ids.
    allowed_model_ids = ModelAccessPolicy(model_repo).get_allowed_model_ids(current_user, all_models=True)
    return {**config, "_allowed_model_ids": allowed_model_ids}


def get_select_options(template_processor, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get select field options from various sources.

    Supports:
    - Static options from configuration
    - File-based options (YAML files)
    - File system scanning for .safetensors files

    Args:
        template_processor: Resolves templated `file.path`/`files.in` values.
        config: Field configuration with options, file, and/or files keys

    Returns:
        List of option dictionaries with label, value, and optional example
    """
    options = []

    # Static options from configuration
    static_options = config.get('options', [])
    for option in static_options:
        options.append({
            'label': option.get('label'),
            'value': option.get('value'),
            'example': option.get('example')
        })

    # File-based options
    if 'file' in config:
        file_config = config['file']
        file_path = file_config.get('path')

        if file_path:
            # Process template in file path
            processed_path = template_processor.process_template(file_path, {})

            try:
                with open(processed_path, 'r', encoding='utf-8') as f:
                    file_options = yaml.safe_load(f)

                if isinstance(file_options, list):
                    for option in file_options:
                        if isinstance(option, dict):
                            options.append({
                                'label': option.get('label'),
                                'value': option.get('value'),
                                'example': option.get('example')
                            })
            except FileNotFoundError:
                logger.debug(f"Options file not found: {processed_path}")
            except yaml.YAMLError as e:
                logger.debug(f"Invalid YAML in {processed_path}: {e}")

    # File system scanning
    if 'files' in config:
        files_config = config['files']
        directory = files_config.get('in')

        if directory:
            processed_dir = template_processor.process_template(directory, {})
            pattern = os.path.join(processed_dir, "*.safetensors")

            try:
                files = glob.glob(pattern)
                for file_path in files:
                    filename = os.path.basename(file_path)
                    name_without_ext = filename.replace('.safetensors', '')
                    options.append({
                        'label': name_without_ext,
                        'value': filename
                    })
            except Exception as e:
                logger.debug(f"Error scanning directory {processed_dir}: {e}")

    return options


def get_model_database_options(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get model options from the database.

    Args:
        config: Field configuration with optional model_type, include_providers, limit
            keys, plus the private `_allowed_model_ids` key `get_field_options`
            injects for `model`/`models` field types - `None`
            means unrestricted (admin, or no user context), an empty list means
            STRICT zero results, never "unfiltered".

    Returns:
        List of model option dictionaries with full metadata
    """
    from src.features.models.repository import model_repo

    # Get filter parameters
    model_type = config.get('model_type', None)
    include_providers = config.get('include_providers', True)
    limit = config.get('limit', None)
    allowed_model_ids = config.get('_allowed_model_ids', None)

    # Fetch models from database
    models = model_repo.get_all(
        model_type=model_type,
        include_providers=include_providers,
        limit=limit,
        allowed_model_ids=allowed_model_ids,
    )

    options = []
    for model in models:
        option = {
            'label': _format_model_label(model),
            'value': model.id,
            'description': _get_model_description(model),
            'category': model.get_model_type_display(),
            'size': model.get_file_size_mb() if hasattr(model, 'get_file_size_mb') else None,
            'file_path': model.file_path,
            'filename': model.filename,
            'user_notes': model.description,
            'model_type': model.model_type
        }

        # Add provider info if available
        if model.provider_info:
            option['provider_info'] = {
                'name': model.provider_info.name,
                'description': model.provider_info.description,
                'tags': model.provider_info.tags,
                'nsfw': model.provider_info.nsfw,
                'images': model.provider_info.images,
                'model_id': model.provider_info.model_id,
                'version_id': model.provider_info.version_id
            }

        options.append(option)

    return options


def _format_model_label(model) -> str:
    """
    Format model label for display.

    Uses provider name if available, otherwise formats the filename.

    Args:
        model: Model database object

    Returns:
        Formatted label string
    """
    if model.provider_info and model.provider_info.name:
        return model.provider_info.name
    else:
        # Fallback to formatted filename - properly remove only the file extension
        name = model.filename
        # Remove common model file extensions
        extensions = ['.safetensors', '.ckpt', '.pt', '.pth', '.bin']
        for ext in extensions:
            if name.lower().endswith(ext.lower()):
                name = name[:-len(ext)]
                break

        # Format the name for display
        name = name.replace('_', ' ').replace('-', ' ')
        return ' '.join(word.capitalize() for word in name.split())


def _get_model_description(model) -> str:
    """
    Generate a description for the model.

    Combines provider description (if available) with file size info.

    Args:
        model: Model database object

    Returns:
        Description string
    """
    parts = []

    if model.provider_info and model.provider_info.description:
        # Truncate provider description to first sentence or 100 chars
        desc = model.provider_info.description[:100]
        if len(model.provider_info.description) > 100:
            desc += "..."
        parts.append(desc)

    # Add file size info
    size_mb = None
    if hasattr(model, 'get_file_size_mb'):
        size_mb = model.get_file_size_mb()
    elif model.file_size:
        size_mb = model.file_size / (1024 * 1024)

    if size_mb:
        if size_mb >= 1024:
            size_str = f"{size_mb / 1024:.1f}GB"
        else:
            size_str = f"{size_mb:.1f}MB"
        parts.append(f"• {size_str}")

    return " ".join(parts) if parts else f"{model.model_type.title()} model"


def get_checkbox_options(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get checkbox group options.

    Args:
        config: Field configuration with options key

    Returns:
        List of checkbox option dictionaries
    """
    options = []
    static_options = config.get('options', [])

    for option in static_options:
        options.append({
            'label': option.get('label'),
            'value': option.get('value'),
            'checked': option.get('checked', False)
        })

    return options
