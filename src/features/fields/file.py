from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class File(BaseField):
    """File field for file uploads"""
    
    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform file field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)
        
        # File fields are string type (file paths)
        schema['type'] = 'file'
        
        # Add file configuration if present
        configuration = field_info['configuration']
        if 'accept' in configuration:
            schema['accept'] = configuration['accept']
        if 'multiple' in configuration:
            schema['multiple'] = configuration['multiple']
        if 'max_size' in configuration:
            schema['max_size'] = configuration['max_size']
        
        return schema
    
    
    def can_handle(self, field_type: str) -> bool:
        return field_type == 'file'
    
    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        return self.output(field, preset_id)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="accept",
                param_type=str,
                default="*/*",
                description="MIME types or file extensions to accept",
                example=".pdf,.doc,.docx"
            ),
            FieldConfigSpec(
                name="multiple",
                param_type=bool,
                default=False,
                description="Allow multiple file selection",
                example=True
            ),
            FieldConfigSpec(
                name="max_size",
                param_type=int,
                default=52428800,
                description="Maximum file size in bytes (legacy, prefer validation rules)",
                example=10485760
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        base_rules = super().validation_rules()
        file_rules = [
            FieldValidationSpec(
                rule_name="max_size",
                description="Maximum file size in bytes",
                param_type=int,
                example=52428800  # 50MB
            ),
            FieldValidationSpec(
                rule_name="allowed_types",
                description="List of allowed file extensions or MIME types",
                param_type=list,
                example=[".pdf", ".doc", ".txt"]
            ),
        ]
        return base_rules + file_rules

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Document Upload",
                description="Single document file upload",
                yaml_config="""type: file
name: document
label: Upload Document
configuration:
  accept: ".pdf,.doc,.docx"
validation:
  max_size: 10485760  # 10MB
  allowed_types: [".pdf", ".doc", ".docx"]""",
                rendered_output={
                    "type": "file",
                    "name": "document",
                    "title": "Upload Document",
                    "accept": ".pdf,.doc,.docx",
                    "multiple": False
                },
                frontend_preview={
                    "type": "file",
                    "name": "preview_document",
                    "title": "Upload Document",
                    "accept": ".pdf,.doc,.docx",
                    "multiple": False
                }
            ),
            FieldExampleSpec(
                title="Multiple Files Upload",
                description="Allow uploading multiple files",
                yaml_config="""type: file
name: attachments
label: Attachments
configuration:
  accept: "*/*"
  multiple: true
validation:
  max_size: 52428800  # 50MB per file""",
                rendered_output={
                    "type": "file",
                    "name": "attachments",
                    "title": "Attachments",
                    "accept": "*/*",
                    "multiple": True
                },
                frontend_preview={
                    "type": "file",
                    "name": "preview_attachments",
                    "title": "Attachments",
                    "accept": "*/*",
                    "multiple": True
                }
            ),
        ]