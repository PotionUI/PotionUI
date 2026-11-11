import type {
	McpKeyToolsPatch,
	McpTool,
	McpToolOverrides,
	McpToolsLevel,
	McpToolsListing,
	McpToolsPolicy
} from '$lib/services/api/mcpTools';

export type ToolFilter = 'all' | 'on' | 'off' | 'unavailable';
export type ToolKind = 'tool' | 'offDefault' | 'admin' | 'unavailable';

export const FILTERS: readonly { id: ToolFilter; label: string }[] = [
	{ id: 'all', label: 'All' },
	{ id: 'on', label: 'On' },
	{ id: 'off', label: 'Off' },
	{ id: 'unavailable', label: 'Can’t run' }
];

export interface ToolsView {
	query: string;
	filter: ToolFilter;
}

export interface ToolCounts {
	on: number;
	usable: number;
	unavailable: number;
	locked: number;
	offDefault: number;
}

export interface ToolGroup {
	name: string;
	shown: McpTool[];
	on: number;
	denominator: number;
	toggleable: McpTool[];
	allOn: boolean;
	mixed: boolean;
}

export interface ToolsChange {
	overrides?: McpToolOverrides;
	policy?: McpToolsPolicy;
	reset?: boolean;
}

export function toolKind(tool: McpTool): ToolKind {
	switch (tool.state) {
		case 'unavailable':
			return 'unavailable';
		case 'locked_by_admin':
			return 'admin';
		case 'off_in_default':
			return 'offDefault';
		default:
			return 'tool';
	}
}

export function isToggleable(tool: McpTool): boolean {
	return toolKind(tool) === 'tool';
}

export function showsNewChip(tool: McpTool): boolean {
	const kind = toolKind(tool);
	return !tool.reviewed && (kind === 'tool' || kind === 'offDefault');
}

export function countTools(tools: readonly McpTool[]): ToolCounts {
	const counts: ToolCounts = { on: 0, usable: 0, unavailable: 0, locked: 0, offDefault: 0 };
	for (const tool of tools) {
		const kind = toolKind(tool);
		if (kind === 'unavailable') counts.unavailable += 1;
		else if (kind === 'admin') counts.locked += 1;
		else {
			counts.usable += 1;
			if (kind === 'offDefault') counts.offDefault += 1;
			else if (tool.state === 'on') counts.on += 1;
		}
	}
	return counts;
}

export function summaryText(level: McpToolsLevel, counts: ToolCounts): string {
	return `${counts.on} of ${counts.usable} ${level === 'ceiling' ? 'allowed' : 'on'}`;
}

export function bulkAction(level: McpToolsLevel): { id: 'writes-off' | 'match'; label: string } {
	return level === 'key' ? { id: 'match', label: 'Match default' } : { id: 'writes-off', label: 'Turn off writes' };
}

export function policyOptions(level: McpToolsLevel): { id: McpToolsPolicy; label: string }[] {
	return level === 'ceiling'
		? [
				{ id: 'all_on', label: 'All tools' },
				{ id: 'read_only_on', label: 'Read-only tools only' }
			]
		: [
				{ id: 'all_on', label: 'All on' },
				{ id: 'read_only_on', label: 'Read-only on' }
			];
}

export function toolMatches(tool: McpTool, view: ToolsView): boolean {
	const query = view.query.trim().toLowerCase();
	if (query && !(tool.name.toLowerCase().includes(query) || tool.description.toLowerCase().includes(query) || tool.group.toLowerCase().includes(query))) {
		return false;
	}
	const kind = toolKind(tool);
	switch (view.filter) {
		case 'on':
			return kind === 'tool' && tool.state === 'on';
		case 'off':
			return (kind === 'tool' || kind === 'offDefault') && tool.state !== 'on';
		case 'unavailable':
			return kind === 'unavailable';
		default:
			return true;
	}
}

