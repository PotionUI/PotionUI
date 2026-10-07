import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { TABS_STORAGE_KEY } from '$lib/types/tabs';
import { ACCOUNTS_STORAGE_KEY } from './accountRegistry';

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

function registryJson(activeId: string | null, ids: string[]): string {
	return JSON.stringify({
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
	});
}

async function load() {
	vi.resetModules();
	return import('./identityScopedStorage');
}

const CHAT_KEYS = [
	TABS_STORAGE_KEY,
	'unified-ai-chat-session-id',
	'unified-ai-chat-config-id',
	'unified-ai-chat-pinned-tab',
	'unified-ai-chat-attach-image',
	'unified-ai-chat-enable-tools',
	'phrasebook-generation-config',
	'unified-ai-chat-disabled-tools:chat'
];

describe('identity key namespacing', () => {
	let store: Map<string, string>;

	beforeEach(() => {
		store = stubLocalStorage();
	});

	afterEach(() => {
		delete (globalThis as any).localStorage;
	});

	it('identityKey joins the base and the user id', async () => {
		const { identityKey } = await load();
		expect(identityKey(TABS_STORAGE_KEY, 'u1')).toBe('potionui_tabs_state::u1');
	});

	it('reads the active identity from the registry first, then auth_last_user_id, then anon', async () => {
		let mod = await load();
		expect(mod.readActiveIdentity()).toBe('anon');

		localStorage.setItem('auth_last_user_id', 'legacy-user');
		expect(mod.readActiveIdentity()).toBe('legacy-user');

		localStorage.setItem(ACCOUNTS_STORAGE_KEY, registryJson('u2', ['u1', 'u2']));
		mod = await load();
		expect(mod.readActiveIdentity()).toBe('u2');
	});

	it('writes every identity-scoped key under the active account namespace', async () => {
		localStorage.setItem(ACCOUNTS_STORAGE_KEY, registryJson('u1', ['u1']));
		const { scopedStorage } = await load();

		for (const key of CHAT_KEYS) scopedStorage.set(key, 'v');

		for (const key of CHAT_KEYS) {
			expect(store.get(`${key}::u1`)).toBe('v');
			expect(store.has(key)).toBe(false);
		}
	});

	it('keeps two accounts fully separate in one browser', async () => {
		localStorage.setItem(ACCOUNTS_STORAGE_KEY, registryJson('alice', ['alice', 'bob']));
		let mod = await load();
		mod.scopedStorage.set(TABS_STORAGE_KEY, 'alice-tabs');

		localStorage.setItem(ACCOUNTS_STORAGE_KEY, registryJson('bob', ['alice', 'bob']));
		mod = await load();
		expect(mod.scopedStorage.get(TABS_STORAGE_KEY)).toBeNull();
		mod.scopedStorage.set(TABS_STORAGE_KEY, 'bob-tabs');

		localStorage.setItem(ACCOUNTS_STORAGE_KEY, registryJson('alice', ['alice', 'bob']));
		mod = await load();
		expect(mod.scopedStorage.get(TABS_STORAGE_KEY)).toBe('alice-tabs');
		expect(store.get(`${TABS_STORAGE_KEY}::bob`)).toBe('bob-tabs');
	});

	it('switching the active account never deletes the other account keys', async () => {
		localStorage.setItem(ACCOUNTS_STORAGE_KEY, registryJson('alice', ['alice', 'bob']));
		let mod = await load();
		for (const key of CHAT_KEYS) mod.scopedStorage.set(key, `alice-${key}`);
		const before = new Map(store);

		localStorage.setItem(ACCOUNTS_STORAGE_KEY, registryJson('bob', ['alice', 'bob']));
		mod = await load();
		for (const key of CHAT_KEYS) mod.scopedStorage.get(key);
		mod.scopedStorage.set(TABS_STORAGE_KEY, 'bob-tabs');

		for (const [key, value] of before) {
			if (key === ACCOUNTS_STORAGE_KEY) continue;
			expect(store.get(key)).toBe(value);
		}
	});

	it('removeIdentityKeys deletes one account only', async () => {
		const { removeIdentityKeys } = await load();
		for (const id of ['alice', 'bob']) {
			for (const key of CHAT_KEYS) localStorage.setItem(`${key}::${id}`, 'v');
		}
		localStorage.setItem('theme', 'dark');

		removeIdentityKeys('alice');

		for (const key of CHAT_KEYS) {
			expect(store.has(`${key}::alice`)).toBe(false);
			expect(store.has(`${key}::bob`)).toBe(true);
		}
		expect(store.get('theme')).toBe('dark');
	});

	it('removeAllIdentityKeys deletes every account but not device prefs', async () => {
		const { removeAllIdentityKeys } = await load();
		for (const id of ['alice', 'bob', 'anon']) {
			for (const key of CHAT_KEYS) localStorage.setItem(`${key}::${id}`, 'v');
		}
		localStorage.setItem('theme', 'dark');
		localStorage.setItem('unified-ai-chat-history-rail-collapsed', 'true');

		removeAllIdentityKeys();

		expect(Array.from(store.keys()).sort()).toEqual(['theme', 'unified-ai-chat-history-rail-collapsed']);
	});
});

