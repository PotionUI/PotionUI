import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { TABS_STORAGE_KEY } from '$lib/types/tabs';
import { ACCOUNTS_STORAGE_KEY, EXPIRED_PRUNE_MS, MAX_ACCOUNTS } from './accountRegistry';

const NOW = Date.now();

function jwt(expSeconds: number): string {
	const body = Buffer.from(JSON.stringify({ exp: expSeconds })).toString('base64url');
	return `h.${body}.s`;
}

const LIVE = Math.floor(NOW / 1000) + 3600;

function tokenFor(id: string): string {
	return `${jwt(LIVE).slice(0, -1)}${id}`;
}

function user(id: string) {
	return {
		id,
		username: id,
		email: `${id}@x.test`,
		account_type: id === 'alice' ? ('ADMIN' as const) : ('USER' as const),
		created_at: null,
		last_login: null,
		avatar_url: null,
		has_local_password: true
	};
}

interface Seed {
	id: string;
	token?: string;
	expired?: boolean;
	expiredAt?: number;
	signedInAt?: number;
}

function seedRegistry(activeId: string | null, seeds: Seed[]) {
	localStorage.setItem(
		ACCOUNTS_STORAGE_KEY,
		JSON.stringify({
			v: 1,
			activeId,
			accounts: seeds.map((s) => ({
				userId: s.id,
				username: s.id,
				role: s.id === 'alice' ? 'ADMIN' : 'USER',
				avatar: null,
				token: s.token ?? tokenFor(s.id),
				signedInAt: s.signedInAt ?? NOW,
				expired: s.expired ?? false,
				expiredAt: s.expiredAt
			}))
		})
	);
}

function stubLocalStorage() {
	const store = new Map<string, string>();
	(globalThis as any).localStorage = {
		get length() {
			return store.size;
		},
		key: (i: number) => Array.from(store.keys())[i] ?? null,
		getItem: (key: string) => store.get(key) ?? null,
		setItem: (key: string, value: string) => void store.set(key, value),
		removeItem: (key: string) => void store.delete(key),
		clear: () => store.clear()
	};
	return store;
}

const apiMock = vi.hoisted(() => {
	const state = { header: null as string | null };
	return {
		state,
		api: {
			setAuthHeader: vi.fn((token: string) => {
				state.header = token;
				localStorage.setItem('auth_token', token);
			}),
			clearAuth: vi.fn(() => {
				state.header = null;
				localStorage.removeItem('auth_token');
			}),
			getToken: vi.fn(() => state.header),
			getCurrentUser: vi.fn(),
			login: vi.fn(),
			endMediaSession: vi.fn(async () => undefined)
		}
	};
});

vi.mock('$lib/services/api/index', () => ({ api: apiMock.api }));
vi.mock('$app/environment', () => ({ browser: true }));

const location = { reload: vi.fn(), assign: vi.fn(), pathname: '/generate' };

async function loadAccounts() {
	vi.resetModules();
	return import('./accounts');
}

function respondByToken(users: Record<string, ReturnType<typeof user>>) {
	apiMock.api.getCurrentUser.mockImplementation(async () => {
		const id = Object.keys(users).find((key) => tokenFor(key) === apiMock.state.header);
		if (!id) {
			const error: any = new Error('unauthorized');
			error.response = { status: 401 };
			throw error;
		}
		return { success: true, data: users[id] };
	});
}

beforeEach(() => {
	stubLocalStorage();
	location.reload.mockClear();
	location.assign.mockClear();
	location.pathname = '/generate';
	vi.stubGlobal('location', location);
	apiMock.state.header = null;
	for (const fn of Object.values(apiMock.api)) (fn as any).mockClear?.();
	apiMock.api.getCurrentUser.mockReset();
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.useRealTimers();
	delete (globalThis as any).localStorage;
});