export function buildGroups(tools: readonly McpTool[], view: ToolsView): ToolGroup[] {
	const order: string[] = [];
	const byName = new Map<string, McpTool[]>();
	for (const tool of tools) {
		if (!byName.has(tool.group)) {
			byName.set(tool.group, []);
			order.push(tool.group);
		}
		byName.get(tool.group)!.push(tool);
	}
	const groups: ToolGroup[] = [];
	for (const name of order) {
		const all = byName.get(name)!;
		const shown = all.filter((tool) => toolMatches(tool, view));
		if (!shown.length) continue;
		const toggleable = all.filter(isToggleable);
		const on = toggleable.filter((tool) => tool.state === 'on').length;
		groups.push({
			name,
			shown,
			on,
			denominator: all.filter((tool) => toolKind(tool) === 'tool' || toolKind(tool) === 'offDefault').length,
			toggleable,
			allOn: toggleable.length > 0 && on === toggleable.length,
			mixed: on > 0 && on < toggleable.length
		});
	}
	return groups;
}

export function isGroupOpen(
	group: string,
	view: ToolsView,
	chosen: Readonly<Record<string, boolean>>,
	defaultOpen: string | null
): boolean {
	if (view.query.trim() !== '' || view.filter !== 'all') return true;
	return group in chosen ? chosen[group] : group === defaultOpen;
}

export function firstGroup(tools: readonly McpTool[]): string | null {
	return tools[0]?.group ?? null;
}

function valueFor(level: McpToolsLevel, on: boolean): boolean | null {
	if (level === 'key') return on ? null : false;
	return on;
}

export function changeForTools(level: McpToolsLevel, tools: readonly McpTool[], on: boolean): ToolsChange {
	const overrides: McpToolOverrides = {};
	for (const tool of tools) if (isToggleable(tool)) overrides[tool.name] = valueFor(level, on);
	return { overrides };
}

export function changeForWritesOff(level: McpToolsLevel, tools: readonly McpTool[]): ToolsChange {
	return changeForTools(level, tools.filter((tool) => tool.writes && tool.state === 'on'), false);
}

export function changeForPolicy(policy: McpToolsPolicy): ToolsChange {
	return { policy };
}

export function changeForReset(): ToolsChange {
	return { reset: true };
}

export function isEmptyChange(change: ToolsChange): boolean {
	return !change.reset && !change.policy && Object.keys(change.overrides ?? {}).length === 0;
}

export function policyAllows(policy: McpToolsPolicy, tool: McpTool): boolean {
	return policy === 'all_on' || !tool.writes;
}

function followPolicy(tool: McpTool, policy: McpToolsPolicy): McpTool {
	return { ...tool, state: policyAllows(policy, tool) ? 'on' : 'off' };
}

export function applyChange(listing: McpToolsListing, change: ToolsChange): McpToolsListing {
	const key = listing.level === 'key';
	let tools = listing.tools;
	let policy = listing.policy;
	if (change.reset) {
		tools = tools.map((tool) => (tool.state === 'off' ? { ...tool, state: 'on' } : tool));
	}
	if (change.policy && !key) {
		const next = change.policy;
		policy = next;
		tools = tools.map((tool) => (!tool.reviewed && isToggleable(tool) ? followPolicy(tool, next) : tool));
	}
	if (change.overrides) {
		const overrides = change.overrides;
		tools = tools.map((tool) => {
			if (!isToggleable(tool) || !(tool.name in overrides)) return tool;
			const value = overrides[tool.name];
			if (key) return { ...tool, state: value === false ? 'off' : 'on' };
			if (value === null) return { ...followPolicy(tool, policy ?? 'read_only_on'), reviewed: false };
			return { ...tool, state: value ? 'on' : 'off', reviewed: true };
		});
	}
	return { ...listing, policy, tools };
}

export function keyPatchOf(change: ToolsChange): McpKeyToolsPatch {
	if (change.reset) return { reset: true };
	const overrides: Record<string, false | null> = {};
	for (const [name, value] of Object.entries(change.overrides ?? {})) overrides[name] = value === false ? false : null;
	return { overrides };
}
