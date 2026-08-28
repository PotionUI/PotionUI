import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { LAST_USER_ID_KEY } from './identityScopedStorage';

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

	it('resets every identity-scoped store when a different user signs in', async () => {
		const { applyIdentityGuard, stores } = await freshGuardWithStores();
		const spies = Object.values(stores).map((store) => vi.spyOn(store, 'reset'));

		localStorage.setItem(LAST_USER_ID_KEY, 'user-a');
		applyIdentityGuard('user-b');

		for (const spy of spies) {
			expect(spy).toHaveBeenCalledTimes(1);
		}
		expect(localStorage.getItem(LAST_USER_ID_KEY)).toBe('user-b');
	});

	it('does not reset any store on a same-user relogin (e.g. after a token expiry)', async () => {
		const { applyIdentityGuard, stores } = await freshGuardWithStores();
		const spies = Object.values(stores).map((store) => vi.spyOn(store, 'reset'));

		localStorage.setItem(LAST_USER_ID_KEY, 'user-a');
		applyIdentityGuard('user-a');

		for (const spy of spies) {
			expect(spy).not.toHaveBeenCalled();
		}
	});

	it('does not reset any store on the very first login (no prior identity recorded)', async () => {
		const { applyIdentityGuard, stores } = await freshGuardWithStores();
		const spies = Object.values(stores).map((store) => vi.spyOn(store, 'reset'));

		applyIdentityGuard('user-a');

		for (const spy of spies) {
			expect(spy).not.toHaveBeenCalled();
		}
		expect(localStorage.getItem(LAST_USER_ID_KEY)).toBe('user-a');
	});
});
