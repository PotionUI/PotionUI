import { describe, it, expect } from 'vitest';
import {
	EXPIRED_PRUNE_MS,
	MAX_ACCOUNTS,
	activeAccount,
	emptyRegistry,
	findByToken,
	isExpired,
	jwtExpiryMs,
	markExpired,
	normalizeRegistry,
	parseRegistry,
	removeAccount,
	setActive,
	upsertAccount,
	type AccountProfile,
	type AccountRegistry
} from './accountRegistry';

const NOW = 1_800_000_000_000;

function jwt(expSeconds: number): string {
	const body = Buffer.from(JSON.stringify({ sub: 'x', exp: expSeconds })).toString('base64url');
	return `h.${body}.s`;
}

function profile(n: number): AccountProfile {
	return { userId: `u${n}`, username: `user${n}`, role: 'USER', avatar: null };
}

function registryWith(count: number): AccountRegistry {
	let registry = emptyRegistry();
	for (let i = 1; i <= count; i++) {
		registry = upsertAccount(registry, profile(i), jwt(NOW / 1000 + 3600), NOW).registry;
	}
	return registry;
}

describe('jwtExpiryMs', () => {
	it('reads exp in milliseconds', () => {
		expect(jwtExpiryMs(jwt(1234))).toBe(1_234_000);
	});

	it('returns null for tokens it cannot decode', () => {
		expect(jwtExpiryMs('not-a-jwt')).toBeNull();
		expect(jwtExpiryMs('a.%%%.c')).toBeNull();
	});
});

describe('upsertAccount', () => {
	it('adds a new account', () => {
		const { registry, outcome } = upsertAccount(emptyRegistry(), profile(1), 'tok', NOW);
		expect(outcome).toBe('added');
		expect(registry.accounts).toHaveLength(1);
		expect(registry.accounts[0]).toMatchObject({ userId: 'u1', token: 'tok', signedInAt: NOW, expired: false });
	});

	it('replaces the token of a known account in place instead of duplicating it', () => {
		const first = upsertAccount(emptyRegistry(), profile(1), 'old', NOW).registry;
		const { registry, outcome } = upsertAccount(first, profile(1), 'new', NOW + 5);
		expect(outcome).toBe('replaced');
		expect(registry.accounts).toHaveLength(1);
		expect(registry.accounts[0].token).toBe('new');
		expect(registry.accounts[0].signedInAt).toBe(NOW + 5);
	});

	it('clears the expired flag when an expired account signs in again', () => {
		const added = upsertAccount(emptyRegistry(), profile(1), 'old', NOW).registry;
		const expired = markExpired(added, 'u1', NOW);
		expect(expired.accounts[0].expired).toBe(true);
		const { registry } = upsertAccount(expired, profile(1), 'new', NOW + 1);
		expect(registry.accounts[0].expired).toBe(false);
	});

	it('refuses a sixth account', () => {
		const full = registryWith(MAX_ACCOUNTS);
		const { registry, outcome } = upsertAccount(full, profile(6), 'tok', NOW);
		expect(outcome).toBe('full');
		expect(registry.accounts).toHaveLength(MAX_ACCOUNTS);
		expect(registry.accounts.some((a) => a.userId === 'u6')).toBe(false);
	});

	it('still lets a full registry refresh a known account', () => {
		const full = registryWith(MAX_ACCOUNTS);
		const { outcome } = upsertAccount(full, profile(3), 'fresh', NOW);
		expect(outcome).toBe('replaced');
	});
});

describe('removeAccount and setActive', () => {
	it('removes only the named account', () => {
		const registry = registryWith(3);
		const next = removeAccount(registry, 'u2');
		expect(next.accounts.map((a) => a.userId)).toEqual(['u1', 'u3']);
	});

	it('clears activeId when the active account is removed, keeps it otherwise', () => {
		const registry = setActive(registryWith(3), 'u2');
		expect(removeAccount(registry, 'u2').activeId).toBeNull();
		expect(removeAccount(registry, 'u3').activeId).toBe('u2');
	});

	it('resolves the active account', () => {
		const registry = setActive(registryWith(2), 'u2');
		expect(activeAccount(registry)?.userId).toBe('u2');
		expect(activeAccount(emptyRegistry())).toBeNull();
	});
});

