import type { AdminApiKey, ApiKey, ApiKeyLimits } from '$lib/services/api/apiKeys';
import type { McpTool, McpToolsListing } from '$lib/services/api/mcpTools';

export const NOW = new Date('2026-11-11T12:00:00Z');

export function makeKey(over: Partial<ApiKey> = {}): ApiKey {
	return {
		id: 'k1',
		name: 'Claude Desktop',
		display_prefix: 'pui_7Hq2xK9a',
		last_four: 'Qz0P',
		scopes: ['read', 'mcp'],
		status: 'active',
		created_at: '2026-10-14T09:00:00Z',
		expires_at: null,
		last_used_at: '2026-11-11T08:00:00Z',
		last_used_via: 'mcp',
		revoked_at: null,
		revoked_by: null,
		mcp_tools: { exposed: 18, total: 44 },
		...over
	};
}

export function makeAdminKey(over: Partial<AdminApiKey> = {}): AdminApiKey {
	return { ...makeKey(), owner: { id: 'u1', username: 'Marta Kowalska' }, ...over };
}

export const LIMITS: ApiKeyLimits = {
	keys_enabled: true,
	mcp_enabled: true,
	scopes_allowed: ['read', 'generate', 'content', 'mcp'],
	max_lifetime_days: null
};

export function makeTool(over: Partial<McpTool> = {}): McpTool {
	return {
		name: 'search_gallery',
		label: 'Search Gallery',
		group: 'Generation',
		description: 'Does something useful.',
		writes: false,
		spends: false,
		unavailable_reason: null,
		state: 'on',
		reviewed: true,
		...over
	};
}

export function makeListing(tools: McpTool[], over: Partial<McpToolsListing> = {}): McpToolsListing {
	return {
		level: 'default',
		key_id: null,
		policy: 'read_only_on',
		ceiling_policy: 'all_on',
		summary: { exposed: 0, total: tools.length, writes_exposed: 0, unavailable: 0, locked: 0, unreviewed: 0 },
		tools,
		...over
	};
}

export const SAMPLE_TOOLS: McpTool[] = [
	makeTool({ name: 'start_generation', group: 'Generation', writes: true }),
	makeTool({ name: 'run_generation', group: 'Generation', writes: true, state: 'unavailable', unavailable_reason: 'Use start_generation over MCP.' }),
	makeTool({ name: 'search_gallery', group: 'Generation' }),
	makeTool({ name: 'run_media_tool', group: 'Generation', writes: true, state: 'locked_by_admin' }),
	makeTool({ name: 'organize_gallery', group: 'Generation', writes: true, state: 'off', reviewed: false }),
	makeTool({ name: 'list_presets', group: 'Models & presets' }),
	makeTool({ name: 'delete_prompt', group: 'Saved prompts', writes: true, state: 'off' }),
	makeTool({ name: 'list_prompts', group: 'Saved prompts', description: 'Lists the prompts in your library.', state: 'off_in_default' })
];
