from typing import Dict, List, Any, Optional
from pydantic import BaseModel


class PresetStyle(BaseModel):
    """One curated style from a preset's `styles.yml` (see docs/presets.md
    "Styles"). Mirrors `PresetStyle` in schema.py; `preview` is the
    preset-relative asset path (e.g. "public/styles/<id>.webp"), served the
    same way as `media.cover`/`media.gallery` - the frontend composes the URL."""

    id: str
    name: str
    category: str
    description: Optional[str] = None
    prepend: str
    append: str
    negative: Optional[str] = None
    example_prompt: str
    preview: Optional[str] = None


class PresetInfo(BaseModel):
    id: str
    name: str
    version: str
    description: Optional[str] = None
    tags: List[str] = []
    category: Optional[str] = None
    source: Optional[str] = None
    engine: Optional[str] = None
    media: Optional[dict] = None
    # Curated styles from `styles.yml` (docs/presets/manifest.md "Styles"); [] when the
    # preset ships none. See `file_repository.preset_to_info`'s
    # `include_styles` for when the list endpoint vs. detail endpoint fills this.
    styles: List[PresetStyle] = []
    # Preset-declared `vars:` (preset.yml), the raw dict a pipeline's Jinja
    # context resolves `preset.vars` against. See docs/presets.md.
    vars: Optional[Dict[str, Any]] = None
    # Preset/family-level prompting guide + chat-workspace context knobs, e.g.
    # {"guide": "...", "context": {...}, "modes": {...}}. Mirrors
    # PresetLLMSpec in schema.py. See docs/presets.md "LLM context".
    llm: Optional[Dict[str, Any]] = None
    # Optional hardware guidance, e.g. {"min_vram_gb": 12, "recommended_vram_gb": 16}.
    # See docs/presets.md "Hardware requirements".
    requires: Optional[dict] = None
    # Last-evaluated requirements summary, e.g.
    # {"ok": 3, "missing": 1, "unknown": 0, "optional_missing": 1} - a missing
    # entry marked `optional: true` counts under `optional_missing`, not
    # `missing`. `None` until GET /api/presets/{id}/requirements has run at
    # least once for this preset+backend - never computed by the list/detail
    # endpoints themselves. See docs/presets.md "Requirements".
    requirements_summary: Optional[dict] = None


class PresetFormSchema(BaseModel):
    preset_id: str
    form_schema: Dict[str, Any]