describe('syncActive', () => {
	it('adds the signed in user, activates it and mirrors the token', async () => {
		const accounts = await loadAccounts();
		expect(accounts.syncActive(user('alice'), tokenFor('alice'))).toBe('added');

		const registry = accounts.currentRegistry();
		expect(registry.activeId).toBe('alice');
		expect(registry.accounts.map((a) => a.userId)).toEqual(['alice']);
		expect(localStorage.getItem('auth_token')).toBe(tokenFor('alice'));
		expect(JSON.parse(localStorage.getItem(ACCOUNTS_STORAGE_KEY)!).activeId).toBe('alice');
	});

	it('keeps several accounts and switches the active pointer', async () => {
		const accounts = await loadAccounts();
		accounts.syncActive(user('alice'), tokenFor('alice'));
		accounts.syncActive(user('bob'), tokenFor('bob'));

		const registry = accounts.currentRegistry();
		expect(registry.accounts.map((a) => a.userId)).toEqual(['alice', 'bob']);
		expect(registry.activeId).toBe('bob');
	});

	it('does not bump signedInAt when the same token is synced again', async () => {
		seedRegistry('alice', [{ id: 'alice', signedInAt: 1000 }]);
		const accounts = await loadAccounts();
		accounts.syncActive(user('alice'), tokenFor('alice'));
		expect(accounts.currentRegistry().accounts[0].signedInAt).toBe(1000);
	});

	it('replaces the token in place when a known user signs in with a new one', async () => {
		seedRegistry('alice', [{ id: 'alice', expired: true, expiredAt: NOW - 1000, token: 'stale' }]);
		const accounts = await loadAccounts();
		expect(accounts.syncActive(user('alice'), tokenFor('alice'))).toBe('replaced');

		const [only] = accounts.currentRegistry().accounts;
		expect(only.token).toBe(tokenFor('alice'));
		expect(only.expired).toBe(false);
		expect(accounts.currentRegistry().accounts).toHaveLength(1);
	});

	it('refuses a sixth account', async () => {
		seedRegistry('u1', ['u1', 'u2', 'u3', 'u4', 'u5'].slice(0, MAX_ACCOUNTS).map((id) => ({ id })));
		const accounts = await loadAccounts();
		expect(accounts.syncActive(user('u6'), tokenFor('u6'))).toBe('full');
		expect(accounts.currentRegistry().accounts).toHaveLength(MAX_ACCOUNTS);
		expect(accounts.currentRegistry().activeId).toBe('u1');
		expect(accounts.canAddAccount()).toBe(false);
		expect(accounts.canAddAccount('u3')).toBe(true);
	});
});

