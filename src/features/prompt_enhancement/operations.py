"""Prompt enhancement operations: staged creative expansion with grounding and learning.

Runs the deterministic gather -> ideate -> write enhancement pipeline. Module-
level functions, `PromptEnhancementCollaborators` as their leading arg - no
class holds them together (see `collaborators.py`'s docstring).
"""

import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.features.llm.tools.builtin.utils import extract_model_path, lookup_model, resolve_active_model_id
from src.features.prompt_database import operations as prompt_database_operations
from src.features.prompt_enhancement.collaborators import PromptEnhancementCollaborators
from src.features.prompt_enhancement.guidelines import PROMPT_ENHANCEMENT_GUIDELINES

logger = logging.getLogger(__name__)

APPROVED_SOURCE_PROVIDER = "chat_approved"

MAX_APPROVED_EXEMPLARS = 3
MAX_COMMUNITY_EXEMPLARS = 4
MAX_REJECTION_HINTS = 5
MAX_CONCEPTS = 6

CONCEPT_SYSTEM_MESSAGE = (
    "You decompose image descriptions into atomic visual concepts for semantic search. "
    "Reply with ONLY a JSON array of 3-6 short strings, nothing else."
)

IDEATE_SYSTEM_MESSAGE = (
    "You are a creative director brainstorming visually striking scene concepts "
    "for AI image generation. You value the unexpected over the safe."
)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

async def enhance_stream(
    collaborators: PromptEnhancementCollaborators,
    user_id: str,
    llm_id: str,
    brief: str,
    form_state: Optional[dict] = None,
    model_id: Optional[str] = None,
    n_candidates: int = 2,
    mode: str = "generation",
) -> AsyncGenerator[Dict[str, Any], None]:
    """Run the enhancement pipeline, yielding stage events and a final result.

    Yields dicts:
        {"type": "stage_start"|"stage_end", "stage": "enhance:gather"|...}
        {"type": "result", "data": {"candidates": [...], "model_id", "brief", "exemplar_ids"}}
    """
    brief = (brief or "").strip()

    yield {"type": "stage_start", "stage": "enhance:gather"}
    context = await _gather(collaborators, user_id, llm_id, brief, form_state, model_id, mode=mode)
    yield {"type": "stage_end", "stage": "enhance:gather"}

    yield {"type": "stage_start", "stage": "enhance:ideate"}
    directions = await _ideate(collaborators, llm_id, brief, context, n_candidates)
    yield {"type": "stage_end", "stage": "enhance:ideate"}

    yield {"type": "stage_start", "stage": "enhance:write"}
    candidates = []
    for direction in directions[:n_candidates]:
        text = await _write(collaborators, llm_id, brief, context, direction)
        candidates.append({"text": text, "direction": direction})
    yield {"type": "stage_end", "stage": "enhance:write"}

    yield {
        "type": "result",
        "data": {
            "candidates": candidates,
            "model_id": context.get("model_id"),
            "brief": brief,
            "exemplar_ids": context.get("exemplar_ids", []),
        },
    }


async def enhance(
    collaborators: PromptEnhancementCollaborators,
    user_id: str,
    llm_id: str,
    brief: str,
    form_state: Optional[dict] = None,
    model_id: Optional[str] = None,
    n_candidates: int = 1,
    mode: str = "generation",
) -> Dict[str, Any]:
    """Non-streaming wrapper: run the pipeline and return the final result."""
    result: Dict[str, Any] = {}
    async for event in enhance_stream(
        collaborators, user_id, llm_id, brief,
        form_state=form_state, model_id=model_id, n_candidates=n_candidates,
        mode=mode,
    ):
        if event["type"] == "result":
            result = event["data"]
    return result


async def record_feedback(
    collaborators: PromptEnhancementCollaborators,
    user_id: str,
    session_id: str,
    message_id: str,
    prompt_text: str,
    verdict: str,
    model_id: Optional[str] = None,
    reason: Optional[str] = None,
    mode: str = "generation",
) -> Dict[str, Any]:
    """Record approve/reject feedback; approved prompts join the exemplar library.

    Exemplars and verdicts are tagged with the chat mode so retrieval never
    crosses modes (legacy rows without a mode count as 'generation').
    """
    if verdict not in ("approved", "rejected"):
        raise ValueError(f"Invalid verdict '{verdict}'")

    prompt_id = None
    if verdict == "approved" and collaborators.prompt_database:
        saved = await prompt_database_operations.add_prompt(
            collaborators.prompt_database,
            user_id=user_id,
            prompt_text=prompt_text,
            model_id=model_id,
            source_provider=APPROVED_SOURCE_PROVIDER,
            metadata={"mode": mode},
        )
        prompt_id = saved.id

    feedback_id = None
    if collaborators.feedback_repository:
        feedback = collaborators.feedback_repository.create(
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            prompt_text=prompt_text,
            verdict=verdict,
            model_id=model_id,
            reason=reason,
            prompt_id=prompt_id,
            mode=mode,
        )
        feedback_id = feedback.id

    return {"feedback_id": feedback_id, "prompt_id": prompt_id}


