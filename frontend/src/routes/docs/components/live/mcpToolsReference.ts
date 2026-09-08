export interface McpToolParameter {
	name: string;
	type?: string;
	required?: boolean;
	description?: string;
	[key: string]: unknown;
}

export interface McpToolEntry {
	name: string;
	description?: string;
	group?: string;
	mutating?: boolean;
	parameters?: McpToolParameter[];
	[key: string]: unknown;
}

export interface McpGovernance {
	acts_as_token_owner?: string;
	global_setting_key?: string;
	global_default_enabled?: boolean;
	user_setting_key?: string;
	user_default_enabled?: boolean;
	model_visibility_rule?: string;
	excluded_tool_names?: string[];
	[key: string]: unknown;
}

export function toolParameters(entry: McpToolEntry): McpToolParameter[] {
	return entry.parameters ?? [];
}

export function matchesMcpTool(entry: McpToolEntry, query: string): boolean {
	const needle = query.toLowerCase();
	if (entry.name.toLowerCase().includes(needle)) return true;
	if ((entry.description || '').toLowerCase().includes(needle)) return true;
	if ((entry.group || '').toLowerCase().includes(needle)) return true;
	return toolParameters(entry).some((param) => param.name.toLowerCase().includes(needle));
}