describe('expiry', () => {
	it('markExpired flags only the named account', () => {
		const registry = markExpired(registryWith(3), 'u2', NOW);
		expect(registry.accounts.map((a) => a.expired)).toEqual([false, true, false]);
	});

	it('isExpired honours both the flag and the JWT exp', () => {
		const live = upsertAccount(emptyRegistry(), profile(1), jwt(NOW / 1000 + 60), NOW).registry.accounts[0];
		expect(isExpired(live, NOW)).toBe(false);
		expect(isExpired(live, NOW + 61_000)).toBe(true);
		expect(isExpired({ ...live, expired: true }, NOW)).toBe(true);
	});

	it('normalizeRegistry flags an account whose JWT has lapsed and keeps it', () => {
		const registry = upsertAccount(emptyRegistry(), profile(1), jwt(NOW / 1000 - 10), NOW - 100_000).registry;
		const next = normalizeRegistry(registry, NOW);
		expect(next.accounts).toHaveLength(1);
		expect(next.accounts[0].expired).toBe(true);
	});

	it('prunes an account that has been expired for more than 7 days', () => {
		const expiredAt = NOW - EXPIRED_PRUNE_MS - 1;
		const registry = upsertAccount(emptyRegistry(), profile(1), jwt(expiredAt / 1000), NOW - 2 * EXPIRED_PRUNE_MS).registry;
		expect(normalizeRegistry(registry, NOW).accounts).toHaveLength(0);
	});

	it('keeps an account that expired less than 7 days ago', () => {
		const expiredAt = NOW - EXPIRED_PRUNE_MS + 60_000;
		const registry = upsertAccount(emptyRegistry(), profile(1), jwt(expiredAt / 1000), NOW - 2 * EXPIRED_PRUNE_MS).registry;
		const next = normalizeRegistry(registry, NOW);
		expect(next.accounts).toHaveLength(1);
		expect(next.accounts[0].expired).toBe(true);
	});

	it('prunes a 401-expired account on the same 7 day clock', () => {
		const registry = markExpired(registryWith(2), 'u1', NOW - EXPIRED_PRUNE_MS - 1);
		const next = normalizeRegistry(registry, NOW);
		expect(next.accounts.map((a) => a.userId)).toEqual(['u2']);
	});

	it('drops activeId when the active account was pruned', () => {
		const registry = setActive(markExpired(registryWith(2), 'u1', NOW - EXPIRED_PRUNE_MS - 1), 'u1');
		expect(normalizeRegistry(registry, NOW).activeId).toBeNull();
	});
});

describe('findByToken', () => {
	it('finds the owner of a token and nothing for unknown or empty tokens', () => {
		const registry = upsertAccount(registryWith(1), profile(2), 'special', NOW).registry;
		expect(findByToken(registry, 'special')?.userId).toBe('u2');
		expect(findByToken(registry, 'other')).toBeNull();
		expect(findByToken(registry, null)).toBeNull();
	});
});

describe('parseRegistry', () => {
	it('returns an empty registry for garbage', () => {
		expect(parseRegistry(null)).toEqual(emptyRegistry());
		expect(parseRegistry('{')).toEqual(emptyRegistry());
		expect(parseRegistry('{"v":2,"accounts":[]}')).toEqual(emptyRegistry());
	});

	it('drops malformed entries and an activeId that points nowhere', () => {
		const raw = JSON.stringify({
			v: 1,
			activeId: 'ghost',
			accounts: [{ userId: 'u1', username: 'a', token: 't', role: 'USER', avatar: null, signedInAt: 1, expired: false }, { nope: true }]
		});
		const parsed = parseRegistry(raw, NOW);
		expect(parsed.accounts.map((a) => a.userId)).toEqual(['u1']);
		expect(parsed.activeId).toBeNull();
	});
});
