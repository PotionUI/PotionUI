import { browser } from '$app/environment';
import { api } from '$lib/services/api/index';
import { IDENTITY_NAMESPACE_SEPARATOR, scopedStorage } from '$lib/stores/identityScopedStorage';

export function loadFromStorage(key: string): string {
	if (!browser) return '';
	return scopedStorage.get(key) || '';
}

export function saveToStorage(key: string, value: string): void {
	if (!browser) return;
	scopedStorage.set(key, value);
}

export async function loadConfigurations(): Promise<any[]> {
	try {
		const response = await api.getMyLLMConfigurations();
		if (response.success && response.data) {
			return response.data.llm_configs || [];
		}
	} catch {
		// silently fail
	}
	return [];
}

export function resolveConfigId(configs: any[], storedId: string): string {
	if (storedId && configs.find((c: any) => c.id === storedId)) {
		return storedId;
	}
	return configs.length > 0 ? configs[0].id : '';
}

// --- Active session persistence --------------------------------------------
// The panel binds to exactly one active session id, independent of the
// route or the currently selected mode (a session's mode is fixed by the
// server once the session exists). Older builds stored a session id per
// mode (`unified-ai-chat-session-id:<mode>`); those keys migrate once,
// best-effort, into the single active-session key below and are removed.

export const ACTIVE_SESSION_KEY = 'unified-ai-chat-session-id';
const LEGACY_PER_MODE_PREFIX = 'unified-ai-chat-session-id:';

export function loadActiveSessionId(): string {
	if (!browser) return '';
	const legacyKeys: string[] = [];
	for (let i = 0; i < localStorage.length; i++) {
		const key = localStorage.key(i);
		if (key?.startsWith(LEGACY_PER_MODE_PREFIX) && !key.includes(IDENTITY_NAMESPACE_SEPARATOR)) legacyKeys.push(key);
	}

	let current = scopedStorage.get(ACTIVE_SESSION_KEY) || '';
	if (!current) {
		for (const key of legacyKeys) {
			const value = localStorage.getItem(key);
			if (value) {
				current = value;
				break;
			}
		}
		if (current) scopedStorage.set(ACTIVE_SESSION_KEY, current);
	}
	for (const key of legacyKeys) localStorage.removeItem(key);
	return current;
}

export function saveActiveSessionId(id: string): void {
	if (!browser) return;
	if (id) {
		scopedStorage.set(ACTIVE_SESSION_KEY, id);
	} else {
		scopedStorage.remove(ACTIVE_SESSION_KEY);
	}
}

// Subtractive tool filter (names the user unticked), persisted per-mode.
export function disabledToolsStorageKey(mode: string): string {
	return `unified-ai-chat-disabled-tools:${mode}`;
}

export function loadDisabledTools(mode: string): string[] {
	return scopedStorage.getJSON<string[]>(disabledToolsStorageKey(mode)) ?? [];
}

export function saveDisabledTools(mode: string, names: string[]): void {
	scopedStorage.setJSON(disabledToolsStorageKey(mode), names);
}
