import os
from pathlib import Path
from typing import Dict, Any, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec


class Markdown(BaseField):
    """Markdown field for rendering markdown content from inline text or external files"""

    def can_handle(self, field_type: str) -> bool:
        return field_type == 'markdown'

    def map_field(self, field, preset_id: str = None) -> Dict[str, Any]:
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)

        # Markdown-specific configuration
        config = field_info['configuration']

        # Get markdown content from various sources
        content = self._get_markdown_content(config, field_info, preset_id)
        schema['content'] = content

        # Set rendering options
        schema['enableHtml'] = config.get('enableHtml', False)
        schema['className'] = config.get('className', '')

        return schema


    @classmethod
    def description(cls) -> str:
        return "Markdown field for rendering markdown documentation from inline content or external files"

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="content",
                param_type=str,
                default="",
                description="Inline markdown content to render",
                example="# Hello World\n\nThis is **bold** text."
            ),
            FieldConfigSpec(
                name="file",
                param_type=str,
                default="",
                description="Path to markdown file to load and render",
                example="{{paths.preset}}/docs/readme.md"
            ),
            FieldConfigSpec(
                name="enableHtml",
                param_type=bool,
                default=False,
                description="Whether to allow HTML tags in markdown",
                example=True
            ),
            FieldConfigSpec(
                name="className",
                param_type=str,
                default="",
                description="CSS class name to apply to the markdown container",
                example="prose prose-lg"
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return empty list - Markdown is a display-only field with no validation"""
        return []

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Inline Markdown Content",
                description="Simple markdown content defined directly in the configuration",
                yaml_config="""type: markdown
name: inline_docs
label: Documentation
configuration:
  content: |
    # Getting Started

    This preset allows you to generate **high-quality** images using:

    - Advanced sampling methods
    - Custom LoRA models
    - Precise control over generation parameters

    > **Tip**: Start with the default settings and adjust as needed.""",
                rendered_output={
                    "type": "markdown",
                    "name": "inline_docs",
                    "title": "Documentation",
                    "content": "# Getting Started\n\nThis preset allows you to generate **high-quality** images using:\n\n- Advanced sampling methods\n- Custom LoRA models\n- Precise control over generation parameters\n\n> **Tip**: Start with the default settings and adjust as needed.",
                    "enableHtml": False,
                    "className": ""
                },
                frontend_preview={
                    "type": "markdown",
                    "name": "preview_markdown",
                    "title": "Documentation",
                    "content": "# Getting Started\n\nThis preset allows you to generate **high-quality** images."
                }
            ),
            FieldExampleSpec(
                title="External Markdown File",
                description="Load markdown content from an external file",
                yaml_config="""type: markdown
name: external_docs
label: Model Information
configuration:
  file: "{{paths.preset}}/docs/model_info.md"
  enableHtml: true
  className: "prose prose-sm"=""",
                rendered_output={
                    "type": "markdown",
                    "name": "external_docs",
                    "title": "Model Information",
                    "content": "",  # Loaded from file at runtime
                    "enableHtml": True,
                    "className": "prose prose-sm"
                },
                frontend_preview={
                    "type": "markdown",
                    "name": "preview_external_docs",
                    "title": "Model Information",
                    "content": "*Content loaded from external file*"
                }
            ),
            FieldExampleSpec(
                title="Usage Instructions",
                description="Markdown field with styled content for usage instructions",
                yaml_config="""type: markdown
name: usage_guide
label: Usage Guide
configuration:
  content: |
    ## How to Use This Preset

    ### Step 1: Configure Basic Settings

    1. Select your desired **resolution** from the dropdown
    2. Choose an appropriate **sampling method**
    3. Set the **number of steps** (20-50 recommended)

    ### Step 2: Customize Your Prompt

    - Use descriptive language
    - Add style keywords like `photorealistic`, `artistic`, `detailed`
    - Consider adding quality boosters: `high quality`, `masterpiece`

    ### Tips for Better Results

    💡 **Pro Tip**: Higher CFG values (7-12) give more prompt adherence

    ⚠️ **Warning**: Very high step counts may not improve quality
  className: "prose prose-blue max-w-none"=""",
                rendered_output={
                    "type": "markdown",
                    "name": "usage_guide",
                    "title": "Usage Guide",
                    "content": "## How to Use This Preset\n\n### Step 1: Configure Basic Settings\n\n1. Select your desired **resolution** from the dropdown\n2. Choose an appropriate **sampling method**\n3. Set the **number of steps** (20-50 recommended)\n\n### Step 2: Customize Your Prompt\n\n- Use descriptive language\n- Add style keywords like `photorealistic`, `artistic`, `detailed`\n- Consider adding quality boosters: `high quality`, `masterpiece`\n\n### Tips for Better Results\n\n💡 **Pro Tip**: Higher CFG values (7-12) give more prompt adherence\n\n⚠️ **Warning**: Very high step counts may not improve quality",
                    "enableHtml": False,
                    "className": "prose prose-blue max-w-none"
                },
                frontend_preview={
                    "type": "markdown",
                    "name": "preview_usage_guide",
                    "title": "Usage Guide",
                    "content": "## How to Use This Preset\n\n1. Select your **resolution**\n2. Choose a **sampling method**"
                }
            ),
        ]

    def _get_markdown_content(self, config: Dict[str, Any], field_info: Dict[str, Any], preset_id: str = None) -> str:
        """Get markdown content from various sources"""

        # Priority 1: Inline content
        if 'content' in config and config['content']:
            return config['content']

        # Priority 2: External file
        if 'file' in config and config['file'] and preset_id:
            file_content = self._load_markdown_file(config['file'], preset_id)
            if file_content:
                return file_content

        # Priority 3: Field description as fallback
        if field_info['description']:
            return field_info['description']

        # Default empty content
        return ""

    def _load_markdown_file(self, file_path: str, preset_id: str) -> str:
        """Load markdown content from an external file"""
        try:
            found_preset = self._find_preset_by_id(preset_id)
            if not found_preset:
                return ""

            # Create context for template processing
            context = {
                'paths': {
                    'preset': found_preset.path,
                }
            }

            # Process the template to get the actual file path
            # For now, do basic template replacement
            resolved_path = file_path.replace('{{paths.preset}}', context['paths']['preset'])

            file_path_obj = Path(resolved_path)
            if file_path_obj.exists():
                with open(file_path_obj, 'r', encoding='utf-8') as f:
                    return f.read()
        except Exception as e:
            print(f"Error loading markdown file {file_path}: {e}")

        return ""