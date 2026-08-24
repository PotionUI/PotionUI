import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';

/**
 * One MCP token as returned by the list/create endpoints. `token_prefix` is a
 * short, non-secret slice used to recognise a token in the UI - the full
 * secret is never returned again after creation.
 */
export interface McpToken {
	id: string;
	name: string;
	token_prefix: string;
	created_at: string;
	last_used_at: string | null;
	revoked_at: string | null;
}

/** Create-only response shape: carries the full secret, once. */
export interface McpTokenCreated extends McpToken {
	token: string;
}

/**
 * Effective MCP availability for the current user - `enabled` is the AND of
 * the two admin-controlled flags. Token list/create/revoke stay reachable
 * even when `enabled` is false; only connecting via MCP itself is gated.
 */
export interface McpStatus {
	enabled: boolean;
	global_enabled: boolean;
	user_enabled: boolean;
}

export function createMcpApi(client: AxiosInstance) {
	return {
		async getMcpStatus(): Promise<APIResponse<McpStatus>> {
			const response = await client.get('/api/mcp/status');
			return response.data;
		},

		async listMcpTokens(): Promise<APIResponse<McpToken[]>> {
			const response = await client.get('/api/mcp/tokens');
			return response.data;
		},

		/** The returned `token` is the only time the full secret is ever exposed. */
		async createMcpToken(name: string): Promise<APIResponse<McpTokenCreated>> {
			const response = await client.post('/api/mcp/tokens', { name });
			return response.data;
		},

		async revokeMcpToken(tokenId: string): Promise<APIResponse<{ id: string; revoked: boolean }>> {
			const response = await client.delete(`/api/mcp/tokens/${tokenId}`);
			return response.data;
		}
	};
}
