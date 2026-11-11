from typing import Dict, Iterable, Tuple

from src.features.mcp.protocol import McpToolCollaborators, _candidate_tools, _default_llm_id, _resolve_scope


def tool_counts_by_owner(
    collaborators: McpToolCollaborators, user_ids: Iterable[str]
) -> Dict[str, Tuple[int, int]]:
    total = len(_candidate_tools(collaborators))
    llm_id = _default_llm_id(collaborators)
    counts: Dict[str, Tuple[int, int]] = {}
    for user_id in dict.fromkeys(user_ids):
        exposed = len(_resolve_scope(collaborators, user_id, llm_id).allowed_names())
        counts[user_id] = (exposed, total)
    return counts
