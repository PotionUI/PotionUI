import { describe, it, expect } from 'vitest';
import type { User } from '$lib/stores/auth';
import {
	DEFAULT_USERS_FILTERS,
	applyUsersFilters,
	clearAllUsersFilters,
	clearUsersFilterChip,
	usersFilterActiveCount,
	usersFilterChips,
	usersFiltersFromSearchParams,
	usersFiltersToSearchParams,
	type UsersFilters
} from './usersFilters';

function user(overrides: Partial<User> = {}): User {
	return {
		id: 'u1',
		username: 'alice',
		email: 'alice@example.com',
		account_type: 'USER',
		created_at: '2026-09-01T00:00:00.000Z',
		last_login: null,
		avatar_url: null,
		has_local_password: true,
		...overrides
	};
}

describe('applyUsersFilters', () => {
	const alice = user({ id: '1', username: 'alice', email: 'alice@example.com', account_type: 'USER', created_at: '2026-09-01T00:00:00.000Z' });
	const bob = user({ id: '2', username: 'bob', email: 'bob@example.com', account_type: 'ADMIN', created_at: '2026-09-03T00:00:00.000Z' });
	const carol = user({ id: '3', username: 'carol', email: 'carol@example.com', account_type: 'USER', created_at: '2026-09-02T00:00:00.000Z' });
	const all = [alice, bob, carol];

	it('passes everything through and sorts by username when filters are default', () => {
		expect(applyUsersFilters(all, DEFAULT_USERS_FILTERS).map((u) => u.id)).toEqual(['1', '2', '3']);
	});

	it('matches the search against username and email', () => {
		expect(applyUsersFilters(all, { ...DEFAULT_USERS_FILTERS, q: 'bob' }).map((u) => u.id)).toEqual(['2']);
		expect(applyUsersFilters(all, { ...DEFAULT_USERS_FILTERS, q: 'carol@example.com' }).map((u) => u.id)).toEqual(['3']);
	});

	it('filters by account type', () => {
		expect(applyUsersFilters(all, { ...DEFAULT_USERS_FILTERS, accountType: 'ADMIN' }).map((u) => u.id)).toEqual(['2']);
	});

	it('sorts by recently added when sortBy is created', () => {
		expect(applyUsersFilters(all, { ...DEFAULT_USERS_FILTERS, sortBy: 'created' }).map((u) => u.id)).toEqual(['2', '3', '1']);
	});

	it('does not mutate the list it was given', () => {
		const source = [...all];
		applyUsersFilters(source, { ...DEFAULT_USERS_FILTERS, sortBy: 'created' });
		expect(source.map((u) => u.id)).toEqual(all.map((u) => u.id));
	});
});

describe('usersFiltersFromSearchParams / usersFiltersToSearchParams', () => {
	it('round-trips a non-default filter set', () => {
		const filters: UsersFilters = { q: 'alice', accountType: 'ADMIN', sortBy: 'created' };
		const params = usersFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('alice');
		expect(params.get('account_type')).toBe('ADMIN');
		expect(params.get('sort_by')).toBe('created');
		expect(usersFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('serializes the default filters to an empty query string', () => {
		expect(usersFiltersToSearchParams(DEFAULT_USERS_FILTERS).toString()).toBe('');
	});

	it('falls back to defaults for unknown or missing params', () => {
		const params = new URLSearchParams('account_type=SUPERADMIN&sort_by=bogus');
		expect(usersFiltersFromSearchParams(params)).toEqual(DEFAULT_USERS_FILTERS);
	});
});

describe('users filter chips', () => {
	it('emits an account-type chip and clears it', () => {
		const filters: UsersFilters = { q: 'x', accountType: 'ADMIN', sortBy: 'username' };
		expect(usersFilterChips(filters)).toEqual([{ key: 'accountType', label: 'Administrators' }]);
		expect(clearUsersFilterChip(filters, 'accountType').accountType).toBe('all');
		expect(clearUsersFilterChip(filters, 'nothing')).toBe(filters);
	});

	it('emits no chips and zero active count for the default filters', () => {
		expect(usersFilterChips(DEFAULT_USERS_FILTERS)).toEqual([]);
		expect(usersFilterActiveCount(DEFAULT_USERS_FILTERS)).toBe(0);
	});

	it('counts account type as one active filter and keeps query and sort when clearing all', () => {
		const filters: UsersFilters = { q: 'keep me', accountType: 'USER', sortBy: 'created' };
		expect(usersFilterActiveCount(filters)).toBe(1);
		expect(clearAllUsersFilters(filters)).toEqual({ q: 'keep me', accountType: 'all', sortBy: 'created' });
	});
});
