from typing import Dict, Any, List, Optional
from pathlib import Path
import yaml

from src.features.presets import PresetTemplateLoader
from src.platform.templating import TemplateProcessor
from src.features.fields.field_factory import FieldFactory
from src.features.presets.templates import FieldTemplate
from src.platform.observability.logger import logger

class PresetFormSerializer:
    """
    Serializer class for processing preset form configurations and converting them to JSON schema.
    This class handles the transformation of form fields, options, and other form-related data.
    """

    def __init__(self, preset_loader: PresetTemplateLoader, template_processor: TemplateProcessor = None):
        self.preset_loader = preset_loader
        self.template_processor = template_processor
        self.field_factory = FieldFactory(preset_loader, template_processor)

    def process_form_fields(
        self,
        form_config,
        preset_id: str,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Process form configuration to create JSON schema.

        `overrides` (admin per-field form overrides for the relevant
        mode, `{field_name: {default?, editable?, visible?}}`) is applied
        right after `@loop`/external-`children` resolution below - the single
        funnel every rendered field passes through, so expanded field names
        match and hidden/locked fields never reach `_process_field_recursive`
        (which builds `properties`/`required`). See
        src/features/presets/form_overrides.py.
        """
        # If form_config is None or doesn't have fields, return empty schema
        if not form_config or not hasattr(form_config, 'fields'):
            return {
                'type': 'object',
                'properties': {},
                'required': []
            }

        # Get fields from form_config
        fields = form_config.fields

        # Resolve external children files before processing
        preset_template = self.preset_loader.load_preset_by_id(preset_id)
        if preset_template:
            # Create context for template processing
            context = {
                'paths': {
                    'preset': preset_template.path
                }
            }
            fields = self._resolve_external_children(fields, context)

            if overrides:
                from src.features.presets.form_overrides import apply_overrides_to_fields
                fields = apply_overrides_to_fields(fields, overrides)

        # Create schema
        schema = {
            'type': 'object',
            'properties': {},
            'required': []
        }

        # Process all fields recursively
        for field in fields:
            self._process_field_recursive(field, schema, preset_id)

        return schema

    def _process_loop_inline(self, loop_config: dict, context: Dict[str, Any]) -> List:
        """Process loop configuration inline without creating PresetProcessor"""
        # Extract loop configuration
        count = loop_config.get('count')
        items = loop_config.get('items')
        template = loop_config.get('template')
        when = loop_config.get('when')
        as_var = loop_config.get('as')

        # Process count/items if they contain templates
        if count and isinstance(count, str) and ("{{" in count or "{%" in count):
            count = self.template_processor.process_template(count, context)
            try:
                count = int(count)
            except (ValueError, TypeError):
                raise ValueError(f"Loop count must be an integer, got: {count}")

        if items and isinstance(items, str) and ("{{" in items or "{%" in items):
            items = self.template_processor.process_template(items, context)

        # Determine loop source
        if count is not None:
            loop_items = list(range(1, count + 1))
        elif items is not None:
            if isinstance(items, dict):
                loop_items = list(items.items())
            else:
                loop_items = list(items) if hasattr(items, '__iter__') else [items]
        else:
            raise ValueError("@loop requires either 'count' or 'items' parameter")

        # Process each iteration
        results = []
        for index, item in enumerate(loop_items):
            loop_context = context.copy()

            # Add loop variables
            loop_vars = {
                'index': index + 1,
                'index0': index,
                'first': index == 0,
                'last': index == len(loop_items) - 1,
                'length': len(loop_items)
            }
            loop_context['loop'] = loop_vars

            # Handle item variable
            if items is not None:
                if as_var and isinstance(item, tuple) and len(item) == 2:
                    if ',' in as_var:
                        key_var, val_var = [v.strip() for v in as_var.split(',', 1)]
                        loop_context[key_var] = item[0]
                        loop_context[val_var] = item[1]
                    else:
                        loop_context[as_var] = item
                else:
                    loop_context['item'] = item

            # Check 'when' condition if specified
            if when:
                when_result = self._process_value_inline(when, loop_context)
                if isinstance(when_result, str) and when_result.lower() in ['false', '']:
                    continue
                elif not when_result:
                    continue

            # Process template
            result = self._process_value_inline(template, loop_context)
            results.append(result)

        return results

    def _process_value_inline(self, value, context):
        """Process a value that might contain template strings"""
        if isinstance(value, str):
            if "{{" in value or "{%" in value:
                return self.template_processor.process_template(value, context)
            return value
        elif isinstance(value, dict):
            return {k: self._process_value_inline(v, context) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._process_value_inline(item, context) for item in value]
        else:
            return value

    def _convert_dict_to_field_template(self, item: dict, context: Dict[str, Any]) -> FieldTemplate:
        """Convert a dictionary to FieldTemplate, recursively processing children"""
        if 'children' in item and isinstance(item['children'], list):
            item = item.copy()
            children = []
            for child in item['children']:
                if isinstance(child, dict):
                    children.append(self._convert_dict_to_field_template(child, context))
                elif isinstance(child, FieldTemplate):
                    children.append(child)
            item['children'] = children

        return FieldTemplate(**item)

    def _expand_loop_fields(self, fields: List, context: Dict[str, Any]) -> List[FieldTemplate]:
        """Expand @loop fields into concrete field definitions"""
        expanded_fields = []

        for field in fields:
            # Handle both FieldTemplate objects and dictionaries
            if hasattr(field, 'type'):
                field_type = field.type
                field_config = field.configuration if hasattr(field, 'configuration') else None
            else:
                field_type = field.get('type')
                field_config = field.get('configuration')

            # Check if this is a loop field
            if field_type == "@loop":
                # Extract loop configuration
                if not field_config:
                    raise ValueError("@loop field requires configuration with count/items and template")

                loop_config = {
                    'count': field_config.get('count'),
                    'items': field_config.get('items'),
                    'template': field_config.get('template'),
                    'when': field_config.get('when'),
                    'as': field_config.get('as')
                }

                # Process the loop inline
                expanded_items = self._process_loop_inline(loop_config, context)

                # Convert expanded items to FieldTemplate objects
                for item in expanded_items:
                    if isinstance(item, dict):
                        try:
                            expanded_field = self._convert_dict_to_field_template(item, context)
                            # Recursively expand loops in children
                            if expanded_field.children and isinstance(expanded_field.children, list):
                                expanded_field_dict = expanded_field.__dict__.copy()
                                expanded_field_dict['children'] = self._expand_loop_fields(expanded_field.children, context)
                                expanded_field = FieldTemplate(**expanded_field_dict)
                            expanded_fields.append(expanded_field)
                        except Exception as e:
                            logger.warning(f"Error creating FieldTemplate from loop expansion: {e}")
                    elif isinstance(item, list):
                        for sub_item in item:
                            if isinstance(sub_item, dict):
                                try:
                                    expanded_field = self._convert_dict_to_field_template(sub_item, context)
                                    # Recursively expand loops in children
                                    if expanded_field.children and isinstance(expanded_field.children, list):
                                        expanded_field_dict = expanded_field.__dict__.copy()
                                        expanded_field_dict['children'] = self._expand_loop_fields(expanded_field.children, context)
                                        expanded_field = FieldTemplate(**expanded_field_dict)
                                    expanded_fields.append(expanded_field)
                                except Exception as e:
                                    logger.warning(f"Error creating FieldTemplate from loop expansion: {e}")
            else:
                # Not a loop field, recursively process children if they exist
                if hasattr(field, 'children') and field.children and isinstance(field.children, list):
                    field_dict = field.__dict__.copy() if hasattr(field, '__dict__') else field.copy()
                    field_dict['children'] = self._expand_loop_fields(field.children, context)
                    field = FieldTemplate(**field_dict)
                    expanded_fields.append(field)
                elif isinstance(field, dict) and 'children' in field and isinstance(field['children'], list):
                    field_dict = field.copy()
                    field_dict['children'] = self._expand_loop_fields(field['children'], context)
                    expanded_fields.append(FieldTemplate(**field_dict))
                else:
                    # Convert to FieldTemplate if needed
                    if isinstance(field, dict):
                        expanded_fields.append(FieldTemplate(**field))
                    else:
                        expanded_fields.append(field)

        return expanded_fields

    def _resolve_external_children(self, fields: List, context: Dict[str, Any]) -> List[FieldTemplate]:
        """Resolve external children files for form fields and expand loops"""
        # First, expand any @loop fields
        fields = self._expand_loop_fields(fields, context)

        resolved_fields = []

        for field in fields:
            # Handle both FieldTemplate objects and dictionaries
            if hasattr(field, '__dict__'):
                # FieldTemplate object
                field_dict = field.__dict__.copy()
                children = field.children
            else:
                # Dictionary
                field_dict = field.copy()
                children = field_dict.get('children')

            # Check if children is a string (template path to external file)
            if children and isinstance(children, str):
                # Resolve the template path
                resolved_path = self.template_processor.process_template(children, context)

                # Load external children
                external_children = self._load_external_children_file(resolved_path)
                field_dict['children'] = self._resolve_external_children(external_children, context) if external_children else []
            elif children and isinstance(children, list):
                # Recursively process nested children
                field_dict['children'] = self._resolve_external_children(children, context)

            # Create new FieldTemplate with resolved children
            resolved_field = FieldTemplate(**field_dict)
            resolved_fields.append(resolved_field)

        return resolved_fields

    def _load_external_children_file(self, file_path: str) -> List[FieldTemplate]:
        """Load field children from external YAML file"""
        children_file = Path(file_path)

        if not children_file.exists():
            logger.warning(f"External children file not found: {children_file}")
            return []

        try:
            with open(children_file, 'r') as f:
                children_data = yaml.load(f, Loader=yaml.FullLoader)

            # Convert to FieldTemplate objects
            fields = []
            for field_data in children_data.get('fields', []):
                try:
                    fields.append(FieldTemplate(**field_data))
                except Exception as e:
                    logger.warning(f"Error parsing external field: {e}")

            return fields
        except Exception as e:
            logger.error(f"Error loading external children file {children_file}: {e}")
            return []

    def _process_field_recursive(self, field, schema_obj: Dict[str, Any], preset_id: str = None):
        """Process a field and its children recursively"""
        # Handle both FieldTemplate objects and dictionaries
        if hasattr(field, 'type'):
            field_type = field.type
            field_name = field.name
            required = field.required
        else:
            field_type = field.get('type')
            field_name = field.get('name')
            required = field.get('required', False)

        # For tabs field, use 'tabs' as the name if it doesn't have one
        if field_type == 'tabs' and not field_name:
            field_name = 'tabs'

        # Process all fields, including container fields
        if field_name:
            # Process the field using the field factory
            schema_obj['properties'][field_name] = self.field_factory.map_field(field, preset_id)
            if required:
                schema_obj['required'].append(field_name)


    def get_json_schema_type(self, field_type: str) -> str:
        """Convert field type to JSON schema type"""
        type_mapping = {
            'textbox': 'string',
            'select': 'string',
            'slider': 'number',
            'number': 'number',
            'stepper': 'number',
            'checkbox_group': 'array',
            'button': 'null',
        }
        return type_mapping.get(field_type, 'string')