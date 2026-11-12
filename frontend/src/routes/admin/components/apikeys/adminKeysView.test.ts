import { describe, it, expect } from 'vitest';
import { makeAdminKey, NOW } from '$lib/components/apikeys/fixtures';
import {
	ADMIN_EVENTS_LIMIT,
	INITIAL_VIEW,
	adminKeyBadge,
	canDoText,
	changeView,
	describeAdminEvent,
	describeAdminEvents,
	expiresText,
	goToPage,
	lastUsedText,
	listQuery,
	ownerInitials,
	ownerName,
	rangeText,
	revokeCopy,
	revokable,
	sameQuery,
	statusItems,
	isFiltered,
	adminNeverExpires,
	totalPages
} from './adminKeysView';

describe('view state', () => {
	it('sends the status, owner, page and page size to the list route', () => {
		const view = { ...INITIAL_VIEW, status: 'revoked' as const, owner: 'u9', page: 3 };
		expect(listQuery(view)).toEqual({ status: 'revoked', userId: 'u9', query: '', page: 3, pageSize: 10 });
	});

	it('returns to page 1 when a filter changes, never when the page does', () => {
		const onThree = { ...INITIAL_VIEW, page: 3 };
		expect(changeView(onThree, { status: 'active' }).page).toBe(1);
		expect(changeView(onThree, { owner: 'u2' }).page).toBe(1);
		expect(goToPage(onThree, 4, 5).page).toBe(4);
	});

	it('clamps a requested page into the available range', () => {
		expect(goToPage(INITIAL_VIEW, 99, 5).page).toBe(5);
		expect(goToPage(INITIAL_VIEW, 0, 5).page).toBe(1);
		expect(goToPage(INITIAL_VIEW, Number.NaN, 5).page).toBe(1);
	});

	it('tells a page change apart from no change', () => {
		expect(sameQuery(INITIAL_VIEW, { ...INITIAL_VIEW })).toBe(true);
		expect(sameQuery(INITIAL_VIEW, { ...INITIAL_VIEW, page: 2 })).toBe(false);
	});

	it('counts pages and words the range', () => {
		expect(totalPages(0)).toBe(1);
		expect(totalPages(47)).toBe(5);
		expect(rangeText(1, 10, 10, 47)).toBe('1–10 of 47');
		expect(rangeText(5, 10, 7, 47)).toBe('41–47 of 47');
		expect(rangeText(1, 10, 0, 0)).toBe('');
	});
});

describe('search and counts', () => {
	it('sends the search text to the list route, trimmed in the service, and returns to page 1', () => {
		const view = changeView({ ...INITIAL_VIEW, page: 4 }, { search: 'raycast' });
		expect(view.page).toBe(1);
		expect(listQuery(view)).toEqual({ status: 'all', userId: null, query: 'raycast', page: 1, pageSize: 10 });
	});

	it('asks for a new page only when the search text really changed', () => {
		expect(sameQuery(INITIAL_VIEW, { ...INITIAL_VIEW, search: '  ' })).toBe(true);
		expect(sameQuery(INITIAL_VIEW, { ...INITIAL_VIEW, search: 'abc' })).toBe(false);
	});

	it('puts a count on every status chip when the server sent them, and none otherwise', () => {
		const items = statusItems({ all: 47, active: 34, expired: 8, revoked: 5 });
		expect(items.map((item) => [item.label, item.count])).toEqual([['All', 47], ['Active', 34], ['Expired', 8], ['Revoked', 5]]);
		expect(statusItems(undefined).every((item) => item.count === undefined)).toBe(true);
	});

	it('knows when anything narrows the list', () => {
		expect(isFiltered(INITIAL_VIEW)).toBe(false);
		expect(isFiltered({ ...INITIAL_VIEW, search: 'x' })).toBe(true);
		expect(isFiltered({ ...INITIAL_VIEW, owner: 'u1' })).toBe(true);
		expect(isFiltered({ ...INITIAL_VIEW, status: 'revoked' })).toBe(true);
	});
});

describe('admin keys that never expire', () => {
	const never = makeAdminKey({ scopes: ['read', 'generate', 'content', 'admin'], expires_at: null });

	it('is not shown as Never and carries a flag', () => {
		expect(adminNeverExpires(never)).toBe(true);
		expect(expiresText(never, NOW)).toBe('No expiry');
		expect(adminKeyBadge(never, NOW)).toEqual({ variant: 'warning', label: 'No expiry' });
	});

	it('shows an ordinary never-expiring key as Never with no flag', () => {
		const ordinary = makeAdminKey({ scopes: ['read'], expires_at: null });
		expect(adminNeverExpires(ordinary)).toBe(false);
		expect(expiresText(ordinary, NOW)).toBe('Never');
		expect(adminKeyBadge(ordinary, NOW)).toBeNull();
	});

	it('shows an admin key with an expiry as a date', () => {
		const capped = makeAdminKey({ scopes: ['admin'], expires_at: '2027-02-09T09:00:00Z' });
		expect(adminNeverExpires(capped)).toBe(false);
		expect(expiresText(capped, NOW)).toBe('Feb 9, 2027');
	});

	it('keeps Revoked as the badge of a revoked admin key but still not Never', () => {
		const revoked = { ...never, status: 'revoked' as const };
		expect(adminKeyBadge(revoked, NOW)?.label).toBe('Revoked');
		expect(expiresText(revoked, NOW)).toBe('No expiry');
	});
});