describe('legacy key migration', () => {
	beforeEach(() => {
		stubLocalStorage();
	});

	afterEach(() => {
		delete (globalThis as any).localStorage;
	});

	it('moves every legacy key to the user recorded in auth_last_user_id and deletes the legacy copy', async () => {
		localStorage.setItem('auth_last_user_id', 'alice');
		localStorage.setItem(ACCOUNTS_STORAGE_KEY, registryJson('alice', ['alice']));
		for (const key of CHAT_KEYS) localStorage.setItem(key, `legacy-${key}`);
		const { scopedStorage } = await load();

		expect(scopedStorage.get(TABS_STORAGE_KEY)).toBe(`legacy-${TABS_STORAGE_KEY}`);

		for (const key of CHAT_KEYS) {
			expect(localStorage.getItem(key)).toBeNull();
			expect(localStorage.getItem(`${key}::alice`)).toBe(`legacy-${key}`);
		}
	});

	it('works on an upgraded install with no registry yet', async () => {
		localStorage.setItem('auth_last_user_id', 'alice');
		localStorage.setItem(TABS_STORAGE_KEY, 'old-tabs');
		const { scopedStorage } = await load();

		expect(scopedStorage.get(TABS_STORAGE_KEY)).toBe('old-tabs');
		expect(localStorage.getItem(TABS_STORAGE_KEY)).toBeNull();
	});

	it('drops legacy keys that belong to a different user instead of handing them over', async () => {
		localStorage.setItem('auth_last_user_id', 'alice');
		localStorage.setItem(ACCOUNTS_STORAGE_KEY, registryJson('bob', ['alice', 'bob']));
		localStorage.setItem(TABS_STORAGE_KEY, 'alice-old-tabs');
		const { scopedStorage } = await load();

		expect(scopedStorage.get(TABS_STORAGE_KEY)).toBeNull();
		expect(localStorage.getItem(TABS_STORAGE_KEY)).toBeNull();
		expect(localStorage.getItem(`${TABS_STORAGE_KEY}::alice`)).toBeNull();
	});

	it('migrates only once: a second read does not re-import the legacy copy', async () => {
		localStorage.setItem('auth_last_user_id', 'alice');
		localStorage.setItem(TABS_STORAGE_KEY, 'old-tabs');
		const { scopedStorage } = await load();
		scopedStorage.get(TABS_STORAGE_KEY);
		scopedStorage.set(TABS_STORAGE_KEY, 'new-tabs');
		expect(scopedStorage.get(TABS_STORAGE_KEY)).toBe('new-tabs');
	});

	it('moves per-mode disabled-tools keys but leaves per-mode legacy session ids for chatConfig', async () => {
		localStorage.setItem('auth_last_user_id', 'alice');
		localStorage.setItem('unified-ai-chat-disabled-tools:chat', '["a"]');
		localStorage.setItem('unified-ai-chat-session-id:generation', 'sess');
		const { scopedStorage } = await load();

		expect(scopedStorage.getJSON<string[]>('unified-ai-chat-disabled-tools:chat')).toEqual(['a']);
		expect(localStorage.getItem('unified-ai-chat-session-id:generation')).toBe('sess');
	});

	it('does not migrate anything while the identity is unknown', async () => {
		localStorage.setItem(TABS_STORAGE_KEY, 'old-tabs');
		const { scopedStorage } = await load();
		scopedStorage.get(TABS_STORAGE_KEY);
		expect(localStorage.getItem(TABS_STORAGE_KEY)).toBe('old-tabs');
	});
});

describe('keysToPurge and namespacedKeys', () => {
	it('keysToPurge ignores keys that already carry a namespace', async () => {
		const { keysToPurge } = await load();
		expect(keysToPurge([TABS_STORAGE_KEY, `${TABS_STORAGE_KEY}::u1`, 'unified-ai-chat-session-id::u1'])).toEqual([
			TABS_STORAGE_KEY
		]);
	});

	it('namespacedKeys finds namespaced identity keys, optionally for one user', async () => {
		const { namespacedKeys } = await load();
		const keys = [
			`${TABS_STORAGE_KEY}::a`,
			`${TABS_STORAGE_KEY}::b`,
			'unified-ai-chat-disabled-tools:chat::a',
			'theme::a',
			TABS_STORAGE_KEY
		];
		expect(namespacedKeys(keys)).toEqual([
			`${TABS_STORAGE_KEY}::a`,
			`${TABS_STORAGE_KEY}::b`,
			'unified-ai-chat-disabled-tools:chat::a'
		]);
		expect(namespacedKeys(keys, 'b')).toEqual([`${TABS_STORAGE_KEY}::b`]);
	});
});
