"""Frozen collaborators bundle for the prompt-enhancement operations layer.

The gather -> ideate -> write pipeline and its feedback loop share the same
handful of infrastructure legs - the LLM gateway, the prompt library, model
grounding, feedback history, and the active preset's style guide. Bundling
them once here - built in the composition root and passed to `operations`
functions and to `ToolContext`/`ChatRuntime` call sites as a single object -
avoids threading six positional collaborators through every call site. A
plain, frozen data holder (no behavior beyond field access), matching
`PromptDatabaseCollaborators` (see `src.features.prompt_database.collaborators`
- the reference shape for a wide-collaborator dissolution).
"""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptEnhancementCollaborators:
    llm_service: Any
    prompt_database: Any = None
    model_index_manager: Any = None
    llm_memory_repository: Any = None
    feedback_repository: Any = None
    preset_manager: Any = None
