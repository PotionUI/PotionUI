import type { ApiKeyScope } from '$lib/services/api/apiKeys';
import type { McpToolsListing } from '$lib/services/api/mcpTools';
import { SCOPE_DEFS, SCOPE_ORDER } from '$lib/components/apikeys/apiKeyDialog';
import { isEmptyChange, type ToolsChange } from '$lib/components/apikeys/mcpToolsView';

export const API_KEY_SETTING_KEYS = ['api_keys_enabled', 'api_key_scopes_allowed', 'api_key_max_lifetime_days'] as const;

export const GRANTABLE_SCOPES = ['generate', 'content', 'admin'] as const satisfies readonly ApiKeyScope[];
export type GrantableScope = (typeof GRANTABLE_SCOPES)[number];

export const ADMIN_KEY_MAX_DAYS = 90;

export interface LifetimeOption {
	days: number;
	label: string;
}

const STANDARD_LIFETIMES: readonly LifetimeOption[] = [
	{ days: 0, label: 'No limit' },
	{ days: 365, label: '1 year' },
	{ days: 90, label: '90 days' },
	{ days: 30, label: '30 days' }
];

export function keysEnabledOf(settings: Record<string, unknown>): boolean {
	const value = settings.api_keys_enabled;
	if (typeof value === 'string') return value.toLowerCase() !== 'false';
	return value === undefined || value === null ? true : Boolean(value);
}

export function scopesAllowedOf(settings: Record<string, unknown>): ApiKeyScope[] {
	let raw = settings.api_key_scopes_allowed;
	if (typeof raw === 'string') {
		try {
			raw = JSON.parse(raw);
		} catch {
			raw = null;
		}
	}
	const list = Array.isArray(raw) ? raw.filter((item): item is ApiKeyScope => typeof item === 'string') : ['read', 'generate', 'content', 'mcp'];
	return canonicalScopes(list as ApiKeyScope[]);
}

function canonicalScopes(scopes: readonly ApiKeyScope[]): ApiKeyScope[] {
	const set = new Set<string>(scopes);
	set.add('read');
	const known = SCOPE_ORDER.filter((scope) => set.has(scope));
	const extra = [...set].filter((scope) => !SCOPE_ORDER.includes(scope as ApiKeyScope));
	return [...known, ...extra] as ApiKeyScope[];
}

export function scopeAllowed(settings: Record<string, unknown>, scope: ApiKeyScope): boolean {
	return scopesAllowedOf(settings).includes(scope);
}

export function withScope(settings: Record<string, unknown>, scope: GrantableScope, on: boolean): ApiKeyScope[] {
	const current = scopesAllowedOf(settings).filter((item) => item !== scope);
	return canonicalScopes(on ? [...current, scope] : current);
}

export function lifetimeOf(settings: Record<string, unknown>): number {
	const days = Number(settings.api_key_max_lifetime_days ?? 0);
	return Number.isFinite(days) && days > 0 ? Math.floor(days) : 0;
}

export function lifetimeOptions(current: number): LifetimeOption[] {
	if (STANDARD_LIFETIMES.some((option) => option.days === current)) return [...STANDARD_LIFETIMES];
	return [...STANDARD_LIFETIMES, { days: current, label: `${current} days` }].sort((a, b) => (a.days === 0 ? -1 : b.days === 0 ? 1 : b.days - a.days));
}

export function scopeTitle(scope: GrantableScope): string {
	return SCOPE_DEFS[scope].label;
}

export function scopeNote(scope: GrantableScope): string | null {
	if (scope === 'admin') return `Admin keys can install plugins. They last ${ADMIN_KEY_MAX_DAYS} days at most.`;
	return null;
}

export type Direction = 'on' | 'off';

export interface EffectNote {
	tone: 'info' | 'warning';
	text: string;
}

export function keysSwitchNote(saved: boolean, draft: boolean): EffectNote | null {
	if (!draft) {
		return {
			tone: saved ? 'warning' : 'info',
			text: saved
				? 'On save, every key stops working within seconds, including open connections. Keys are not deleted: turning this back on restores them.'
				: 'Every key is refused while this is off, including open connections. Nobody can create one. Turning it on restores them.'
		};
	}
	return null;
}

export function mcpSwitchNote(saved: boolean, draft: boolean): EffectNote | null {
	if (!draft) {
		return {
			tone: saved ? 'warning' : 'info',
			text: saved
				? 'On save, MCP clients are cut off within seconds, open connections included. Keys keep working over the HTTP API.'
				: 'MCP clients are refused while this is off. Keys keep working over the HTTP API.'
		};
	}
	return null;
}

export function scopeLoweredNote(saved: boolean, draft: boolean): EffectNote | null {
	if (saved && !draft) {
		return {
			tone: 'warning',
			text: 'On save, keys that carry a scope turned off here lose it within seconds, open connections included. Their other scopes keep working.'
		};
	}
	return null;
}

export function lifetimeNote(draft: number): string {
	if (draft === 0) return `No limit on new keys. Administer keys are still capped at ${ADMIN_KEY_MAX_DAYS} days.`;
	const cap = Math.min(draft, ADMIN_KEY_MAX_DAYS);
	return `Applies to keys created from now on. Existing keys keep their expiry. Administer keys last ${cap} days at most.`;
}

export const CEILING_LOWERED_NOTE =
	'On save, keys and users lose any tool turned off here within seconds. Connected clients may need to refresh their tool list.';

export function mergeChange(current: ToolsChange, next: ToolsChange): ToolsChange {
	const merged: ToolsChange = {};
	const policy = next.policy ?? current.policy;
	if (policy) merged.policy = policy;
	const overrides = { ...(current.overrides ?? {}), ...(next.overrides ?? {}) };
	if (Object.keys(overrides).length) merged.overrides = overrides;
	return merged;
}

export function netChange(base: McpToolsListing, change: ToolsChange): ToolsChange {
	const net: ToolsChange = {};
	if (change.policy && change.policy !== base.policy) net.policy = change.policy;
	const overrides: Record<string, boolean | null> = {};
	for (const [name, value] of Object.entries(change.overrides ?? {})) {
		const tool = base.tools.find((item) => item.name === name);
		const unchanged = tool && tool.reviewed && value !== null && (tool.state === 'on') === value;
		if (!unchanged) overrides[name] = value;
	}
	if (Object.keys(overrides).length) net.overrides = overrides;
	return net;
}

export function pendingCount(base: McpToolsListing | null, change: ToolsChange): number {
	if (!base) return 0;
	const net = netChange(base, change);
	return (net.policy ? 1 : 0) + Object.keys(net.overrides ?? {}).length;
}

export function ceilingPatchOf(base: McpToolsListing, change: ToolsChange): ToolsChange | null {
	const net = netChange(base, change);
	return isEmptyChange(net) ? null : net;
}

export function ceilingLowered(base: McpToolsListing | null, change: ToolsChange): boolean {
	if (!base) return false;
	const net = netChange(base, change);
	if (net.policy === 'read_only_on' && base.policy !== 'read_only_on') return true;
	return Object.entries(net.overrides ?? {}).some(([name, value]) => {
		const tool = base.tools.find((item) => item.name === name);
		return value === false && tool?.state === 'on';
	});
}