describe('switchTo', () => {
	it('verifies the target token, activates it and reloads, leaving the other account keys alone', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		localStorage.setItem(`${TABS_STORAGE_KEY}::alice`, 'alice-tabs');
		localStorage.setItem('unified-ai-chat-config-id::alice', 'cfg');
		respondByToken({ alice: user('alice'), bob: user('bob') });
		const accounts = await loadAccounts();

		const outcome = await accounts.switchTo('bob');

		expect(outcome).toEqual({ ok: true });
		expect(apiMock.api.setAuthHeader).toHaveBeenCalledWith(tokenFor('bob'));
		expect(apiMock.api.getCurrentUser).toHaveBeenCalledTimes(1);
		expect(accounts.currentRegistry().activeId).toBe('bob');
		expect(localStorage.getItem('auth_token')).toBe(tokenFor('bob'));
		expect(location.reload).toHaveBeenCalledTimes(1);
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::alice`)).toBe('alice-tabs');
		expect(localStorage.getItem('unified-ai-chat-config-id::alice')).toBe('cfg');
		expect(get(accounts.switchOverlay)).toMatchObject({ kind: 'initiator', username: 'bob', step: 'reloading' });
	});

	it('does not write the registry before the new token is verified', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const accounts = await loadAccounts();
		let activeDuringVerify: string | null = null;
		apiMock.api.getCurrentUser.mockImplementation(async () => {
			activeDuringVerify = JSON.parse(localStorage.getItem(ACCOUNTS_STORAGE_KEY)!).activeId;
			return { success: true, data: user('bob') };
		});

		await accounts.switchTo('bob');

		expect(activeDuringVerify).toBe('alice');
	});

	it('goes to /generate instead of reloading when started from the login page', async () => {
		seedRegistry(null, [{ id: 'bob' }]);
		respondByToken({ bob: user('bob') });
		location.pathname = '/login';
		const accounts = await loadAccounts();

		await accounts.switchTo('bob');

		expect(location.assign).toHaveBeenCalledWith('/generate');
		expect(location.reload).not.toHaveBeenCalled();
	});

	it('marks the target expired on a 401, restores the previous token and never reloads', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		apiMock.state.header = tokenFor('alice');
		respondByToken({ alice: user('alice') });
		const accounts = await loadAccounts();

		const outcome = await accounts.switchTo('bob');

		expect(outcome).toEqual({ ok: false, reason: 'expired' });
		const registry = accounts.currentRegistry();
		expect(registry.activeId).toBe('alice');
		expect(registry.accounts.find((a) => a.userId === 'bob')?.expired).toBe(true);
		expect(registry.accounts.find((a) => a.userId === 'alice')?.expired).toBe(false);
		expect(apiMock.api.setAuthHeader).toHaveBeenLastCalledWith(tokenFor('alice'));
		expect(location.reload).not.toHaveBeenCalled();
		expect(get(accounts.switchOverlay)).toBeNull();
		expect(accounts.isSwitching()).toBe(false);
	});

	it('keeps the target usable on a network failure but still restores the previous token', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		apiMock.api.getCurrentUser.mockRejectedValue(new Error('network'));
		const accounts = await loadAccounts();

		const outcome = await accounts.switchTo('bob');

		expect(outcome).toEqual({ ok: false, reason: 'failed' });
		expect(accounts.currentRegistry().accounts.find((a) => a.userId === 'bob')?.expired).toBe(false);
		expect(accounts.currentRegistry().activeId).toBe('alice');
		expect(apiMock.api.setAuthHeader).toHaveBeenLastCalledWith(tokenFor('alice'));
	});

	it('refuses an expired target without sending a request', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob', expired: true, expiredAt: NOW - 1000 }]);
		const accounts = await loadAccounts();

		expect(await accounts.switchTo('bob')).toEqual({ ok: false, reason: 'expired' });
		expect(apiMock.api.getCurrentUser).not.toHaveBeenCalled();
		expect(location.reload).not.toHaveBeenCalled();
	});

	it('reports an unknown account', async () => {
		const accounts = await loadAccounts();
		expect(await accounts.switchTo('ghost')).toEqual({ ok: false, reason: 'missing' });
	});

	it('refreshes the stored profile from /me', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		apiMock.api.getCurrentUser.mockResolvedValue({
			success: true,
			data: { ...user('bob'), username: 'bobby', avatar_url: '/a.png' }
		});
		const accounts = await loadAccounts();

		await accounts.switchTo('bob');

		const bob = accounts.currentRegistry().accounts.find((a) => a.userId === 'bob');
		expect(bob).toMatchObject({ username: 'bobby', avatar: '/a.png' });
	});
});

describe('handleUnauthorized', () => {
	it('marks only the active account when the failing request carried its token', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const accounts = await loadAccounts();

		expect(accounts.handleUnauthorized(tokenFor('alice'))).toBe('active');

		const registry = accounts.currentRegistry();
		expect(registry.accounts.map((a) => [a.userId, a.expired])).toEqual([
			['alice', true],
			['bob', false]
		]);
		expect(registry.activeId).toBe('alice');
	});

	it('marks only a non-active account and reports it as other when its token fails', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const accounts = await loadAccounts();

		expect(accounts.handleUnauthorized(tokenFor('bob'))).toBe('other');

		const registry = accounts.currentRegistry();
		expect(registry.accounts.map((a) => [a.userId, a.expired])).toEqual([
			['alice', false],
			['bob', true]
		]);
		expect(registry.activeId).toBe('alice');
	});

	it('treats an unrecognised token as the active account failing', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const accounts = await loadAccounts();

		expect(accounts.handleUnauthorized('mystery')).toBe('active');
		expect(accounts.currentRegistry().accounts.map((a) => a.expired)).toEqual([true, false]);
	});

	it('never auto-switches the active account', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const accounts = await loadAccounts();
		accounts.handleUnauthorized(tokenFor('alice'));
		expect(accounts.currentRegistry().activeId).toBe('alice');
		expect(location.reload).not.toHaveBeenCalled();
		expect(apiMock.api.setAuthHeader).not.toHaveBeenCalled();
	});
});

describe('removing accounts', () => {
	function seedKeys() {
		for (const id of ['alice', 'bob']) {
			localStorage.setItem(`${TABS_STORAGE_KEY}::${id}`, `${id}-tabs`);
			localStorage.setItem(`unified-ai-chat-enable-tools::${id}`, 'true');
		}
		localStorage.setItem('theme', 'dark');
	}

	it('logging out a non-active account deletes only its own keys', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		seedKeys();
		const accounts = await loadAccounts();

		accounts.removeAccount('bob');

		expect(accounts.currentRegistry().accounts.map((a) => a.userId)).toEqual(['alice']);
		expect(accounts.currentRegistry().activeId).toBe('alice');
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::bob`)).toBeNull();
		expect(localStorage.getItem('unified-ai-chat-enable-tools::bob')).toBeNull();
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::alice`)).toBe('alice-tabs');
		expect(localStorage.getItem('unified-ai-chat-enable-tools::alice')).toBe('true');
		expect(localStorage.getItem('theme')).toBe('dark');
	});

	it('logging out the active account leaves the others signed in and drops the token', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		seedKeys();
		localStorage.setItem('auth_token', tokenFor('alice'));
		localStorage.setItem('auth_last_user_id', 'alice');
		const accounts = await loadAccounts();

		accounts.removeAccount('alice');

		const registry = accounts.currentRegistry();
		expect(registry.accounts.map((a) => a.userId)).toEqual(['bob']);
		expect(registry.activeId).toBeNull();
		expect(localStorage.getItem('auth_token')).toBeNull();
		expect(localStorage.getItem('auth_last_user_id')).toBeNull();
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::alice`)).toBeNull();
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::bob`)).toBe('bob-tabs');
	});

	it('logging out of all clears every account, key and token but keeps device prefs', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		seedKeys();
		localStorage.setItem('auth_token', tokenFor('alice'));
		localStorage.setItem('auth_last_user_id', 'alice');
		localStorage.setItem('remember_me', 'true');
		const accounts = await loadAccounts();

		accounts.clearAllAccounts();

		expect(accounts.currentRegistry().accounts).toEqual([]);
		expect(localStorage.getItem(ACCOUNTS_STORAGE_KEY)).toBeNull();
		expect(localStorage.getItem('auth_token')).toBeNull();
		expect(localStorage.getItem('auth_last_user_id')).toBeNull();
		const left = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)).sort();
		expect(left).toEqual(['remember_me', 'theme']);
	});
});

describe('expiry and pruning', () => {
	it('prunes accounts expired for more than 7 days on load and deletes their keys', async () => {
		seedRegistry('alice', [
			{ id: 'alice' },
			{ id: 'old', expired: true, expiredAt: NOW - EXPIRED_PRUNE_MS - 60_000 },
			{ id: 'recent', expired: true, expiredAt: NOW - 60_000 }
		]);
		localStorage.setItem(`${TABS_STORAGE_KEY}::old`, 'old-tabs');
		localStorage.setItem(`${TABS_STORAGE_KEY}::recent`, 'recent-tabs');
		const accounts = await loadAccounts();

		expect(accounts.currentRegistry().accounts.map((a) => a.userId)).toEqual(['alice', 'recent']);
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::old`)).toBeNull();
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::recent`)).toBe('recent-tabs');
		expect(JSON.parse(localStorage.getItem(ACCOUNTS_STORAGE_KEY)!).accounts).toHaveLength(2);
	});

	it('flags an account whose JWT lapsed while the app was closed, without deleting it', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob', token: jwt(Math.floor(NOW / 1000) - 60) }]);
		const accounts = await loadAccounts();

		const bob = accounts.currentRegistry().accounts.find((a) => a.userId === 'bob');
		expect(bob?.expired).toBe(true);
	});

	it('refreshExpiry prunes and flags accounts while the app is open', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob', token: jwt(Math.floor(NOW / 1000) + 5) }]);
		localStorage.setItem(`${TABS_STORAGE_KEY}::bob`, 'bob-tabs');
		const accounts = await loadAccounts();
		expect(accounts.currentRegistry().accounts[1].expired).toBe(false);

		accounts.refreshExpiry(NOW + 6000);
		expect(accounts.currentRegistry().accounts[1].expired).toBe(true);

		accounts.refreshExpiry(NOW + 6000 + EXPIRED_PRUNE_MS + 1000);
		expect(accounts.currentRegistry().accounts.map((a) => a.userId)).toEqual(['alice']);
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::bob`)).toBeNull();
	});
});

