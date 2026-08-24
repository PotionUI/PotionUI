from typing import Dict, Any, List, Optional
from pydantic import BaseModel


class FormFieldOption(BaseModel):
    """A single option for select/checkbox fields."""
    label: str
    value: str
    example: Optional[Dict[str, Any]] = None


class FormFieldSchema(BaseModel):
    """Schema definition for a form field."""
    name: str
    type: str
    label: str
    description: str = ""
    required: bool = False
    default: Any = None
    options: List[FormFieldOption] = []
    configuration: Dict[str, Any] = {}


class FormFieldOptionsRequest(BaseModel):
    """Request body for getting field options."""
    field_type: str
    field_config: Dict[str, Any] = {}


class FormValidationRequest(BaseModel):
    """Request body for form validation."""
    form_schema: Dict[str, Any]
    form_data: Dict[str, Any]


class FormDefaultsRequest(BaseModel):
    """Request body for getting form defaults."""
    preset_id: str
