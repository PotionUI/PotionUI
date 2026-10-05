import { describe, it, expect } from 'vitest';
import {
	controlsDirty,
	controlsDraftFrom,
	controlsPayload,
	filterPeople,
	isForbidden,
	parseUserCap,
	replaceUser,
	userCapText,
	validateControls
} from './autoOrganizeAdmin';
import type { OrganizeAdminOverview, OrganizeAdminUser } from '$lib/types/organize';

const base = { default_rule_cap: 50, hourly_limit: 200 };

function user(overrides: Partial<OrganizeAdminUser> = {}): OrganizeAdminUser {
	return {
		user_id: 'u1',
		username: 'jan',
		rules: 4,
		enabled_rules: 3,
		paused_rules: 1,
		items_filed_24h: 120,
		items_filed_total: 2048,
		paused: false,
		rule_cap: null,
		effective_rule_cap: 50,
		last_run_at: null,
		...overrides
	};
}

describe('controls', () => {
	it('builds the draft from the overview', () => {
		expect(controlsDraftFrom(base)).toEqual({ defaultRuleCap: '50', hourlyLimit: '200' });
	});

	it('sends only the changed values', () => {
		expect(controlsPayload(base, { defaultRuleCap: '50', hourlyLimit: '300' })).toEqual({ hourly_limit: 300 });
		expect(controlsPayload(base, { defaultRuleCap: '0', hourlyLimit: '200' })).toEqual({ default_rule_cap: 0 });
	});

	it('is clean when nothing changed and dirty otherwise', () => {
		expect(controlsDirty(base, controlsDraftFrom(base))).toBe(false);
		expect(controlsDirty(base, { defaultRuleCap: '51', hourlyLimit: '200' })).toBe(true);
	});

	it('accepts the documented bounds', () => {
		expect(validateControls({ defaultRuleCap: '0', hourlyLimit: '1' })).toEqual({});
		expect(validateControls({ defaultRuleCap: '1000', hourlyLimit: '100000' })).toEqual({});
	});

	it('rejects out of range and non numeric values', () => {
		const errors = validateControls({ defaultRuleCap: '1001', hourlyLimit: '0' });
		expect(errors.defaultRuleCap).toBeTruthy();
		expect(errors.hourlyLimit).toBeTruthy();
		expect(validateControls({ defaultRuleCap: 'abc', hourlyLimit: '-3' })).toHaveProperty('defaultRuleCap');
		expect(validateControls({ defaultRuleCap: '5', hourlyLimit: '100001' })).toHaveProperty('hourlyLimit');
		expect(validateControls({ defaultRuleCap: '', hourlyLimit: '5' })).toHaveProperty('defaultRuleCap');
	});
});

describe('per person cap', () => {
	it('treats empty as the default', () => {
		expect(parseUserCap('  ')).toEqual({ ok: true, value: null });
	});

	it('parses a whole number including zero', () => {
		expect(parseUserCap('0')).toEqual({ ok: true, value: 0 });
		expect(parseUserCap('12')).toEqual({ ok: true, value: 12 });
	});

	it('rejects bad input', () => {
		expect(parseUserCap('1001').ok).toBe(false);
		expect(parseUserCap('2.5').ok).toBe(false);
	});

	it('shows an empty box for the default cap', () => {
		expect(userCapText(user())).toBe('');
		expect(userCapText(user({ rule_cap: 7 }))).toBe('7');
	});
});

describe('replaceUser and isForbidden', () => {
	it('swaps only the matching row', () => {
		const overview = { ...base, paused_all: false, totals: {} as never, users: [user(), user({ user_id: 'u2' })] } as OrganizeAdminOverview;
		const next = replaceUser(overview, user({ paused: true }));
		expect(next.users[0].paused).toBe(true);
		expect(next.users[1].paused).toBe(false);
	});

	it('detects a 403', () => {
		expect(isForbidden({ response: { status: 403 } })).toBe(true);
		expect(isForbidden({ response: { status: 500 } })).toBe(false);
		expect(isForbidden(null)).toBe(false);
	});
});

describe('filterPeople', () => {
	const users = [
		user({ user_id: 'a', username: 'zed', items_filed_24h: 5 }),
		user({ user_id: 'b', username: 'amy', items_filed_24h: 50 }),
		user({ user_id: 'c', username: 'Bob', items_filed_24h: 5 })
	];

	it('sorts by username', () => {
		expect(filterPeople(users, '', 'username').map((u) => u.user_id)).toEqual(['b', 'c', 'a']);
	});

	it('sorts by activity then name', () => {
		expect(filterPeople(users, '', 'activity').map((u) => u.user_id)).toEqual(['b', 'c', 'a']);
	});

	it('matches names case-insensitively', () => {
		expect(filterPeople(users, ' BO ', 'username').map((u) => u.user_id)).toEqual(['c']);
	});
});
