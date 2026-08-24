import { describe, it, expect, beforeEach, vi } from 'vitest';

// vitest runs with `environment: 'node'`, so `$app/environment`'s `browser` is
// false and `storage` no-ops — force it true and stub `localStorage` (same
// pattern as tabPersistence.test.ts) so the round trip is actually exercised.
vi.mock('$app/environment', () => ({ browser: true }));

import { getApplySoundToAllTabs, getGlobalSoundDefault, setApplySoundToAllTabs, setGlobalSoundDefault } from './soundSettings';

describe('soundSettings', () => {
	beforeEach(() => {
		const store = new Map<string, string>();
		(globalThis as any).localStorage = {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key),
			clear: () => store.clear()
		};
	});

	it('defaults both sound kinds to enabled when nothing is persisted', () => {
		expect(getGlobalSoundDefault('complete')).toBe(true);
		expect(getGlobalSoundDefault('error')).toBe(true);
	});

	it('round-trips a global default through localStorage, independently per kind', () => {
		setGlobalSoundDefault('complete', false);

		expect(getGlobalSoundDefault('complete')).toBe(false);
		expect(getGlobalSoundDefault('error')).toBe(true);
	});

	it('defaults "apply to all tabs" to off, and round-trips it once set', () => {
		expect(getApplySoundToAllTabs()).toBe(false);

		setApplySoundToAllTabs(true);
		expect(getApplySoundToAllTabs()).toBe(true);
	});
});
