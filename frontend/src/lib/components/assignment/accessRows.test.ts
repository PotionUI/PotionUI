import { describe, it, expect } from 'vitest';
import {
	toUserAccessRows,
	toGroupAccessRows,
	type AccessGroup,
	type AccessUser
} from './accessRows';

const users: AccessUser[] = [
	{ id: 'u1', username: 'zara', email: 'zara@example.com' },
	{ id: 'u2', username: 'ada', email: 'ada@studio.test', account_type: 'ADMIN' },
	{ id: 'u3', username: 'milo', email: null }
];

const groups: AccessGroup[] = [
	{ id: 'g1', name: 'Painters', description: 'Illustration team', member_count: 4 },
	{ id: 'g2', name: 'Animators', description: null },
	{ id: 'g3', name: 'Everyone', member_count: 0 }
];

describe('toUserAccessRows', () => {
	it('sorts assigned users first, then alphabetically', () => {
		const rows = toUserAccessRows(users, new Set(['u3']), '');
		expect(rows.map((r) => r.id)).toEqual(['u3', 'u2', 'u1']);
		expect(rows[0].assigned).toBe(true);
		expect(rows[1].assigned).toBe(false);
	});

	it('matches on username or email, case-insensitively', () => {
		expect(toUserAccessRows(users, new Set(), 'ZAR').map((r) => r.id)).toEqual(['u1']);
		expect(toUserAccessRows(users, new Set(), 'studio').map((r) => r.id)).toEqual(['u2']);
	});

	it('treats a whitespace-only query as no query', () => {
		expect(toUserAccessRows(users, new Set(), '   ')).toHaveLength(3);
	});

	it('survives a user with no email', () => {
		const rows = toUserAccessRows(users, new Set(), 'milo');
		expect(rows).toHaveLength(1);
		expect(rows[0].subtitle).toBe('');
	});

	it('badges admins only', () => {
		const rows = toUserAccessRows(users, new Set(), '');
		expect(rows.find((r) => r.id === 'u2')?.badge).toEqual({ variant: 'warning', text: 'admin' });
		expect(rows.find((r) => r.id === 'u1')?.badge).toBeNull();
	});

	it('keys rows by grant kind so user and group toggles cannot collide', () => {
		expect(toUserAccessRows(users, new Set(), '')[0].key).toMatch(/^user:/);
	});

	it('leaves the caller array untouched', () => {
		const order = users.map((u) => u.id);
		toUserAccessRows(users, new Set(['u3']), '');
		expect(users.map((u) => u.id)).toEqual(order);
	});
});

describe('toGroupAccessRows', () => {
	it('sorts assigned groups first, then alphabetically', () => {
		const rows = toGroupAccessRows(groups, new Set(['g1']), '');
		expect(rows.map((r) => r.id)).toEqual(['g1', 'g2', 'g3']);
	});

	it('matches on name or description', () => {
		expect(toGroupAccessRows(groups, new Set(), 'illustration').map((r) => r.id)).toEqual(['g1']);
		expect(toGroupAccessRows(groups, new Set(), 'anim').map((r) => r.id)).toEqual(['g2']);
	});

	it('badges a zero member count but not a missing one', () => {
		const rows = toGroupAccessRows(groups, new Set(), '');
		expect(rows.find((r) => r.id === 'g3')?.badge).toEqual({ variant: 'neutral', text: '0 members' });
		expect(rows.find((r) => r.id === 'g2')?.badge).toBeNull();
	});

	it('keys rows by grant kind', () => {
		expect(toGroupAccessRows(groups, new Set(), '')[0].key).toMatch(/^group:/);
	});
});
