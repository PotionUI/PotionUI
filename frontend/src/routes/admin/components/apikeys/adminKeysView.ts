import type { AdminApiKey, AdminApiKeysQuery, AdminKeyCounts, AdminKeyStatusFilter, ApiKeyEvent } from '$lib/services/api/apiKeys';
import { formatKeyDate, keyBadge, keyFingerprint, scopeLine, usedPhrase, type KeyBadge } from '$lib/components/apikeys/apiKeyView';
import { describeEvent, type ActivityLine } from '$lib/components/apikeys/apiKeyEvents';
import { parseServerDate } from '$lib/utils/relativeTime';

export const ADMIN_KEYS_PAGE_SIZE = 10;
export const ADMIN_EVENTS_LIMIT = 20;
export const USER_KEYS_LIMIT = 8;

export const STATUS_FILTERS: readonly { id: AdminKeyStatusFilter; label: string }[] = [
	{ id: 'all', label: 'All' },
	{ id: 'active', label: 'Active' },
	{ id: 'expired', label: 'Expired' },
	{ id: 'revoked', label: 'Revoked' }
];

export interface AdminKeysView {
	status: AdminKeyStatusFilter;
	owner: string | null;
	page: number;
	search: string;
}

export const INITIAL_VIEW: AdminKeysView = { status: 'all', owner: null, page: 1, search: '' };

export function changeView(view: AdminKeysView, patch: Partial<Pick<AdminKeysView, 'status' | 'owner' | 'search'>>): AdminKeysView {
	return { ...view, ...patch, page: 1 };
}

export function goToPage(view: AdminKeysView, page: number, pages: number): AdminKeysView {
	return { ...view, page: Math.min(Math.max(1, Math.floor(page) || 1), Math.max(1, pages)) };
}

export function listQuery(view: AdminKeysView, pageSize = ADMIN_KEYS_PAGE_SIZE): AdminApiKeysQuery {
	return { status: view.status, userId: view.owner, query: view.search, page: view.page, pageSize };
}

export function sameQuery(a: AdminKeysView, b: AdminKeysView): boolean {
	return a.status === b.status && a.owner === b.owner && a.page === b.page && a.search.trim() === b.search.trim();
}

export function totalPages(total: number, pageSize = ADMIN_KEYS_PAGE_SIZE): number {
	return Math.max(1, Math.ceil(total / pageSize));
}

export function rangeText(page: number, pageSize: number, shown: number, total: number): string {
	if (total === 0 || shown === 0) return '';
	const from = (page - 1) * pageSize + 1;
	return `${from}–${from + shown - 1} of ${total}`;
}

export function ownerName(key: Pick<AdminApiKey, 'owner'>): string {
	return key.owner?.username || 'Deleted user';
}

export function ownerInitials(name: string): string {
	const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
	if (parts.length === 0) return '?';
	if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
	return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export interface StatusItem {
	id: AdminKeyStatusFilter;
	label: string;
	count?: number;
}

export function statusItems(counts: AdminKeyCounts | null | undefined): StatusItem[] {
	return STATUS_FILTERS.map((filter) => ({ ...filter, ...(counts ? { count: counts[filter.id] } : {}) }));
}

export function isFiltered(view: AdminKeysView): boolean {
	return view.status !== 'all' || view.owner !== null || view.search.trim() !== '';
}

export function adminNeverExpires(key: Pick<AdminApiKey, 'scopes' | 'expires_at'>): boolean {
	return key.scopes.includes('admin') && !key.expires_at;
}

export function canDoText(key: Pick<AdminApiKey, 'scopes'>): string {
	const http = scopeLine(key.scopes);
	const mcp = key.scopes.includes('mcp');
	if (http && mcp) return `${http}, MCP`;
	if (http) return http;
	return mcp ? 'MCP only' : 'Nothing';
}

export function lastUsedText(key: Pick<AdminApiKey, 'last_used_at'>, now: Date = new Date()): string {
	const phrase = usedPhrase(key.last_used_at, now);
	if (phrase === 'never used') return 'Never';
	const rest = phrase.replace(/^used /, '');
	return rest.charAt(0).toUpperCase() + rest.slice(1);
}

export function expiresText(key: Pick<AdminApiKey, 'expires_at' | 'scopes'>, now: Date = new Date()): string {
	return adminNeverExpires(key) ? 'No expiry' : formatKeyDate(key.expires_at, now);
}

export function formatCreated(key: Pick<AdminApiKey, 'created_at'>, now: Date = new Date()): string {
	return formatKeyDate(key.created_at, now);
}

export function adminKeyBadge(key: AdminApiKey, now: Date = new Date()): KeyBadge | null {
	const badge = keyBadge(key, now);
	if (!badge) return key.status === 'active' && adminNeverExpires(key) ? { variant: 'warning', label: 'No expiry' } : null;
	if (badge.variant === 'warning') return { variant: 'warning', label: 'Expiring' };
	return badge;
}

export function keyTitle(key: AdminApiKey): string {
	return `${key.name} (${keyFingerprint(key)})`;
}

export function revokeCopy(key: Pick<AdminApiKey, 'name' | 'owner'>): { title: string; message: string; confirmLabel: string } {
	const owner = ownerName(key);
	return {
		title: `Revoke “${key.name}” for ${owner}?`,
		message: `This key stops working right away for ${owner}, open connections included. They will see it as revoked by an administrator and can’t undo it. The revoke is recorded in the key’s history.`,
		confirmLabel: 'Revoke key'
	};
}

export function describeAdminEvent(event: ApiKeyEvent, owner: string, now: Date = new Date()): ActivityLine {
	const line = describeEvent(event, now);
	const actor = event.actor?.username ?? null;
	switch (event.action) {
		case 'created':
			return { ...line, detail: `By ${actor ?? owner}` };
		case 'revoked':
			return { ...line, detail: event.detail?.by_owner === false ? `By administrator ${actor ?? ''}`.trim() : `By ${owner}` };
		case 'mcp_tools_changed':
			return { ...line, what: 'MCP tools changed', detail: `By ${actor ?? owner}` };
		case 'expired':
			return line;
		default:
			return { ...line, detail: [line.detail, actor ? `By ${actor}` : ''].filter(Boolean).join(' · ') };
	}
}

export function describeAdminEvents(events: readonly ApiKeyEvent[], owner: string, now: Date = new Date()): ActivityLine[] {
	const newestFirst = [...events].sort((a, b) => (parseServerDate(b.created_at)?.getTime() ?? 0) - (parseServerDate(a.created_at)?.getTime() ?? 0));
	return newestFirst.slice(0, ADMIN_EVENTS_LIMIT).map((event) => describeAdminEvent(event, owner, now));
}

export function revokable(key: Pick<AdminApiKey, 'status'>): boolean {
	return key.status === 'active';
}
