import { describe, it, expect } from 'vitest';
import { accountTypeCounts, deletableGroupIds, deletableUserIds } from './usersLibrary';
import type { User } from '$lib/stores/auth';
import type { UserGroup } from '$lib/services/admin-api';

function makeUser(overrides: Partial<User>): User {
	return {
		id: 'u1',
		username: 'user',
		email: 'user@example.com',
		account_type: 'USER',
		created_at: null,
		last_login: null,
		avatar_url: null,
		has_local_password: true,
		...overrides
	};
}

function makeGroup(overrides: Partial<UserGroup>): UserGroup {
	return {
		id: 'g1',
		name: 'group',
		is_system: false,
		...overrides
	};
}

describe('accountTypeCounts', () => {
	it('counts admins and regular users separately', () => {
		const users = [
			makeUser({ id: '1', account_type: 'ADMIN' }),
			makeUser({ id: '2', account_type: 'USER' }),
			makeUser({ id: '3', account_type: 'USER' })
		];
		expect(accountTypeCounts(users)).toEqual({ ADMIN: 1, USER: 2 });
	});

	it('returns zero counts for an empty list', () => {
		expect(accountTypeCounts([])).toEqual({ ADMIN: 0, USER: 0 });
	});
});

describe('deletableGroupIds', () => {
	it('excludes built-in groups from the selected set', () => {
		const groups = [makeGroup({ id: 'a', is_system: true }), makeGroup({ id: 'b', is_system: false }), makeGroup({ id: 'c', is_system: false })];
		const result = deletableGroupIds(groups, new Set(['a', 'b', 'c']));
		expect(result.deletableIds.sort()).toEqual(['b', 'c']);
		expect(result.skippedCount).toBe(1);
	});

	it('ignores groups outside the selection', () => {
		const groups = [makeGroup({ id: 'a', is_system: false }), makeGroup({ id: 'b', is_system: false })];
		const result = deletableGroupIds(groups, new Set(['a']));
		expect(result.deletableIds).toEqual(['a']);
		expect(result.skippedCount).toBe(0);
	});
});

describe('deletableUserIds', () => {
	it('excludes the current user from a bulk delete', () => {
		const result = deletableUserIds(new Set(['1', '2', '3']), '2');
		expect(result.deletableIds.sort()).toEqual(['1', '3']);
		expect(result.skippedCount).toBe(1);
	});

	it('keeps every id when the current user is not selected', () => {
		const result = deletableUserIds(new Set(['1', '3']), '2');
		expect(result.deletableIds.sort()).toEqual(['1', '3']);
		expect(result.skippedCount).toBe(0);
	});
});
