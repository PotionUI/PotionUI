"""
Prompt database operations.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function taking a `PromptDatabaseCollaborators` bundle (repository + vector
store + embedding provider + plugin registry - see `collaborators.py`) as
its leading arg. `PromptDatabaseController` (`routes.py`) and the chat/MCP
tool surface hold the bundle and pass it in; nothing here is stored across
calls.

Shape rule: one module per concern (`mutations`, `search`, `embedding`),
each re-exported here as the public surface. Plain reads (`list_prompts`,
`get_prompt`) are pure repository calls made directly by callers against
`collaborators.repository` - there is nothing here for them to add.
"""
from src.features.prompt_database.operations.mutations import (
    MANUAL_SOURCE_PROVIDER,
    add_prompt,
    bulk_delete_prompts,
    create_prompt,
    delete_prompt,
    purge_model_prompts,
    replace_prompt,
)
from src.features.prompt_database.operations.embedding import embed_pending, embed_prompt
from src.features.prompt_database.operations.search import find_duplicates, search

__all__ = [
    "MANUAL_SOURCE_PROVIDER",
    "add_prompt",
    "bulk_delete_prompts",
    "create_prompt",
    "delete_prompt",
    "purge_model_prompts",
    "replace_prompt",
    "embed_pending",
    "embed_prompt",
    "find_duplicates",
    "search",
]
