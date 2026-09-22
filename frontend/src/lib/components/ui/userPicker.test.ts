import { describe, it, expect } from 'vitest';
import {
	filterUsers,
	isAllMode,
	isUserSelected,
	toggleUserSelection,
	clearSelection,
	setAllMode,
	selectedCount,
	moveActiveIndex
} from './userPicker';

const users = [
	{ id: 'u1', username: 'alice', email: 'alice@example.com' },
	{ id: 'u2', username: 'bob', email: 'bob@example.com' },
	{ id: 'u3', username: 'carol', email: 'carol@other.org' }
];

describe('filterUsers', () => {
	it('returns everyone for a blank query', () => {
		expect(filterUsers(users, '')).toEqual(users);
		expect(filterUsers(users, '   ')).toEqual(users);
	});

	it('matches by username, case-insensitively', () => {
		expect(filterUsers(users, 'ALICE')).toEqual([users[0]]);
	});

	it('matches by email', () => {
		expect(filterUsers(users, 'other.org')).toEqual([users[2]]);
	});

	it('tolerates users with no email', () => {
		expect(filterUsers([{ id: 'u4', username: 'dave' }], 'dave')).toEqual([{ id: 'u4', username: 'dave' }]);
	});
});

describe('isAllMode', () => {
	it('is true only for the "all" sentinel', () => {
		expect(isAllMode('all')).toBe(true);
		expect(isAllMode([])).toBe(false);
		expect(isAllMode(['u1'])).toBe(false);
	});
});

describe('isUserSelected', () => {
	it('checks membership in the selected id list', () => {
		expect(isUserSelected(['u1', 'u2'], 'u1')).toBe(true);
		expect(isUserSelected(['u1', 'u2'], 'u3')).toBe(false);
	});

	it('is false when in "all" mode', () => {
		expect(isUserSelected('all', 'u1')).toBe(false);
	});
});

describe('toggleUserSelection', () => {
	it('adds and removes immutably', () => {
		const added = toggleUserSelection(['u1'], 'u2');
		expect(added).toEqual(['u1', 'u2']);
		expect(toggleUserSelection(added, 'u1')).toEqual(['u2']);
	});

	it('treats "all" mode as an empty starting list', () => {
		expect(toggleUserSelection('all', 'u1')).toEqual(['u1']);
	});
});

describe('clearSelection / setAllMode', () => {
	it('clearSelection returns an empty list', () => {
		expect(clearSelection()).toEqual([]);
	});

	it('setAllMode toggles between "all" and empty', () => {
		expect(setAllMode(true)).toBe('all');
		expect(setAllMode(false)).toEqual([]);
	});
});

describe('selectedCount', () => {
	it('counts the selected id list', () => {
		expect(selectedCount(['u1', 'u2'], 5)).toBe(2);
	});

	it('counts every user in "all" mode', () => {
		expect(selectedCount('all', 5)).toBe(5);
	});
});

describe('moveActiveIndex', () => {
	it('wraps forward past the end', () => {
		expect(moveActiveIndex(2, 1, 3)).toBe(0);
	});

	it('wraps backward past the start', () => {
		expect(moveActiveIndex(0, -1, 3)).toBe(2);
	});

	it('returns -1 for an empty list', () => {
		expect(moveActiveIndex(0, 1, 0)).toBe(-1);
	});
});
