"""The form fields.

A field type is a pair: an implementation that processes its value in both
directions (frontend to backend on input, backend to frontend on output), and an
entry on a `FieldTypeRegistry` declaring it to the form system. The registry
itself is a plugin extension point and lives with the other ones
(`src.platform.plugins.field_types`); `register_builtin_fields` declares the set
this application ships, exactly the way a plugin declares its own.

Available fields:
- Image: Handle image uploads and processing
- Select: Dropdown selections with various data sources
- Slider: Numeric input with ranges
- CheckboxGroup: Multiple checkbox selections
- Container: Layout containers (tabs, rows, etc.)
"""

from .builtin import register_builtin_fields
from .field_factory import FieldFactory
from .base_field import BaseField
from .image import Image
from .select import Select
from .slider import Slider
from .checkbox_group import CheckboxGroup
from .container import Container

__all__ = [
    'register_builtin_fields',
    'FieldFactory',
    'BaseField',
    'Image',
    'Select',
    'Slider',
    'CheckboxGroup',
    'Container'
]
