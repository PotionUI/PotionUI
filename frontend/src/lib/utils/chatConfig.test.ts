import { describe, it, expect, beforeEach, vi } from 'vitest';

// vitest runs with `environment: 'node'`, so `$app/environment`'s `browser` is
// false and these helpers no-op — force it true and stub `localStorage` (same
// pattern as chatHistoryRail.test.ts) so the round trip is actually exercised.
vi.mock('$app/environment', () => ({ browser: true }));
// chatConfig.ts also pulls in the real api singleton (for loadConfigurations),
// whose constructor reads localStorage at import time — stub it out so that
// happens before our localStorage stub exists.
vi.mock('$lib/services/api/index', () => ({ api: { getMyLLMConfigurations: vi.fn() } }));

import { ACTIVE_SESSION_KEY, loadActiveSessionId, saveActiveSessionId } from './chatConfig';

describe('active session id storage', () => {
	beforeEach(() => {
		const store = new Map<string, string>();
		(globalThis as any).localStorage = {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key),
			get length() {
				return store.size;
			},
			key: (index: number) => Array.from(store.keys())[index] ?? null,
			clear: () => store.clear()
		};
	});

	it('returns empty when nothing is persisted', () => {
		expect(loadActiveSessionId()).toBe('');
	});

	it('round-trips a saved session id', () => {
		saveActiveSessionId('sess-1');
		expect(loadActiveSessionId()).toBe('sess-1');
		expect(localStorage.getItem(ACTIVE_SESSION_KEY)).toBe('sess-1');
	});

	it('clears the key when saving an empty id', () => {
		saveActiveSessionId('sess-1');
		saveActiveSessionId('');
		expect(localStorage.getItem(ACTIVE_SESSION_KEY)).toBeNull();
		expect(loadActiveSessionId()).toBe('');
	});

	it('migrates a legacy per-mode key into the active-session key and removes it', () => {
		localStorage.setItem('unified-ai-chat-session-id:generation', 'legacy-sess');
		expect(loadActiveSessionId()).toBe('legacy-sess');
		expect(localStorage.getItem(ACTIVE_SESSION_KEY)).toBe('legacy-sess');
		expect(localStorage.getItem('unified-ai-chat-session-id:generation')).toBeNull();
	});

	it('removes every legacy per-mode key even when several are present', () => {
		localStorage.setItem('unified-ai-chat-session-id:generation', 'legacy-a');
		localStorage.setItem('unified-ai-chat-session-id:models', 'legacy-b');

		loadActiveSessionId();

		expect(localStorage.getItem('unified-ai-chat-session-id:generation')).toBeNull();
		expect(localStorage.getItem('unified-ai-chat-session-id:models')).toBeNull();
	});

	it('does not overwrite an already-set active session id, but still cleans up legacy keys', () => {
		localStorage.setItem(ACTIVE_SESSION_KEY, 'current-sess');
		localStorage.setItem('unified-ai-chat-session-id:generation', 'stale-legacy');

		expect(loadActiveSessionId()).toBe('current-sess');
		expect(localStorage.getItem('unified-ai-chat-session-id:generation')).toBeNull();
	});
});
