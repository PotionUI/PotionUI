import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { TABS_STORAGE_KEY } from '$lib/types/tabs';
import { LAST_USER_ID_KEY, identityKey } from './identityScopedStorage';

// applyIdentityGuard is exercised at the module boundary (not through a
// simulated login/logout, which would need mocking every store's own API
// surface) - each test imports a fresh module graph so per-module singleton
// state (e.g. nsfwFilter's one-shot init guard) doesn't leak between cases.
vi.mock('$lib/services/api/index', () => ({
	api: {
		getCurrentUser: vi.fn(),
		setAuthHeader: vi.fn(),
		setOnAuthExpired: vi.fn(),
		clearAuth: vi.fn(),
		endMediaSession: vi.fn(async () => undefined),
		getToken: vi.fn(() => null),
		login: vi.fn(),
		register: vi.fn(),
		getClient: vi.fn(() => ({ get: vi.fn(), put: vi.fn() })),
		getGenerationHistory: vi.fn(),
		getHistoryFacets: vi.fn(),
		getTags: vi.fn(),
		listLibraryItems: vi.fn(),
		getLibraryFacets: vi.fn(),
		getPhrasebookCategories: vi.fn(),
		getPhrasebookCategory: vi.fn(),
		listPresets: vi.fn(),
		getSessionsForPreset: vi.fn(),
		getPresetModes: vi.fn()
	}
}));

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

async function freshGuardWithStores() {
	vi.resetModules();
	const { applyIdentityGuard } = await import('./auth');
	const { tabsStore } = await import('./tabs');
	const { chatSession } = await import('./chatSession');
	const { chatComposerDrafts } = await import('./chatComposerDrafts');
	const { historyStore } = await import('./history');
	const { libraryStore } = await import('./library');
	const { phrasebookStore } = await import('./phrasebook');
	const { nsfwFilterStore } = await import('./nsfwFilter');
	const { previewGenerationStore } = await import('./previewGeneration');
	return {
		applyIdentityGuard,
		stores: {
			tabsStore,
			chatSession,
			chatComposerDrafts,
			historyStore,
			libraryStore,
			phrasebookStore,
			nsfwFilterStore,
			previewGenerationStore
		}
	};
}

describe('applyIdentityGuard', () => {
	beforeEach(() => {
		stubLocalStorage();
		vi.doMock('$app/environment', () => ({ browser: true }));
		vi.doMock('$app/navigation', () => ({ goto: vi.fn() }));
	});

	afterEach(() => {
		vi.doUnmock('$app/environment');
		vi.doUnmock('$app/navigation');
	});

	it('never resets an in-memory store or deletes stored state when a different user signs in', async () => {
		const { applyIdentityGuard, stores } = await freshGuardWithStores();
		const spies = Object.values(stores).map((store) => vi.spyOn(store, 'reset'));

		localStorage.setItem(LAST_USER_ID_KEY, 'user-a');
		localStorage.setItem(identityKey(TABS_STORAGE_KEY, 'user-a'), '{"tabs":[1]}');
		localStorage.setItem(identityKey(TABS_STORAGE_KEY, 'user-b'), '{"tabs":[2]}');
		applyIdentityGuard('user-b');

		for (const spy of spies) {
			expect(spy).not.toHaveBeenCalled();
		}
		expect(localStorage.getItem(identityKey(TABS_STORAGE_KEY, 'user-a'))).toBe('{"tabs":[1]}');
		expect(localStorage.getItem(identityKey(TABS_STORAGE_KEY, 'user-b'))).toBe('{"tabs":[2]}');
		expect(localStorage.getItem(LAST_USER_ID_KEY)).toBe('user-b');
	});

	it('records the identity on a same-user relogin and on the very first login', async () => {
		const { applyIdentityGuard } = await freshGuardWithStores();

		applyIdentityGuard('user-a');
		expect(localStorage.getItem(LAST_USER_ID_KEY)).toBe('user-a');

		applyIdentityGuard('user-a');
		expect(localStorage.getItem(LAST_USER_ID_KEY)).toBe('user-a');
	});
});

describe('authStore.logout', () => {
	beforeEach(() => {
		stubLocalStorage();
		vi.doMock('$app/environment', () => ({ browser: true }));
		vi.doMock('$app/navigation', () => ({ goto: vi.fn() }));
	});

	afterEach(() => {
		vi.doUnmock('$app/environment');
		vi.doUnmock('$app/navigation');
	});

	it('ends the server media session so media links stop loading in this browser', async () => {
		vi.resetModules();
		const { api } = await import('$lib/services/api/index');
		const { authStore } = await import('./auth');
		vi.mocked(api.endMediaSession).mockClear();

		authStore.logout();

		expect(api.endMediaSession).toHaveBeenCalledTimes(1);
	});
});
