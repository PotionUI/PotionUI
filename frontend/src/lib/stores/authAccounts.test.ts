import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { TABS_STORAGE_KEY } from '$lib/types/tabs';
import { ACCOUNTS_STORAGE_KEY } from './accountRegistry';

const hoisted = vi.hoisted(() => ({
	goto: vi.fn(),
	expiredHandler: { current: null as null | ((token: string | null) => void) },
	header: { current: null as string | null },
	userById: {} as Record<string, unknown>
}));

vi.mock('$app/environment', () => ({ browser: true }));
vi.mock('$app/navigation', () => ({ goto: hoisted.goto }));
vi.mock('$lib/services/api/index', () => ({
	api: {
		getCurrentUser: vi.fn(async () => ({
			success: true,
			data: hoisted.userById[(hoisted.header.current ?? '').replace('tok-', '')]
		})),
		setAuthHeader: vi.fn((token: string) => {
			hoisted.header.current = token;
			localStorage.setItem('auth_token', token);
		}),
		clearAuth: vi.fn(() => {
			hoisted.header.current = null;
			localStorage.removeItem('auth_token');
		}),
		setOnAuthExpired: vi.fn((cb: (token: string | null) => void) => {
			hoisted.expiredHandler.current = cb;
		}),
		endMediaSession: vi.fn(async () => undefined),
		getToken: vi.fn(() => hoisted.header.current),
		login: vi.fn(),
		register: vi.fn()
	}
}));

const location = { reload: vi.fn(), assign: vi.fn(), pathname: '/generate' };

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
}

function userRow(id: string) {
	return {
		id,
		username: id,
		email: '',
		account_type: 'USER',
		created_at: null,
		last_login: null,
		avatar_url: null,
		has_local_password: true
	};
}

function seed(activeId: string | null, ids: string[]) {
	localStorage.setItem(
		ACCOUNTS_STORAGE_KEY,
		JSON.stringify({
			v: 1,
			activeId,
			accounts: ids.map((id) => ({
				userId: id,
				username: id,
				role: 'USER',
				avatar: null,
				token: `tok-${id}`,
				signedInAt: Date.now(),
				expired: false
			}))
		})
	);
	for (const id of ids) localStorage.setItem(`${TABS_STORAGE_KEY}::${id}`, `${id}-tabs`);
	if (activeId) localStorage.setItem('auth_token', `tok-${activeId}`);
}

async function loadAuth() {
	vi.resetModules();
	const auth = await import('./auth');
	const accounts = await import('./accounts');
	const { api } = await import('$lib/services/api/index');
	await new Promise((r) => setTimeout(r, 0));
	return { ...auth, accounts, api };
}

beforeEach(() => {
	stubLocalStorage();
	hoisted.goto.mockClear();
	hoisted.header.current = null;
	hoisted.userById = { alice: userRow('alice'), bob: userRow('bob') };
	location.assign.mockClear();
	location.reload.mockClear();
	vi.stubGlobal('location', location);
});

afterEach(() => {
	vi.unstubAllGlobals();
	delete (globalThis as any).localStorage;
});