# ------------------------------------------------------------------
# Stage A: gather
# ------------------------------------------------------------------

async def _gather(
    collaborators: PromptEnhancementCollaborators,
    user_id: str,
    llm_id: str,
    brief: str,
    form_state: Optional[dict],
    model_id: Optional[str],
    mode: str = "generation",
) -> Dict[str, Any]:
    if not model_id:
        model_id = resolve_active_model_id(form_state, collaborators.model_index_manager)

    model_info = _gather_model_grounding(collaborators, form_state)
    preset_guide = _gather_preset_guide(collaborators, form_state)
    concepts = await _decompose_concepts(collaborators, llm_id, brief)

    approved: List[Any] = []
    community: List[Any] = []
    if collaborators.prompt_database:
        approved = await _search_prompts(
            collaborators, user_id, [brief], model_id,
            limit_per_query=MAX_APPROVED_EXEMPLARS,
            total_cap=MAX_APPROVED_EXEMPLARS,
            source_provider=APPROVED_SOURCE_PROVIDER,
            mode=mode,
        )
        community = await _search_prompts(
            collaborators, user_id, concepts or [brief], model_id,
            limit_per_query=2,
            total_cap=MAX_COMMUNITY_EXEMPLARS,
            exclude=approved,
        )

    rejection_reasons: List[str] = []
    if collaborators.feedback_repository:
        try:
            rejection_reasons = collaborators.feedback_repository.get_recent_rejection_reasons(
                user_id=user_id, model_id=model_id, limit=MAX_REJECTION_HINTS,
            )
        except Exception as e:
            logger.warning(f"Could not load rejection reasons: {e}")

    exemplar_ids = [p.id for p in approved + community if getattr(p, "id", None)]
    logger.debug(
        f"Enhance gather: model_id={model_id}, concepts={len(concepts)}, "
        f"approved_exemplars={len(approved)}, community_exemplars={len(community)}, "
        f"rejection_hints={len(rejection_reasons)}"
    )
    return {
        "model_id": model_id,
        "model_info": model_info,
        "preset_guide": preset_guide,
        "concepts": concepts,
        "approved": approved,
        "community": community,
        "rejection_reasons": rejection_reasons,
        "exemplar_ids": exemplar_ids,
    }


def _gather_model_grounding(
    collaborators: PromptEnhancementCollaborators, form_state: Optional[dict]
) -> Optional[Dict[str, Any]]:
    """Look up metadata for the first model selected in the form."""
    if not form_state or not collaborators.model_index_manager:
        return None
    form_data = form_state.get("form_data") or {}
    for value in form_data.values():
        model_path = extract_model_path(value)
        if not model_path:
            continue
        info = lookup_model(collaborators.model_index_manager, model_path)
        if info.get("id"):
            return info
    return None


def _gather_preset_guide(
    collaborators: PromptEnhancementCollaborators, form_state: Optional[dict]
) -> Optional[str]:
    """Look up the active preset's `llm.guide` (see docs/presets.md "LLM context"),
    replaced by `llm.modes[<current mode>].guide` when one is declared."""
    if not form_state or not collaborators.preset_manager:
        return None
    preset_id = form_state.get("preset")
    if not preset_id:
        return None
    try:
        preset = collaborators.preset_manager.file_repo.find_preset_by_id(preset_id)
    except Exception:
        return None
    if not preset:
        return None
    llm_spec = getattr(preset, "llm", None) or {}
    mode = form_state.get("mode")
    if mode:
        mode_spec = (llm_spec.get("modes") or {}).get(mode)
        if mode_spec and mode_spec.get("guide"):
            return mode_spec["guide"]
    return llm_spec.get("guide") or None


