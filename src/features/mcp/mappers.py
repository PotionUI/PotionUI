"""
Response mappers for the mcp feature.

Plain functions that turn an `McpToken` record into its API response dict.
No class, no state.
"""
from src.features.mcp.records import McpToken


def token_to_dict(token: McpToken) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "token_prefix": token.token_prefix,
        "created_at": token.created_at,
        "last_used_at": token.last_used_at,
        "revoked_at": token.revoked_at,
    }
