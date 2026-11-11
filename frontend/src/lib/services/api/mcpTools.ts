import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';

export type McpToolsLevel = 'default' | 'key' | 'ceiling';
export type McpToolsPolicy = 'all_on' | 'read_only_on';
export type McpToolState = 'on' | 'off' | 'off_in_default' | 'locked_by_admin' | 'unavailable';

export interface McpTool {
	name: string;
	label: string;
	group: string;
	description: string;
	writes: boolean;
	spends: boolean;
	unavailable_reason: string | null;
	state: McpToolState;
	reviewed: boolean;
}

export interface McpToolsSummary {
	exposed: number;
	total: number;
	writes_exposed: number;
	unavailable: number;
	locked: number;
	unreviewed: number;
}

export interface McpToolsListing {
	level: McpToolsLevel;
	key_id: string | null;
	policy?: McpToolsPolicy;
	ceiling_policy: McpToolsPolicy;
	summary: McpToolsSummary;
	tools: McpTool[];
}

export type McpToolOverrides = Record<string, boolean | null>;

export interface McpToolsPatch {
	policy?: McpToolsPolicy;
	overrides?: McpToolOverrides;
}

export type McpKeyToolsPatch = { overrides: Record<string, false | null> } | { reset: true };

export function createMcpToolsApi(client: AxiosInstance) {
	return {
		async getMcpTools(): Promise<APIResponse<McpToolsListing>> {
			const response = await client.get('/api/mcp/tools');
			return response.data;
		},

		async patchMcpTools(patch: McpToolsPatch): Promise<APIResponse<McpToolsListing>> {
			const response = await client.patch('/api/mcp/tools', patch);
			return response.data;
		},

		async getMcpKeyTools(keyId: string): Promise<APIResponse<McpToolsListing>> {
			const response = await client.get(`/api/mcp/keys/${encodeURIComponent(keyId)}/tools`);
			return response.data;
		},

		async patchMcpKeyTools(keyId: string, patch: McpKeyToolsPatch): Promise<APIResponse<McpToolsListing>> {
			const response = await client.patch(`/api/mcp/keys/${encodeURIComponent(keyId)}/tools`, patch);
			return response.data;
		}
	};
}