async def _decompose_concepts(
    collaborators: PromptEnhancementCollaborators, llm_id: str, brief: str
) -> List[str]:
    """One cheap LLM call to split the brief into atomic search concepts."""
    if not brief:
        return []
    try:
        response = await collaborators.llm_service.generate_with_history(
            messages=[{
                "role": "user",
                "content": (
                    "Decompose this image description into 3-6 atomic visual concepts "
                    "(subject, environment, style, lighting, composition, mood):\n\n"
                    f"{brief}"
                ),
            }],
            llm_id=llm_id,
            custom_system_message=CONCEPT_SYSTEM_MESSAGE,
            options_override={"temperature": 0.3, "max_tokens": 150, "think": False},
        )
        concepts = _parse_json_array(response.content)
        if concepts:
            return concepts[:MAX_CONCEPTS]
    except Exception as e:
        logger.warning(f"Concept decomposition failed, falling back: {e}")
    # Deterministic fallback: comma-split or the raw brief
    parts = [p.strip() for p in brief.split(",") if p.strip()]
    return (parts or [brief])[:MAX_CONCEPTS]


async def _search_prompts(
    collaborators: PromptEnhancementCollaborators,
    user_id: str,
    queries: List[str],
    model_id: Optional[str],
    limit_per_query: int,
    total_cap: int,
    source_provider: Optional[str] = None,
    exclude: Optional[List[Any]] = None,
    mode: Optional[str] = None,
) -> List[Any]:
    """Search the prompt library, deduping across queries and against `exclude`.

    `mode` filters post-retrieval on prompt metadata (the vector store has no
    metadata filtering); prompts without a mode tag count as 'generation'.
    """
    seen = {_dedupe_key(p) for p in (exclude or [])}
    results: List[Any] = []
    for query in queries:
        if len(results) >= total_cap:
            break
        try:
            kwargs: Dict[str, Any] = {
                "user_id": user_id,
                "query": query,
                # Over-fetch when mode-filtering post-retrieval
                "limit": limit_per_query * 2 if mode else limit_per_query,
            }
            if model_id:
                kwargs["model_id"] = model_id
            if source_provider:
                kwargs["source_provider"] = source_provider
            prompts = await prompt_database_operations.search(collaborators.prompt_database, **kwargs)
        except Exception as e:
            logger.warning(f"Prompt search failed for '{query}': {e}")
            continue
        if mode:
            prompts = [
                p for p in (prompts or [])
                if (getattr(p, "metadata", None) or {}).get("mode", "generation") == mode
            ]
        for p in prompts or []:
            key = _dedupe_key(p)
            if key in seen:
                continue
            seen.add(key)
            results.append(p)
            if len(results) >= total_cap:
                break
    return results


def _dedupe_key(p: Any) -> str:
    return getattr(p, "source_url", None) or (getattr(p, "prompt", None) or "")


# ------------------------------------------------------------------
# Stage B: ideate
# ------------------------------------------------------------------

async def _ideate(
    collaborators: PromptEnhancementCollaborators,
    llm_id: str, brief: str, context: Dict[str, Any], n_candidates: int
) -> List[str]:
    prompt_parts = [
        f"The user wants an image of: {brief}" if brief else "The user wants an image but gave no description yet.",
    ]
    grounding = _format_model_grounding(context.get("model_info"))
    if grounding:
        prompt_parts.append(grounding)
    concepts = context.get("concepts") or []
    if concepts:
        prompt_parts.append("Key concepts: " + ", ".join(concepts))
    prompt_parts.append(
        f"Propose {n_candidates} DIVERGENT scene directions for this image — each with a "
        "different setting, lighting, and mood. One short paragraph per direction. "
        "Be specific and surprising; do not restate the user's words. "
        f"Number them 1. to {n_candidates}."
    )

    try:
        response = await collaborators.llm_service.generate_with_history(
            messages=[{"role": "user", "content": "\n\n".join(prompt_parts)}],
            llm_id=llm_id,
            custom_system_message=IDEATE_SYSTEM_MESSAGE,
            options_override={
                "temperature": 1.1, "top_p": 0.95, "max_tokens": 600, "think": False,
            },
        )
        directions = _parse_numbered_list(response.content)
        if directions:
            return directions
        if response.content.strip():
            return [response.content.strip()]
    except Exception as e:
        logger.warning(f"Ideation failed, writing directly from the brief: {e}")
    # Fallback: no explicit direction; the write stage expands the brief alone
    return [""] * n_candidates


# ------------------------------------------------------------------
# Stage C: write
# ------------------------------------------------------------------

