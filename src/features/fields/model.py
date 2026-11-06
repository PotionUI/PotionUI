from typing import Dict, Any, Optional, List

from .base_field import BaseField
from .specs import FieldConfigSpec, FieldValidationSpec, FieldExampleSpec
from src.features.presets.configuration import resolve_field_filter_tags, resolve_reactions_filter_tags
from src.features.presets.form_lookups import current_form_lookups
from src.platform.filesystem.model_types import pickable_model_types
from src.features.models.recommendations import (
    RECOMMENDATION_EXTENSIONS,
    recommendation_filenames,
    serialize_recommendation,
)



class Model(BaseField):
    """Model field that fetches models from the database with marketplace provider integration"""

    def __init__(self, preset_loader, template_processor=None):
        super().__init__(preset_loader)
        self.template_processor = template_processor

    def output(self, field, preset_id: str = None) -> Dict[str, Any]:
        """Transform model field data to frontend format"""
        field_info = self.get_field_info(field)
        schema = self.create_base_schema(field_info)
        if schema.get('reactions'):
            schema['reactions'] = resolve_reactions_filter_tags(schema['reactions'], preset_id)

        # Add configuration for model field
        model_types = self._clean_model_types(field_info['configuration'].get('model_types'))
        default_type = model_types[0] if model_types else 'checkpoint'
        schema['configuration'] = {
            'model_type': field_info['configuration'].get('model_type', default_type),
            'placeholder': field_info['configuration'].get('placeholder', 'Select a model...'),
            'searchable': field_info['configuration'].get('searchable', True),
            'allow_info_modal': field_info['configuration'].get('allow_info_modal', True),
            'recommendations': self._serialize_recommendations(field_info['configuration'].get('recommendations')),
            # Resolved tag-id list (or None = no filtering) - see resolve_field_filter_tags.
            'filter_tags': resolve_field_filter_tags(field_info['configuration'].get('filter_tags'), preset_id),
        }
        if field_info['configuration'].get('tasks'):
            schema['configuration']['tasks'] = list(field_info['configuration']['tasks'])
        if model_types:
            schema['configuration']['model_types'] = model_types
        if field_info['configuration'].get('unquantized_only') is True:
            schema['configuration']['unquantized_only'] = True

        # Lets the frontend source options from this preset's engine
        # (`GET /api/presets/{preset_id}/models`) instead of the global library.
        schema['preset_id'] = preset_id

        return schema

    @staticmethod
    def _clean_model_types(raw: Any) -> List[str]:
        if not isinstance(raw, (list, tuple)):
            return []
        cleaned: List[str] = []
        pickable = pickable_model_types()
        for item in raw:
            if item in pickable and item not in cleaned:
                cleaned.append(item)
        return cleaned

    @staticmethod
    def _serialize_recommendations(recommendations: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not recommendations:
            return recommendations

        from src.features.providers.registry import get_provider_registry
        provider_registry = get_provider_registry()

        visible = [
            rec for rec in recommendations
            if not rec.get('provider') or provider_registry.is_provider_initialized(rec['provider'])
        ]
        existing = current_form_lookups().existing_filenames(
            candidate
            for rec in visible
            if rec.get('name')
            for candidate in Model._recommendation_filenames(rec['name'])
        )

        serialized = []
        for rec in visible:
            entry = serialize_recommendation(rec)
            entry['installed'] = bool(
                rec.get('name') and existing.intersection(Model._recommendation_filenames(rec['name']))
            )
            serialized.append(entry)

        return serialized

    @staticmethod
    def _recommendation_filenames(name: str) -> List[str]:
        return recommendation_filenames(name)

    @classmethod
    def configuration(cls) -> List[FieldConfigSpec]:
        """Return specification of configuration parameters this field accepts"""
        return [
            FieldConfigSpec(
                name="model_type",
                param_type=str,
                default="checkpoint",
                description="Type of model to filter by",
                choices=list(pickable_model_types()),
                example="lora"
            ),
            FieldConfigSpec(
                name="model_types",
                param_type=list,
                default=None,
                description=(
                    "Several model types offered in one picker (for example diffusion_model, unet "
                    "and text_encoder). Preset-scoped pickers list the union; the first entry "
                    "stands in for `model_type` when that is not set. Unknown types are dropped."
                ),
                example=["diffusion_model", "unet", "checkpoint", "text_encoder"]
            ),
            FieldConfigSpec(
                name="unquantized_only",
                param_type=bool,
                default=False,
                description=(
                    "List only safetensors files whose header carries no quantized tensors "
                    "(no fp8 dtype, scale or comfy_quant entries). Preset-scoped pickers only."
                ),
                example=True
            ),
            FieldConfigSpec(
                name="placeholder",
                param_type=str,
                default="Select a model...",
                description="Placeholder text for the model selector",
                example="Choose LoRA model..."
            ),
            FieldConfigSpec(
                name="searchable",
                param_type=bool,
                default=True,
                description="Enable search functionality in model selector",
                example=False
            ),
            FieldConfigSpec(
                name="allow_info_modal",
                param_type=bool,
                default=True,
                description="Allow opening model information modal",
                example=False
            ),
            FieldConfigSpec(
                name="recommendations",
                param_type=list,
                default=None,
                description=(
                    "List of recommended models to download. Each item is either provider-less "
                    "(name, link, size?, sha256?, description?) or provider-backed (name, "
                    "provider, ref, size?, description?) - provider-backed entries are dropped "
                    "from the serialized schema if that provider isn't installed/enabled. Every "
                    "serialized entry gains `installed: bool`. See docs/presets.md."
                ),
                example=[
                    {
                        "name": "RealESRGAN 4x+ Anime",
                        "link": "https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x4plus_anime_6B.pth",
                        "size": "17.9 MB",
                        "sha256": "f872d837d3c90ed2e05227bed711af5671a6fd1c9f7d7e91c911a61f155e99da",
                        "description": "Optimized for anime-style images with sharp details and vibrant colors"
                    }
                ]
            ),
            FieldConfigSpec(
                name="filter_tags",
                param_type=list,
                default=None,
                description=(
                    "Restrict this field's model options to models tagged with at least one "
                    "of these admin Tag IDs (OR semantics). Either a literal list of tag IDs, "
                    "or '@config:<key>' to resolve against the preset's stored `configuration:` "
                    "value for <key> (a 'model_tags' entry) at form-schema time. Missing/empty "
                    "resolved value means no filtering."
                ),
                example="@config:checkpoint_tags"
            ),
            FieldConfigSpec(
                name="tasks",
                param_type=list,
                default=None,
                description=(
                    "Cloud models only: keep the models whose provider catalog lists at least one "
                    "of these tasks (for example txt2img or txt2video). Ignored for every other "
                    "model type."
                ),
                example=["txt2img"]
            ),
        ]

    @classmethod
    def validation_rules(cls) -> List[FieldValidationSpec]:
        """Return specification of validation rules this field supports"""
        return [
            FieldValidationSpec(
                rule_name="required",
                description="Whether a model selection is required",
                param_type=bool,
                example=True
            ),
            FieldValidationSpec(
                rule_name="model_exists",
                description="Validate that selected model file exists in the database",
                param_type=bool,
                example=True
            ),
            FieldValidationSpec(
                rule_name="model_type",
                description="Validate model is of the specified type (checkpoint, lora, etc.)",
                param_type=str,
                example="checkpoint"
            ),
        ]

    @classmethod
    def examples(cls) -> List[FieldExampleSpec]:
        """Return example configurations for this field"""
        return [
            FieldExampleSpec(
                title="Checkpoint Model Selector",
                description="Select main generation model from database",
                yaml_config="""type: model
name: model
label: Select Model
configuration:
  model_type: checkpoint
  placeholder: "Choose generation model..."
  searchable: true""",
                rendered_output={
                    "type": "model",
                    "name": "model",
                    "title": "Select Model",
                    "configuration": {
                        "model_type": "checkpoint",
                        "placeholder": "Choose generation model...",
                        "searchable": True,
                        "allow_info_modal": True
                    }
                },
                frontend_preview={
                    "type": "model",
                    "name": "preview_model",
                    "title": "Select Model",
                    "configuration": {
                        "model_type": "checkpoint",
                        "placeholder": "Choose generation model...",
                        "searchable": True
                    }
                }
            ),
            FieldExampleSpec(
                title="LoRA Model Selector",
                description="Select LoRA adapter from database",
                yaml_config="""type: model
name: lora
label: LoRA Model
configuration:
  model_type: lora
  placeholder: "Select LoRA..."
  searchable: true""",
                rendered_output={
                    "type": "model",
                    "name": "lora",
                    "title": "LoRA Model",
                    "configuration": {
                        "model_type": "lora",
                        "placeholder": "Select LoRA...",
                        "searchable": True,
                        "allow_info_modal": True
                    }
                },
                frontend_preview={
                    "type": "model",
                    "name": "preview_lora",
                    "title": "LoRA Model",
                    "configuration": {
                        "model_type": "lora",
                        "placeholder": "Select LoRA..."
                    }
                }
            ),
            FieldExampleSpec(
                title="Simple Upscaler Selector",
                description="Basic upscaler model selection without search",
                yaml_config="""type: model
name: upscaler
label: Upscaler
configuration:
  model_type: upscaler
  searchable: false
  allow_info_modal: false""",
                rendered_output={
                    "type": "model",
                    "name": "upscaler",
                    "title": "Upscaler",
                    "configuration": {
                        "model_type": "upscaler",
                        "placeholder": "Select a model...",
                        "searchable": False,
                        "allow_info_modal": False
                    }
                },
                frontend_preview={
                    "type": "model",
                    "name": "preview_upscaler",
                    "title": "Upscaler",
                    "configuration": {
                        "model_type": "upscaler",
                        "placeholder": "Select upscaler..."
                    }
                }
            ),
            FieldExampleSpec(
                title="Upscaler with Recommendations",
                description="Upscaler selector with recommended models to download",
                yaml_config="""type: model
name: upscaler
label: Upscaler
configuration:
  model_type: upscaler
  placeholder: "Select upscaler model..."
  recommendations:
    - name: "RealESRGAN 4x+ Anime"
      link: "https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x4plus_anime_6B.pth"
      size: "17.9 MB"
      sha256: "f872d837d3c90ed2e05227bed711af5671a6fd1c9f7d7e91c911a61f155e99da"
      description: "Optimized for anime-style images with sharp details and vibrant colors"
    - name: "RealESRGAN 4x+"
      link: "https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x4plus.pth"
      size: "63.9 MB"
      sha256: "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"
      description: "General-purpose upscaler for photorealistic images, works well on a wide variety of content\"""",
                rendered_output={
                    "type": "model",
                    "name": "upscaler",
                    "title": "Upscaler",
                    "configuration": {
                        "model_type": "upscaler",
                        "placeholder": "Select upscaler model...",
                        "searchable": True,
                        "allow_info_modal": True,
                        "recommendations": [
                            {
                                "name": "RealESRGAN 4x+ Anime",
                                "link": "https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x4plus_anime_6B.pth",
                                "size": "17.9 MB",
                                "sha256": "f872d837d3c90ed2e05227bed711af5671a6fd1c9f7d7e91c911a61f155e99da",
                                "description": "Optimized for anime-style images with sharp details and vibrant colors"
                            },
                            {
                                "name": "RealESRGAN 4x+",
                                "link": "https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x4plus.pth",
                                "size": "63.9 MB",
                                "sha256": "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1",
                                "description": "General-purpose upscaler for photorealistic images, works well on a wide variety of content"
                            }
                        ]
                    }
                },
                frontend_preview={
                    "type": "model",
                    "name": "preview_upscaler_rec",
                    "title": "Upscaler",
                    "description": "With download recommendations",
                    "configuration": {
                        "model_type": "upscaler",
                        "placeholder": "Select upscaler model..."
                    }
                }
            ),
        ]