describe('cross tab', () => {
	it('shows the follower overlay and reloads when another tab changes the active account', async () => {
		vi.useFakeTimers();
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const accounts = await loadAccounts();
		seedRegistry('bob', [{ id: 'alice' }, { id: 'bob' }]);

		accounts.handleStorageEvent({ key: ACCOUNTS_STORAGE_KEY, newValue: localStorage.getItem(ACCOUNTS_STORAGE_KEY) });

		expect(get(accounts.switchOverlay)).toMatchObject({ kind: 'follower', username: 'bob' });
		expect(location.reload).not.toHaveBeenCalled();
		vi.advanceTimersByTime(500);
		expect(location.reload).toHaveBeenCalledTimes(1);
	});

	it('follows a log out of all in another tab', async () => {
		vi.useFakeTimers();
		seedRegistry('alice', [{ id: 'alice' }]);
		const accounts = await loadAccounts();

		accounts.handleStorageEvent({ key: ACCOUNTS_STORAGE_KEY, newValue: null });
		vi.advanceTimersByTime(500);

		expect(location.reload).toHaveBeenCalledTimes(1);
	});

	it('updates the menu in place when another tab adds or removes a non-active account', async () => {
		vi.useFakeTimers();
		seedRegistry('alice', [{ id: 'alice' }]);
		const accounts = await loadAccounts();
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);

		accounts.handleStorageEvent({ key: ACCOUNTS_STORAGE_KEY, newValue: localStorage.getItem(ACCOUNTS_STORAGE_KEY) });
		vi.advanceTimersByTime(500);

		expect(get(accounts.accountsStore).accounts.map((a) => a.userId)).toEqual(['alice', 'bob']);
		expect(location.reload).not.toHaveBeenCalled();
		expect(get(accounts.switchOverlay)).toBeNull();
	});

	it('ignores unrelated storage keys', async () => {
		vi.useFakeTimers();
		seedRegistry('alice', [{ id: 'alice' }]);
		const accounts = await loadAccounts();

		accounts.handleStorageEvent({ key: 'theme', newValue: 'dark' });
		vi.advanceTimersByTime(500);

		expect(location.reload).not.toHaveBeenCalled();
	});
});

