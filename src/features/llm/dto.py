"""LLM Data Transfer Objects for API requests and responses."""

import math
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, model_validator

# provider_options keys shared by the native and Ollama sampling controls
# (same name, same meaning on both providers) plus native's own
# repetition_penalty (Ollama's equivalent, "repeat_penalty", is a distinct
# key left unvalidated — legacy free-form).
_PROBABILITY_OPTION_KEYS = ("top_p", "min_p")

# Named thinking-effort levels Ollama accepts for models that support graded
# reasoning (e.g. gpt-oss) alongside a plain boolean. Which of these — or
# whether disabling thinking at all — a given model actually honours is up to
# the model, not this layer.
_OLLAMA_THINK_LEVELS = ("low", "medium", "high")


# ============================================================================
# LLM Configuration DTOs
# ============================================================================

class LLMConfigRequest(BaseModel):
    """Request model for creating/updating LLM configuration."""
    id: Optional[str] = None
    name: str
    type: str  # "ollama" or "openai"
    enabled: bool
    base_url: str
    api_key: Optional[str] = None
    model: str
    system_message: str
    temperature: float = 0.7
    max_tokens: int = 1000
    timeout: int = 30
    supports_vision: bool = False
    disable_system_prompt: bool = False
    memory_reflection: bool = True
    provider_options: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate_native_thinking(self) -> "LLMConfigRequest":
        """``provider_options.thinking`` (the native provider's explicit
        thinking-mode toggle — see ``NativeLLMClient._chat_template_kwargs``)
        accepts only ``null``/omitted, ``true``, or ``false``. Checked only
        for ``type == "native"``: every other provider leaves
        ``provider_options`` as an unvalidated free-form dict, and this key
        is meaningless to them."""
        if self.type != "native" or not self.provider_options:
            return self
        if "thinking" not in self.provider_options:
            return self
        value = self.provider_options["thinking"]
        if value is not None and not isinstance(value, bool):
            raise ValueError(
                "provider_options.thinking must be true, false, or omitted (null) "
                f"for a 'native' config, got {value!r}"
            )
        return self

    @model_validator(mode="after")
    def _validate_ollama_think(self) -> "LLMConfigRequest":
        """``provider_options.think`` (the Ollama root-request thinking
        toggle — see ``build_ollama_chat_request``) accepts ``null``/omitted
        (automatic), a real ``bool``, or one of the named effort levels
        ``_OLLAMA_THINK_LEVELS`` that some models support instead of a plain
        on/off. No truthiness coercion: a stray ``1``/``"true"`` is rejected
        rather than silently accepted as ``True``. Checked only for
        ``type == "ollama"``."""
        if self.type != "ollama" or not self.provider_options:
            return self
        if "think" not in self.provider_options:
            return self
        value = self.provider_options["think"]
        if value is None or isinstance(value, bool):
            return self
        if isinstance(value, str) and value in _OLLAMA_THINK_LEVELS:
            return self
        raise ValueError(
            "provider_options.think must be true, false, one of "
            f"{_OLLAMA_THINK_LEVELS!r}, or omitted (null) for an 'ollama' config, got {value!r}"
        )

    @model_validator(mode="after")
    def _validate_sampling_options(self) -> "LLMConfigRequest":
        """``top_k``/``top_p``/``min_p``/``repetition_penalty`` are shared
        sampling knobs (native reads them directly; Ollama copies ``top_k``/
        ``top_p``/``min_p`` verbatim into its request options — see
        ``OLLAMA_OPTION_KEYS``). Malformed values fail loudly here rather than
        being silently truncated or substituted downstream. Omitted/null stays
        valid — that is how a config leaves a knob to the provider/model."""
        if not self.provider_options:
            return self
        opts = self.provider_options

        if "top_k" in opts and opts["top_k"] is not None:
            value = opts["top_k"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"provider_options.top_k must be a non-negative integer or omitted (null), got {value!r}"
                )

        for key in _PROBABILITY_OPTION_KEYS:
            if key in opts and opts[key] is not None:
                value = opts[key]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not (0.0 <= value <= 1.0)
                ):
                    raise ValueError(
                        f"provider_options.{key} must be a probability between 0 and 1, or omitted (null), got {value!r}"
                    )

        if "repetition_penalty" in opts and opts["repetition_penalty"] is not None:
            value = opts["repetition_penalty"]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(
                    f"provider_options.repetition_penalty must be a positive number, or omitted (null), got {value!r}"
                )

        return self


# ============================================================================
# Tool Governance DTOs
# ============================================================================

class ToolGovernanceUpdateRequest(BaseModel):
    """Admin update to one tool's governance row. Both fields optional so a
    client can flip just `enabled` or just `locked` in one call."""
    enabled: Optional[bool] = None
    locked: Optional[bool] = None


class UserToolPreferenceRequest(BaseModel):
    """A user's opt-out toggle for one tool. `llm_config_id` names the caller's
    active LLM config - the toggle is always checked against that config's
    governance row (403/409 if it disabled or locked the tool); the opt-out
    itself is always stored globally, not per-config."""
    disabled: bool
    llm_config_id: str


class LLMConfigResponse(BaseModel):
    """Response model for LLM configuration.

    The API key is never returned. ``api_key_set`` reports whether a key is
    stored so the UI can show a "configured" state without exposing the
    secret; to update the key, clients send a new value on the request DTO.
    """
    id: str
    name: str
    type: str
    enabled: bool
    base_url: str
    api_key_set: bool = False
    model: str
    system_message: str
    temperature: float
    max_tokens: int
    timeout: int
    supports_vision: bool = False
    disable_system_prompt: bool = False
    memory_reflection: bool = True
    provider_options: Optional[Dict[str, Any]] = None
    is_default: bool = False


# ============================================================================
# LLM Generation DTOs
# ============================================================================

class LLMGenerateRequest(BaseModel):
    """Request model for LLM text generation."""
    prompt: str
    config_id: Optional[str] = None
    image_data: Optional[str] = None  # Base64 encoded image or file path for vision models


class LLMGenerateResponse(BaseModel):
    """Response model for LLM text generation."""
    content: str
    model: str
    provider_id: str
    tokens_used: Optional[int] = None


# ============================================================================
# User LLM Assignment DTOs
# ============================================================================

class UserLLMAssignmentRequest(BaseModel):
    """Request model for assigning LLM to user."""
    user_id: str
    llm_config_id: str


class UserLLMAssignmentResponse(BaseModel):
    """Response model for a user-LLM assignment."""
    user_id: str
    llm_config_id: str
    assigned_at: str


class UserLLMAssignmentsResponse(BaseModel):
    """Response model for user's LLM assignments."""
    user_id: str
    llm_configs: List[LLMConfigResponse]