describe('authStore with several accounts', () => {
	it('boots authenticated from the registry active account', async () => {
		seed('alice', ['alice', 'bob']);
		const { authStore } = await loadAuth();

		const state = get(authStore);
		expect(state.isAuthenticated).toBe(true);
		expect(state.token).toBe('tok-alice');
		expect(state.user?.id).toBe('alice');
	});

	it('logout removes only the active account, keeps the others, and goes to the login page', async () => {
		seed('alice', ['alice', 'bob']);
		const { authStore, api, accounts } = await loadAuth();

		authStore.logout();

		expect(api.endMediaSession).toHaveBeenCalledTimes(1);
		expect(accounts.currentRegistry().accounts.map((a) => a.userId)).toEqual(['bob']);
		expect(accounts.currentRegistry().activeId).toBeNull();
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::alice`)).toBeNull();
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::bob`)).toBe('bob-tabs');
		expect(location.assign).toHaveBeenCalledWith('/login');
		expect(get(authStore).isAuthenticated).toBe(false);
	});

	it('logoutAccount on a non-active account touches neither the session nor the cookie', async () => {
		seed('alice', ['alice', 'bob']);
		const { authStore, api, accounts } = await loadAuth();
		vi.mocked(api.endMediaSession).mockClear();
		vi.mocked(api.clearAuth).mockClear();

		authStore.logoutAccount('bob');

		expect(accounts.currentRegistry().accounts.map((a) => a.userId)).toEqual(['alice']);
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::bob`)).toBeNull();
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::alice`)).toBe('alice-tabs');
		expect(api.endMediaSession).not.toHaveBeenCalled();
		expect(api.clearAuth).not.toHaveBeenCalled();
		expect(get(authStore).isAuthenticated).toBe(true);
		expect(location.assign).not.toHaveBeenCalled();
	});

	it('logoutAccount on the active account behaves like logout', async () => {
		seed('alice', ['alice', 'bob']);
		const { authStore, accounts } = await loadAuth();

		authStore.logoutAccount('alice');

		expect(accounts.currentRegistry().accounts.map((a) => a.userId)).toEqual(['bob']);
		expect(get(authStore).isAuthenticated).toBe(false);
	});

	it('logoutAll wipes every account, every namespaced key and the token', async () => {
		seed('alice', ['alice', 'bob']);
		localStorage.setItem('theme', 'dark');
		const { authStore, accounts } = await loadAuth();

		authStore.logoutAll();

		expect(accounts.currentRegistry().accounts).toEqual([]);
		expect(localStorage.getItem(ACCOUNTS_STORAGE_KEY)).toBeNull();
		expect(localStorage.getItem('auth_token')).toBeNull();
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::alice`)).toBeNull();
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::bob`)).toBeNull();
		expect(localStorage.getItem('theme')).toBe('dark');
		expect(location.assign).toHaveBeenCalledWith('/login');
	});

	it('a 401 on the active account goes to login with expired=1, keeps its tabs and does not switch', async () => {
		seed('alice', ['alice', 'bob']);
		const { authStore, accounts, api } = await loadAuth();
		vi.mocked(api.setAuthHeader).mockClear();

		hoisted.expiredHandler.current!('tok-alice');

		expect(hoisted.goto).toHaveBeenCalledWith('/login?expired=1');
		const registry = accounts.currentRegistry();
		expect(registry.accounts.map((a) => [a.userId, a.expired])).toEqual([
			['alice', true],
			['bob', false]
		]);
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::alice`)).toBe('alice-tabs');
		expect(get(authStore).isAuthenticated).toBe(false);
		expect(localStorage.getItem('auth_token')).toBeNull();
		expect(api.setAuthHeader).not.toHaveBeenCalled();
	});

	it('a 401 on a non-active account marks only that account and leaves the session alone', async () => {
		seed('alice', ['alice', 'bob']);
		const { authStore, accounts } = await loadAuth();

		hoisted.expiredHandler.current!('tok-bob');

		expect(hoisted.goto).not.toHaveBeenCalled();
		expect(accounts.currentRegistry().accounts.map((a) => [a.userId, a.expired])).toEqual([
			['alice', false],
			['bob', true]
		]);
		expect(get(authStore).isAuthenticated).toBe(true);
	});

	it('ignores 401s while an account switch is verifying its token', async () => {
		seed('alice', ['alice', 'bob']);
		const { accounts } = await loadAuth();
		let during: Promise<unknown> | null = null;
		const { api } = await import('$lib/services/api/index');
		vi.mocked(api.getCurrentUser).mockImplementationOnce(async () => {
			hoisted.expiredHandler.current!('tok-bob');
			return { success: true, data: userRow('bob') } as never;
		});

		during = accounts.switchTo('bob');
		await during;

		expect(hoisted.goto).not.toHaveBeenCalled();
		expect(accounts.currentRegistry().accounts.map((a) => a.expired)).toEqual([false, false]);
		expect(accounts.currentRegistry().activeId).toBe('bob');
	});

	it('upgrades a legacy single-token install into a one-account registry after /me', async () => {
		localStorage.setItem('auth_token', 'tok-alice');
		localStorage.setItem('auth_last_user_id', 'alice');
		const { accounts } = await loadAuth();

		expect(accounts.currentRegistry().activeId).toBe('alice');
		expect(accounts.currentRegistry().accounts.map((a) => a.userId)).toEqual(['alice']);
	});

	it('login of a sixth user is rejected and leaves the session unauthenticated', async () => {
		seed(null, ['u1', 'u2', 'u3', 'u4', 'u5']);
		hoisted.userById.u6 = userRow('u6');
		const { authStore, api } = await loadAuth();
		vi.mocked(api.login).mockImplementation(async () => {
			hoisted.header.current = 'tok-u6';
			return { access_token: 'tok-u6', token_type: 'bearer' } as never;
		});

		const result = await authStore.login('u6', 'pw');

		expect(result.success).toBe(false);
		expect(get(authStore).isAuthenticated).toBe(false);
	});

	it('finishSignIn reloads when the identity changed since boot and navigates softly otherwise', async () => {
		const { authStore, accounts } = await loadAuth();
		accounts.syncActive(userRow('alice') as never, 'tok-alice');
		authStore.finishSignIn('/generate');
		expect(location.assign).toHaveBeenCalledWith('/generate');
		expect(hoisted.goto).not.toHaveBeenCalled();

		location.assign.mockClear();
		seed('alice', ['alice']);
		const second = await loadAuth();
		second.authStore.finishSignIn('/generate');
		expect(location.assign).not.toHaveBeenCalled();
		expect(hoisted.goto).toHaveBeenCalledWith('/generate', { replaceState: true });
	});
});
