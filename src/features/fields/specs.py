"""Self-description types for form field types.

Every `BaseField` subclass declares what it accepts (`FieldConfigSpec`), what it
validates (`FieldValidationSpec`) and how it is used (`FieldExampleSpec`). The
field-type documentation endpoint (and any workflow-to-form tooling, in or out
of tree) renders from these declarations, so a field type documents itself
instead of being described in a table somewhere else.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class FieldConfigSpec:
    """Specification for a field configuration parameter"""
    name: str           # Parameter name (e.g., "options", "file", "min", "max")
    param_type: type    # Parameter type (int, float, str, bool, dict, list, etc.)
    default: Any        # Default value
    description: str = ""  # Optional description
    required: bool = False  # Whether this parameter is required
    choices: Optional[List[Any]] = None  # Optional list of valid choices
    example: Optional[Any] = None  # Example value or configuration


@dataclass
class FieldValidationSpec:
    """Specification for a field validation rule"""
    rule_name: str      # Validation rule name (e.g., "required", "min_length", "pattern")
    description: str    # What this validation rule does
    param_type: Optional[type] = None  # Parameter type if the rule takes a parameter
    example: Optional[Any] = None  # Example usage of this validation rule


@dataclass
class FieldExampleSpec:
    """Example configuration for a field"""
    title: str          # Example title
    description: str    # What this example demonstrates
    yaml_config: str    # YAML configuration example
    rendered_output: Optional[Dict[str, Any]] = None  # What it looks like when rendered
    frontend_preview: Optional[Dict[str, Any]] = None  # Field schema for live preview rendering