async def _write(
    collaborators: PromptEnhancementCollaborators,
    llm_id: str, brief: str, context: Dict[str, Any], direction: str
) -> str:
    user_prompt = _build_write_prompt(brief, context, direction)
    options = {"temperature": 0.85, "max_tokens": 800, "think": False}

    text = await _call_write(collaborators, llm_id, user_prompt, options)
    if _is_flat(brief, text):
        logger.debug("Enhance write: candidate too close to the brief, retrying hotter")
        retry_options = dict(options, temperature=1.0)
        retry = await _call_write(collaborators, llm_id, user_prompt, retry_options)
        if not _is_flat(brief, retry) or len(retry) > len(text):
            return retry
    return text


async def _call_write(
    collaborators: PromptEnhancementCollaborators, llm_id: str, user_prompt: str, options: Dict[str, Any]
) -> str:
    response = await collaborators.llm_service.generate_with_history(
        messages=[{"role": "user", "content": user_prompt}],
        llm_id=llm_id,
        custom_system_message=PROMPT_ENHANCEMENT_GUIDELINES,
        options_override=options,
    )
    return _strip_thinking(response.content).strip()


def _build_write_prompt(brief: str, context: Dict[str, Any], direction: str) -> str:
    parts: List[str] = []

    preset_guide = context.get("preset_guide")
    if preset_guide:
        parts.append(f"House style guide for this preset:\n{preset_guide[:3000]}")

    grounding = _format_model_grounding(context.get("model_info"))
    if grounding:
        parts.append(grounding)

    examples = _format_exemplars(context)
    if examples:
        parts.append(examples)

    rejections = context.get("rejection_reasons") or []
    if rejections:
        parts.append(
            "The user rejected earlier suggestions for these reasons — avoid them:\n"
            + "\n".join(f"- {r}" for r in rejections)
        )

    if direction:
        parts.append(f"Chosen creative direction:\n{direction}")

    parts.append(
        f"The user's description (keep this subject, expand everything else):\n{brief}\n\n"
        "Write the full image generation prompt now. Output ONLY the prompt text."
    )
    return "\n\n".join(parts)


def _format_model_grounding(model_info: Optional[Dict[str, Any]]) -> str:
    if not model_info:
        return ""
    lines = [f"Generation model: {model_info.get('filename', 'unknown')}"]
    guidance = model_info.get("prompting_guidance")
    if guidance:
        lines.append(f"Admin prompting guidance: {guidance[:1500]}")
    description = model_info.get("combined_description") or model_info.get("description")
    if description:
        lines.append(f"Model notes (style, trigger words, prompt guide): {description[:1500]}")
    tags = model_info.get("tags") or []
    if tags:
        lines.append("Model tags: " + ", ".join(tags[:15]))
    lines.append(
        "Write in the vocabulary this model responds to; include any trigger words from the notes."
    )
    return "\n".join(lines)


def _format_exemplars(context: Dict[str, Any]) -> str:
    blocks: List[str] = []
    for p in context.get("approved") or []:
        if getattr(p, "prompt", None):
            blocks.append(f"[user-approved] {p.prompt[:500]}")
    for p in context.get("community") or []:
        if getattr(p, "prompt", None):
            blocks.append(f"[community] {p.prompt[:500]}")
    if not blocks:
        return ""
    return (
        "Examples of prompts this model responds well to — match their level of detail "
        "and vocabulary, but write a NEW scene:\n" + "\n\n".join(blocks)
    )


# ------------------------------------------------------------------
# Parsing / heuristics
# ------------------------------------------------------------------

def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks some local models emit despite think=False."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)


def _parse_json_array(text: str) -> List[str]:
    """Leniently extract a JSON array of strings from model output."""
    if not text:
        return []
    match = re.search(r"\[.*?\]", text, flags=re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if item and str(item).strip()]


def _parse_numbered_list(text: str) -> List[str]:
    """Split model output on numbered items (1. / 1) / 1:)."""
    text = _strip_thinking(text)
    if not text:
        return []
    items = re.split(r"(?:^|\n)\s*\d+\s*[\.\)\:]\s*", text)
    return [item.strip() for item in items if item.strip()]


def _is_flat(brief: str, candidate: str) -> bool:
    """True when the candidate barely expands beyond the brief."""
    if not candidate:
        return True
    if len(candidate) < max(1.6 * len(brief), 400):
        return True
    brief_words = {w for w in re.findall(r"[a-z0-9]+", brief.lower()) if len(w) > 3}
    candidate_words = [w for w in re.findall(r"[a-z0-9]+", candidate.lower()) if len(w) > 3]
    if not candidate_words or not brief_words:
        return False
    overlap = sum(1 for w in candidate_words if w in brief_words) / len(candidate_words)
    return overlap > 0.8
