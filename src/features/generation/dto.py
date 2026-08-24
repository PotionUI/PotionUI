from typing import Dict, Any, Optional, List
from pydantic import BaseModel, validator

from src.features.generation.validators import validate_rating_policy


class PromptPair(BaseModel):
    """A pair of positive and negative prompts."""
    positive: str = ""
    negative: str = ""


class SegmentPhrasebookInput(BaseModel):
    """A phrasebook value that fed a prompt segment."""
    phrasebook_value_id: Optional[str] = None
    category_path: Optional[str] = None
    value: Optional[str] = None


class SegmentInput(BaseModel):
    """A resolved prompt segment (chip/timeline piece) making up a generation's prompt."""
    channel: str = "positive"  # "positive" | "negative"
    prompt_index: int = 0  # multi-prompt tab index
    segment_index: int = 0  # order within channel
    segment_type: str = "content"  # "content" | "break"
    text: str = ""  # resolved plain text (chip values already resolved)
    is_disabled: bool = False
    name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None
    phrasebooks: Optional[List[SegmentPhrasebookInput]] = None


class GenerationRequest(BaseModel):
    preset_id: str
    prompt: Optional[str] = ""  # Legacy single prompt (deprecated, use prompts array)
    negative_prompt: Optional[str] = ""  # Legacy single negative (deprecated)
    prompts: Optional[List[PromptPair]] = None  # Array of prompt pairs
    prompt_state: Optional[Dict[str, Any]] = None  # Full prompt UI state (segments/chips/timeline) for "Reuse"
    segments: Optional[List[SegmentInput]] = None  # Ordered resolved prompt segments for this generation
    # Prompt variables, name -> value template. Referenced from a prompt as `${name}`
    # and bound at expansion time; a value may itself be a template (e.g. "{a|b}").
    variables: Optional[Dict[str, str]] = None
    mode: Optional[str] = "txt2img"  # Generation mode (txt2img, img2img, etc.)
    # Which form "variant" within the mode this submission came from (see
    # docs/presets.md "Variants"). Optional: when omitted, the mode's default
    # variant is used (same resolution rule as GET .../form's form_name).
    form_name: Optional[str] = None
    form_data: Dict[str, Any] = {}
    backend_id: Optional[str] = None  # Specific backend to use for generation
    # Library prompt (src.features.prompt_database) this submission came from, if
    # any - carried through to Generation.source_prompt_id for "used in
    # generations" provenance. Not validated against the prompt table here; a
    # stale or unknown id is stored as-is and just resolves to nothing later.
    source_prompt_id: Optional[str] = None
    tag_ids: Optional[List[str]] = None  # Auto-tags to apply after generation is created
    collection_ids: Optional[List[str]] = None  # Collections to add the generation to on creation
    # Client-minted id of the tab that queued this. Routes queued results back to
    # the originating tab and scopes "clear this tab's queue". Optional: an API
    # client that has no tabs simply omits it.
    tab_id: Optional[str] = None

    @validator('prompts', pre=True, always=True)
    def normalize_prompts(cls, v, values):
        """Normalize prompts to always be an array of PromptPair."""
        if v is not None:
            # Convert dicts to PromptPair if needed
            return [PromptPair(**p) if isinstance(p, dict) else p for p in v]
        # Convert legacy format to array
        prompt = values.get('prompt', '') or ''
        negative_prompt = values.get('negative_prompt', '') or ''
        return [PromptPair(positive=prompt, negative=negative_prompt)]


class ClearTabQueueRequest(BaseModel):
    tab_id: str


class GenerationStatus(BaseModel):
    id: str
    status: str  # "pending", "running", "completed", "failed", "cancelled"
    user_id: Optional[str] = None
    preset_id: Optional[str] = None
    progress: Optional[float] = None
    current_step: Optional[str] = None
    total_steps: Optional[int] = None
    current_step_num: Optional[int] = None
    message: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class GenerationResult(BaseModel):
    id: str
    status: str
    images: List[str] = []
    metadata: Dict[str, Any] = {}
    error: Optional[str] = None


class UpdateTagsRequest(BaseModel):
    """Request to update tags on a generation."""
    tag_ids: List[str]


class BulkDeleteRequest(BaseModel):
    """Request to delete multiple generations."""
    generation_ids: List[str]


class BulkDeleteByTagsRequest(BaseModel):
    """Request to delete all generations matching ALL specified tags."""
    tag_ids: List[str]


class UploadGenerationRequest(BaseModel):
    """Request to upload generations with optional tags."""
    tag_ids: Optional[List[str]] = []


class RatingRequest(BaseModel):
    """Request to set a generation's star rating (0-5, 0 = unrated)."""
    rating: int

    @validator('rating')
    def validate_rating(cls, v):
        return validate_rating_policy(v)


class FavoriteRequest(BaseModel):
    """Request to set a generation's favorite flag."""
    is_favorite: bool


class ExportRequest(BaseModel):
    """Request to export multiple generations as a zip archive."""
    generation_ids: List[str]
    strip_metadata: bool = False
