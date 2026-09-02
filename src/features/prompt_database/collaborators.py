"""Frozen collaborators bundle for the prompt-database operations layer.

Every non-trivial prompt operation (create, replace, search, dedupe, embed)
needs the same three infrastructure legs together: the SQL repository, the
semantic vector store, and the embedding model. Bundling them once here -
built in the composition root and passed to `operations` functions and to
`ToolContext`/tool call sites as a single object - avoids threading three
positional collaborators through every call site. A plain, frozen data
holder (no behavior beyond field access), matching `McpToolCollaborators`
(see `src.features.mcp.protocol` - the reference shape for a wide-collaborator
dissolution).
"""
from dataclasses import dataclass

from src.features.models.repository import ModelRepository
from src.features.prompt_database.embedding import EmbeddingProvider
from src.features.prompt_database.repository import PromptRepository
from src.features.prompt_database.vector_store import PromptVectorStore
from src.platform.plugins.registry import PluginRegistry


@dataclass(frozen=True)
class PromptDatabaseCollaborators:
    repository: PromptRepository
    vector_store: PromptVectorStore
    embedding_provider: EmbeddingProvider
    plugin_registry: PluginRegistry
    model_repository: ModelRepository
