from typing import Dict, Iterable, Tuple

from src.features.mcp.operations.tools import counts_for_keys
from src.features.mcp.protocol import McpToolCollaborators


def tool_counts_by_key(
    collaborators: McpToolCollaborators, key_owners: Iterable[Tuple[str, str]]
) -> Dict[str, Tuple[int, int]]:
    return counts_for_keys(collaborators, dict(key_owners))
