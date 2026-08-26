"""Schema validation for submitted form data (`POST /api/form/validate`)."""
import logging
from typing import Any, Dict

from src.platform.plugins.hooks import execute_hook
from src.features.forms.hooks import FORM_HOOKS

logger = logging.getLogger(__name__)


def validate_form_data(plugin_registry, form_schema: Dict[str, Any], form_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate form data against schema.

    Executes hooks:
    - form.before_validate: Can modify data or add custom validation
    - form.after_validate: Notification of validation results

    Args:
        plugin_registry: Fires the before/after hooks below.
        form_schema: JSON schema-like form definition
        form_data: Data to validate

    Returns:
        Dict with validation result: {"valid": True} or {"valid": False, "errors": [...]}

    Raises:
        ValueError: If validation fails or operation is blocked
    """
    # Execute before hook
    hook_data, blocked = execute_hook(plugin_registry,
        FORM_HOOKS.before_validate,
        {"form_schema": form_schema, "form_data": form_data}
    )
    if blocked:
        reason = hook_data.get("block_reason", "Validation blocked")
        logger.warning(f"Form validation blocked by plugin: {reason}")
        raise ValueError(reason)

    # Use potentially modified data from hook
    form_data = hook_data.get("form_data", form_data)

    errors = []

    # Check required fields
    required_fields = form_schema.get('required', [])
    for field in required_fields:
        if field not in form_data or form_data[field] is None:
            errors.append(f"Field '{field}' is required")

    # Type validation
    properties = form_schema.get('properties', {})
    for field_name, field_schema in properties.items():
        if field_name in form_data:
            value = form_data[field_name]
            field_type = field_schema.get('type')

            if not _validate_field_type(value, field_type):
                errors.append(f"Field '{field_name}' has invalid type")

            # Range validation for numeric fields
            if field_type in ['number', 'integer']:
                minimum = field_schema.get('minimum')
                maximum = field_schema.get('maximum')

                if minimum is not None and value < minimum:
                    errors.append(f"Field '{field_name}' must be at least {minimum}")
                if maximum is not None and value > maximum:
                    errors.append(f"Field '{field_name}' must be at most {maximum}")

    # Execute after hook
    execute_hook(plugin_registry,
        FORM_HOOKS.after_validate,
        {"form_schema": form_schema, "form_data": form_data, "errors": errors, "valid": len(errors) == 0}
    )

    if errors:
        raise ValueError(f"Form validation failed: {'; '.join(errors)}")

    return {"valid": True}


def _validate_field_type(value: Any, expected_type: str) -> bool:
    """
    Validate field type.

    Args:
        value: The value to validate
        expected_type: Expected type string (string, number, integer, boolean, array, object)

    Returns:
        True if value matches expected type, False otherwise
    """
    if expected_type == 'string':
        return isinstance(value, str)
    elif expected_type == 'number':
        return isinstance(value, (int, float))
    elif expected_type == 'integer':
        return isinstance(value, int)
    elif expected_type == 'boolean':
        return isinstance(value, bool)
    elif expected_type == 'array':
        return isinstance(value, list)
    elif expected_type == 'object':
        return isinstance(value, dict)
    else:
        return True  # Unknown type, assume valid