describe('adding accounts', () => {
	it('adds a new account, makes it active and navigates', async () => {
		seedRegistry('alice', [{ id: 'alice' }]);
		apiMock.state.header = tokenFor('alice');
		respondByToken({ alice: user('alice'), bob: user('bob') });
		const accounts = await loadAccounts();

		const outcome = await accounts.addAccountFromToken(tokenFor('bob'));

		expect(outcome).toEqual({ status: 'added', username: 'bob' });
		expect(accounts.currentRegistry().accounts.map((a) => a.userId)).toEqual(['alice', 'bob']);
		expect(accounts.currentRegistry().activeId).toBe('bob');
		expect(location.assign).toHaveBeenCalledWith('/generate');
	});

	it('reports a duplicate without adding it twice and keeps the current account', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		apiMock.state.header = tokenFor('alice');
		respondByToken({ alice: user('alice'), bob: user('bob') });
		const accounts = await loadAccounts();

		const outcome = await accounts.addAccountFromToken(tokenFor('bob'));

		expect(outcome).toEqual({ status: 'duplicate', username: 'bob', userId: 'bob' });
		expect(accounts.currentRegistry().accounts.filter((a) => a.userId === 'bob')).toHaveLength(1);
		expect(accounts.currentRegistry().activeId).toBe('alice');
		expect(location.assign).not.toHaveBeenCalled();
	});

	it('stayOnCurrentAccount puts the current account token and cookie back after a duplicate', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob' }]);
		apiMock.state.header = tokenFor('alice');
		respondByToken({ alice: user('alice'), bob: user('bob') });
		const accounts = await loadAccounts();
		await accounts.addAccountFromToken(tokenFor('bob'));
		apiMock.api.getCurrentUser.mockClear();

		await accounts.stayOnCurrentAccount();

		expect(apiMock.api.setAuthHeader).toHaveBeenLastCalledWith(tokenFor('alice'));
		expect(apiMock.api.getCurrentUser).toHaveBeenCalledTimes(1);
	});

	it('treats signing in again as an expired account as a refresh, not a duplicate', async () => {
		seedRegistry('alice', [{ id: 'alice' }, { id: 'bob', expired: true, expiredAt: NOW - 1000, token: 'dead' }]);
		apiMock.state.header = tokenFor('alice');
		respondByToken({ alice: user('alice'), bob: user('bob') });
		const accounts = await loadAccounts();

		const outcome = await accounts.addAccountFromToken(tokenFor('bob'));

		expect(outcome).toEqual({ status: 'refreshed', username: 'bob' });
		const bob = accounts.currentRegistry().accounts.find((a) => a.userId === 'bob');
		expect(bob).toMatchObject({ expired: false, token: tokenFor('bob') });
		expect(accounts.currentRegistry().activeId).toBe('alice');
		expect(apiMock.api.setAuthHeader).toHaveBeenLastCalledWith(tokenFor('alice'));
	});

	it('refuses a sixth account and restores the current one', async () => {
		seedRegistry('u1', ['u1', 'u2', 'u3', 'u4', 'u5'].map((id) => ({ id })));
		apiMock.state.header = tokenFor('u1');
		respondByToken({ u1: user('u1'), u6: user('u6') });
		const accounts = await loadAccounts();

		const outcome = await accounts.addAccountFromToken(tokenFor('u6'));

		expect(outcome).toEqual({ status: 'full' });
		expect(accounts.currentRegistry().accounts).toHaveLength(MAX_ACCOUNTS);
		expect(apiMock.api.setAuthHeader).toHaveBeenLastCalledWith(tokenFor('u1'));
	});

	it('ignores 401 handling while an add is verifying its token', async () => {
		seedRegistry('alice', [{ id: 'alice' }]);
		apiMock.state.header = tokenFor('alice');
		const accounts = await loadAccounts();
		let switchingDuringRequest = false;
		apiMock.api.getCurrentUser.mockImplementation(async () => {
			switchingDuringRequest = accounts.isSwitching();
			throw Object.assign(new Error('401'), { response: { status: 401 } });
		});

		const outcome = await accounts.addAccountFromToken('bad-token');

		expect(switchingDuringRequest).toBe(true);
		expect(outcome.status).toBe('error');
		expect(accounts.isSwitching()).toBe(false);
		expect(accounts.currentRegistry().accounts.map((a) => a.expired)).toEqual([false]);
	});

	it('addAccountWithPassword reports a failed login and restores the previous token', async () => {
		seedRegistry('alice', [{ id: 'alice' }]);
		apiMock.state.header = tokenFor('alice');
		apiMock.api.login.mockRejectedValue({ response: { data: { detail: 'Incorrect username or password' } } });
		const accounts = await loadAccounts();

		const outcome = await accounts.addAccountWithPassword('bob', 'nope', false);

		expect(outcome).toEqual({ status: 'error', message: 'Incorrect username or password' });
		expect(accounts.currentRegistry().accounts).toHaveLength(1);
	});

	it('addAccountWithPassword adds the account on a good login', async () => {
		seedRegistry('alice', [{ id: 'alice' }]);
		apiMock.state.header = tokenFor('alice');
		apiMock.api.login.mockImplementation(async () => {
			apiMock.state.header = tokenFor('bob');
			return { access_token: tokenFor('bob'), token_type: 'bearer' };
		});
		respondByToken({ alice: user('alice'), bob: user('bob') });
		const accounts = await loadAccounts();

		const outcome = await accounts.addAccountWithPassword('bob', 'pw', true);

		expect(outcome).toEqual({ status: 'added', username: 'bob' });
		expect(apiMock.api.login).toHaveBeenCalledWith({ username: 'bob', password: 'pw', remember_me: true });
		expect(accounts.currentRegistry().activeId).toBe('bob');
	});
});
