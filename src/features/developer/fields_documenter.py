"""Fields documentation generator."""
from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class FieldsDocumenter:
    """Generates documentation for all available field types."""

    def __init__(self, field_factory):
        """Initialize with field factory.

        Args:
            field_factory: FieldFactory instance for accessing fields
        """
        self.field_factory = field_factory

    def _normalize_field_type(self, class_name: str) -> str:
        """Convert class name to field type string.

        Args:
            class_name: The field class name

        Returns:
            Normalized field type string
        """
        field_type = class_name.lower()
        type_mappings = {
            'defaultfield': 'default',
            'checkboxgroup': 'checkbox_group'
        }
        return type_mappings.get(field_type, field_type)

    def _get_data_sources(self, field_type: str) -> List[str]:
        """Get data sources for a field type.

        Args:
            field_type: The normalized field type

        Returns:
            List of supported data sources
        """
        if field_type == 'select':
            return ['static', 'file', 'filesystem']
        elif field_type == 'model':
            return ['database']
        return ['static']

    def _get_can_handle(self, field_type: str, field_impl) -> List[str]:
        """Get types this field implementation can handle.

        Args:
            field_type: The normalized field type
            field_impl: The field implementation instance

        Returns:
            List of field types this implementation handles
        """
        if not hasattr(field_impl, 'can_handle'):
            return []

        if field_type == 'container':
            return ['container', 'accordion', 'tabs']
        elif field_type == 'default':
            return ['*']  # Handles all unmatched types
        return [field_type]

    def _document_field(self, field_impl) -> Dict[str, Any]:
        """Document a single field implementation.

        Args:
            field_impl: Field implementation instance

        Returns:
            Dict with field documentation
        """
        field_class = field_impl.__class__
        field_type = self._normalize_field_type(field_class.__name__)

        field_info = {
            'type': field_type,
            'class_name': field_class.__name__,
            'description': field_class.description() if hasattr(field_class, 'description') else field_class.__doc__ or "No description available",
        }

        # Get configuration specification
        if hasattr(field_class, 'configuration'):
            field_info['configuration'] = [
                {
                    'name': config.name,
                    'param_type': config.param_type.__name__,
                    'default': config.default,
                    'description': config.description,
                    'required': config.required,
                    'choices': config.choices,
                    'example': config.example
                }
                for config in field_class.configuration()
            ]
        else:
            field_info['configuration'] = []

        # Get validation rules
        if hasattr(field_class, 'validation_rules'):
            field_info['validation_rules'] = [
                {
                    'rule_name': rule.rule_name,
                    'description': rule.description,
                    'param_type': rule.param_type.__name__ if rule.param_type else None,
                    'example': rule.example
                }
                for rule in field_class.validation_rules()
            ]
        else:
            field_info['validation_rules'] = []

        # Get examples
        if hasattr(field_class, 'examples'):
            field_info['examples'] = [
                {
                    'title': example.title,
                    'description': example.description,
                    'yaml_config': example.yaml_config,
                    'rendered_output': example.rendered_output,
                    'frontend_preview': example.frontend_preview
                }
                for example in field_class.examples()
            ]
        else:
            field_info['examples'] = []

        field_info['data_sources'] = self._get_data_sources(field_type)
        field_info['can_handle'] = self._get_can_handle(field_type, field_impl)

        return field_info

    def generate_documentation(self) -> Dict[str, Any]:
        """Generate documentation for all available field types.

        Returns:
            Dict with 'fields' list and 'total' count
        """
        fields_docs = []

        for field_impl in self.field_factory.fields:
            try:
                field_info = self._document_field(field_impl)
                fields_docs.append(field_info)
            except Exception as e:
                logger.warning(f"Error documenting field {field_impl.__class__.__name__}: {e}")
                fields_docs.append({
                    'type': 'unknown',
                    'class_name': field_impl.__class__.__name__,
                    'description': f"Error documenting field: {str(e)}",
                    'configuration': [],
                    'validation_rules': [],
                    'examples': [],
                    'data_sources': [],
                    'can_handle': []
                })

        # Sort fields by type name for better organization
        fields_docs.sort(key=lambda x: x['type'])

        return {
            'fields': fields_docs,
            'total': len(fields_docs)
        }
