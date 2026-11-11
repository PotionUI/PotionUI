import type { ApiKey, ApiKeyScope } from '$lib/services/api/apiKeys';
import { parseServerDate } from '$lib/utils/relativeTime';

export const KEYS_PAGE_SIZE = 10;
export const EXPIRING_SOON_DAYS = 7;

export const SCOPE_LABELS: Record<Exclude<ApiKeyScope, 'mcp'>, string> = {
	read: 'Read',
	generate: 'Generate',
	content: 'Change content',
	admin: 'Full administration'
};

export interface KeyBadge {
	variant: 'neutral' | 'warning';
	label: string;
}

export interface KeyRows {
	active: ApiKey[];
	inactive: ApiKey[];
}

export interface KeyPage {
	rows: ApiKey[];
	page: number;
	pages: number;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAY_MS = 86_400_000;

function dayNumber(date: Date): number {
	return Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY_MS);
}

export function splitKeys(keys: readonly ApiKey[]): KeyRows {
	const active: ApiKey[] = [];
	const inactive: ApiKey[] = [];
	for (const key of keys) (key.status === 'active' ? active : inactive).push(key);
	return { active, inactive };
}

export function visibleKeys(rows: KeyRows, showInactive: boolean): ApiKey[] {
	return showInactive ? [...rows.active, ...rows.inactive] : rows.active;
}

export function pageOf(rows: readonly ApiKey[], requested: number, size = KEYS_PAGE_SIZE): KeyPage {
	const pages = Math.max(1, Math.ceil(rows.length / size));
	const page = Math.min(Math.max(1, requested), pages);
	return { rows: rows.slice((page - 1) * size, page * size), page, pages };
}

export function usedPhrase(lastUsedAt: string | null, now: Date = new Date()): string {
	const used = parseServerDate(lastUsedAt);
	if (!used) return 'never used';
	const days = dayNumber(now) - dayNumber(used);
	if (days <= 0) return 'used today';
	if (days === 1) return 'used yesterday';
	if (days < 7) return `used ${days} days ago`;
	if (days < 28) {
		const weeks = Math.floor(days / 7);
		return `used ${weeks} ${weeks === 1 ? 'week' : 'weeks'} ago`;
	}
	const month = `${MONTHS[used.getMonth()]} ${used.getDate()}`;
	return used.getFullYear() === now.getFullYear() ? `used ${month}` : `used ${month}, ${used.getFullYear()}`;
}

export function scopeLine(scopes: readonly ApiKeyScope[]): string {
	if (scopes.includes('admin')) return SCOPE_LABELS.admin;
	return (['read', 'generate', 'content'] as const)
		.filter((scope) => scopes.includes(scope))
		.map((scope) => SCOPE_LABELS[scope])
		.join(', ');
}

function toolCount(count: number): string {
	return `${count} ${count === 1 ? 'tool' : 'tools'}`;
}

export function keyLine(key: ApiKey, opts: { mcpPaused?: boolean; now?: Date } = {}): string {
	const http = scopeLine(key.scopes);
	const mcp = key.scopes.includes('mcp');
	const parts: string[] = [];
	if (mcp) {
		const tail = opts.mcpPaused ? 'paused' : key.mcp_tools ? toolCount(key.mcp_tools.exposed) : '';
		const text = opts.mcpPaused && http ? 'MCP paused' : tail ? `MCP, ${tail}` : 'MCP';
		if (http) parts.push(http, text);
		else parts.push(opts.mcpPaused ? 'MCP only, paused' : tail ? `MCP only, ${tail}` : 'MCP only');
	} else if (http) {
		parts.push(http);
	}
	parts.push(usedPhrase(key.last_used_at, opts.now));
	return parts.join(' · ');
}

export function keyBadge(key: ApiKey, now: Date = new Date()): KeyBadge | null {
	if (key.status === 'revoked') return { variant: 'neutral', label: 'Revoked' };
	if (key.status === 'expired') return { variant: 'neutral', label: 'Expired' };
	const expires = parseServerDate(key.expires_at);
	if (!expires) return null;
	const remaining = expires.getTime() - now.getTime();
	if (remaining > EXPIRING_SOON_DAYS * DAY_MS) return null;
	if (remaining <= 0) return { variant: 'neutral', label: 'Expired' };
	const days = Math.ceil(remaining / DAY_MS);
	return { variant: 'warning', label: days <= 1 ? 'Expires today' : `Expires in ${days} days` };
}

export function keyFingerprint(key: Pick<ApiKey, 'display_prefix' | 'last_four'>): string {
	return `${key.display_prefix}…${key.last_four ?? ''}`;
}

export function isMigratedKey(key: Pick<ApiKey, 'display_prefix' | 'last_four'>): boolean {
	return key.last_four === null && key.display_prefix.startsWith('pui_mcp_');
}

export function formatKeyDate(value: string | null, now: Date = new Date()): string {
	const date = parseServerDate(value);
	if (!date) return 'Never';
	const month = `${MONTHS[date.getMonth()]} ${date.getDate()}`;
	return date.getFullYear() === now.getFullYear() ? month : `${month}, ${date.getFullYear()}`;
}

export function mcpKeysOf(keys: readonly ApiKey[]): ApiKey[] {
	return keys.filter((key) => key.status === 'active' && key.scopes.includes('mcp'));
}
