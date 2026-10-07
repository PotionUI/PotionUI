import { TABS_STORAGE_KEY } from '$lib/types/tabs';
import { ACCOUNTS_STORAGE_KEY, parseRegistry } from '$lib/stores/accountRegistry';

export const IDENTITY_SCOPED_STORAGE_KEYS: readonly string[] = [
	TABS_STORAGE_KEY,
	'unified-ai-chat-session-id',
	'unified-ai-chat-config-id',
	'unified-ai-chat-pinned-tab',
	'unified-ai-chat-attach-image',
	'unified-ai-chat-enable-tools',
	'phrasebook-generation-config'
];

export const IDENTITY_SCOPED_STORAGE_PREFIXES: readonly string[] = [
	'unified-ai-chat-disabled-tools:',
	'unified-ai-chat-session-id:'
];

const LEGACY_PER_MODE_SESSION_PREFIX = 'unified-ai-chat-session-id:';

export const IDENTITY_NAMESPACE_SEPARATOR = '::';
export const ANON_IDENTITY = 'anon';

export const LAST_USER_ID_KEY = 'auth_last_user_id';

export function isDifferentIdentity(lastUserId: string | null, currentUserId: string): boolean {
	return lastUserId !== null && lastUserId !== currentUserId;
}

function isScopedBase(key: string): boolean {
	return (
		IDENTITY_SCOPED_STORAGE_KEYS.includes(key) ||
		IDENTITY_SCOPED_STORAGE_PREFIXES.some((prefix) => key.startsWith(prefix))
	);
}

export function keysToPurge(allKeys: readonly string[]): string[] {
	return allKeys.filter((key) => isScopedBase(key) && !key.includes(IDENTITY_NAMESPACE_SEPARATOR));
}

export function namespacedKeys(allKeys: readonly string[], userId?: string): string[] {
	return allKeys.filter((key) => {
		const at = key.lastIndexOf(IDENTITY_NAMESPACE_SEPARATOR);
		if (at === -1) return false;
		if (!isScopedBase(key.slice(0, at))) return false;
		return userId === undefined || key.slice(at + IDENTITY_NAMESPACE_SEPARATOR.length) === userId;
	});
}

export function identityKey(base: string, userId: string): string {
	return `${base}${IDENTITY_NAMESPACE_SEPARATOR}${userId}`;
}

function listKeys(): string[] {
	const keys: string[] = [];
	for (let i = 0; i < localStorage.length; i++) {
		const key = localStorage.key(i);
		if (key) keys.push(key);
	}
	return keys;
}

function hasStorage(): boolean {
	return typeof localStorage !== 'undefined';
}

export function readActiveIdentity(): string {
	if (!hasStorage()) return ANON_IDENTITY;
	try {
		const registry = parseRegistry(localStorage.getItem(ACCOUNTS_STORAGE_KEY));
		return registry.activeId ?? localStorage.getItem(LAST_USER_ID_KEY) ?? ANON_IDENTITY;
	} catch {
		return ANON_IDENTITY;
	}
}

export const bootIdentity: string = readActiveIdentity();

export function migrateLegacyIdentityKeys(userId: string): void {
	if (!hasStorage() || userId === ANON_IDENTITY) return;
	const owner = localStorage.getItem(LAST_USER_ID_KEY);
	for (const key of keysToPurge(listKeys())) {
		const foreign = owner !== null && owner !== userId;
		if (key.startsWith(LEGACY_PER_MODE_SESSION_PREFIX)) {
			if (foreign) localStorage.removeItem(key);
			continue;
		}
		if (!foreign) {
			const value = localStorage.getItem(key);
			if (value !== null) localStorage.setItem(identityKey(key, userId), value);
		}
		localStorage.removeItem(key);
	}
}

export function hasIdentityState(userId: string): boolean {
	if (!hasStorage()) return false;
	const keys = listKeys();
	return keysToPurge(keys).length > 0 || namespacedKeys(keys, userId).length > 0;
}

export function removeIdentityKeys(userId: string): void {
	if (!hasStorage()) return;
	for (const key of namespacedKeys(listKeys(), userId)) localStorage.removeItem(key);
}

export function removeAllIdentityKeys(): void {
	if (!hasStorage()) return;
	for (const key of namespacedKeys(listKeys())) localStorage.removeItem(key);
}

let migratedFor: string | null = null;

function scopedKey(base: string): string {
	const userId = readActiveIdentity();
	if (migratedFor !== userId) {
		migratedFor = userId;
		migrateLegacyIdentityKeys(userId);
	}
	return identityKey(base, userId);
}

export const scopedStorage = {
	get(base: string): string | null {
		if (!hasStorage()) return null;
		return localStorage.getItem(scopedKey(base));
	},
	set(base: string, value: string): void {
		if (!hasStorage()) return;
		localStorage.setItem(scopedKey(base), value);
	},
	remove(base: string): void {
		if (!hasStorage()) return;
		localStorage.removeItem(scopedKey(base));
	},
	getJSON<T>(base: string): T | null {
		const raw = scopedStorage.get(base);
		if (!raw) return null;
		try {
			return JSON.parse(raw) as T;
		} catch {
			return null;
		}
	},
	setJSON(base: string, value: unknown): void {
		scopedStorage.set(base, JSON.stringify(value));
	}
};