describe('cells', () => {
	it('words what a key can do', () => {
		expect(canDoText({ scopes: ['read', 'generate'] })).toBe('Read, Generate');
		expect(canDoText({ scopes: ['read', 'mcp'] })).toBe('Read, MCP');
		expect(canDoText({ scopes: ['mcp'] })).toBe('MCP only');
		expect(canDoText({ scopes: ['read', 'admin'] })).toBe('Full administration');
	});

	it('words last use and expiry', () => {
		expect(lastUsedText({ last_used_at: null }, NOW)).toBe('Never');
		expect(lastUsedText({ last_used_at: '2026-11-11T08:00:00Z' }, NOW)).toBe('Today');
		expect(lastUsedText({ last_used_at: '2026-11-10T08:00:00Z' }, NOW)).toBe('Yesterday');
		expect(expiresText({ expires_at: null, scopes: ['read'] }, NOW)).toBe('Never');
		expect(expiresText({ expires_at: '2027-02-09T09:00:00Z', scopes: ['read'] }, NOW)).toBe('Feb 9, 2027');
	});

	it('shows Expiring, Expired and Revoked, and nothing for an ordinary key', () => {
		expect(adminKeyBadge(makeAdminKey(), NOW)).toBeNull();
		expect(adminKeyBadge(makeAdminKey({ expires_at: '2026-11-14T12:00:00Z' }), NOW)).toEqual({ variant: 'warning', label: 'Expiring' });
		expect(adminKeyBadge(makeAdminKey({ status: 'expired' }), NOW)?.label).toBe('Expired');
		expect(adminKeyBadge(makeAdminKey({ status: 'revoked' }), NOW)?.label).toBe('Revoked');
	});

	it('names the owner, with a fallback for a deleted user', () => {
		expect(ownerName(makeAdminKey())).toBe('Marta Kowalska');
		expect(ownerName(makeAdminKey({ owner: { id: 'x', username: null } }))).toBe('Deleted user');
		expect(ownerInitials('Marta Kowalska')).toBe('MK');
		expect(ownerInitials('ops')).toBe('OP');
		expect(ownerInitials('')).toBe('?');
	});

	it('offers revoke only on an active key', () => {
		expect(revokable({ status: 'active' })).toBe(true);
		expect(revokable({ status: 'expired' })).toBe(false);
		expect(revokable({ status: 'revoked' })).toBe(false);
	});
});

describe('revoke confirm copy', () => {
	it('names the key and its owner and says what happens', () => {
		const copy = revokeCopy(makeAdminKey({ name: 'Home Assistant' }));
		expect(copy.title).toBe('Revoke “Home Assistant” for Marta Kowalska?');
		expect(copy.message).toContain('stops working right away for Marta Kowalska');
		expect(copy.message).toContain('open connections');
		expect(copy.message).toContain('revoked by an administrator');
	});
});

describe('history', () => {
	const event = (action: string, extra: Record<string, unknown> = {}) => ({
		action,
		actor: { id: 'a1', username: 'Priya Nair' },
		detail: {},
		changes: null,
		created_at: '2026-11-09T10:00:00Z',
		...extra
	});

	it('says an administrator revoked it, and by whom', () => {
		expect(describeAdminEvent(event('revoked', { detail: { by_owner: false } }), 'Marta', NOW)).toMatchObject({ what: 'Revoked', detail: 'By administrator Priya Nair' });
		expect(describeAdminEvent(event('revoked', { detail: { by_owner: true } }), 'Marta', NOW).detail).toBe('By Marta');
	});

	it('names the actor on created and tool changes', () => {
		expect(describeAdminEvent(event('created', { actor: { id: 'u1', username: 'Marta' } }), 'Marta', NOW).detail).toBe('By Marta');
		expect(describeAdminEvent(event('mcp_tools_changed'), 'Marta', NOW)).toMatchObject({ what: 'MCP tools changed', detail: 'By Priya Nair' });
	});

	it('keeps the access change and adds who made it', () => {
		const changed = event('scopes_changed', { changes: { scopes: { added: ['generate'], removed: [] } } });
		expect(describeAdminEvent(changed, 'Marta', NOW).detail).toBe('Can generate added · By Priya Nair');
	});

	it('lists newest first and stops at the limit', () => {
		const events = Array.from({ length: 30 }, (_, i) => event('renamed', { created_at: `2026-10-${String((i % 28) + 1).padStart(2, '0')}T10:00:00Z` }));
		const lines = describeAdminEvents(events, 'Marta', NOW);
		expect(lines).toHaveLength(ADMIN_EVENTS_LIMIT);
		const first = describeAdminEvents([event('created', { created_at: '2026-08-20T10:00:00Z' }), event('revoked', { created_at: '2026-11-09T10:00:00Z' })], 'Marta', NOW);
		expect(first.map((line) => line.what)).toEqual(['Revoked', 'Created']);
	});
});
