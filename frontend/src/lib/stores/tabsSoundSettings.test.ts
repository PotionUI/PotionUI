import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';

// vitest runs with `environment: 'node'`, so `$app/environment`'s `browser` is
// false and `storage` no-ops — force it true and stub `localStorage` (same
// pattern as tabPersistence.test.ts) so tab seeding actually reads the
// persisted global sound defaults. Stubbed via `vi.hoisted` (not just
// `beforeEach`) because `tabsStore` is a singleton constructed at import
// time, which already needs a working `localStorage` to build its initial
// default tab.
vi.mock('$app/environment', () => ({ browser: true }));
vi.hoisted(() => {
	(globalThis as any).localStorage = {
		getItem: () => null,
		setItem: () => {},
		removeItem: () => {},
		clear: () => {}
	};
});

import { tabsStore } from './tabs';

describe('tabsStore generation-sound settings', () => {
	beforeEach(() => {
		const store = new Map<string, string>();
		(globalThis as any).localStorage = {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key),
			clear: () => store.clear()
		};
		tabsStore.reset();
	});

	it('seeds a freshly added tab from the persisted global sound defaults', () => {
		localStorage.setItem('potionui_sound_on_complete_default', 'false');
		localStorage.setItem('potionui_sound_on_error_default', 'true');

		tabsStore.addTab();

		const state = get(tabsStore);
		const newTab = state.tabs[state.tabs.length - 1];
		expect(newTab.soundOnComplete).toBe(false);
		expect(newTab.soundOnError).toBe(true);
	});

	it('defaults both sound toggles to enabled when no global default is persisted', () => {
		tabsStore.addTab();

		const state = get(tabsStore);
		const newTab = state.tabs[state.tabs.length - 1];
		expect(newTab.soundOnComplete).toBe(true);
		expect(newTab.soundOnError).toBe(true);
	});

	it('updateAllTabs patches the given fields on every open tab, leaving others untouched', () => {
		tabsStore.addTab();
		tabsStore.addTab();
		const before = get(tabsStore);
		const [firstId] = before.tabs.map((t) => t.id);
		tabsStore.updateTab(firstId, { name: 'Kept name' });

		tabsStore.updateAllTabs({ soundOnComplete: false, soundOnError: false });

		const state = get(tabsStore);
		expect(state.tabs).toHaveLength(3);
		expect(state.tabs.every((t) => t.soundOnComplete === false && t.soundOnError === false)).toBe(true);
		expect(state.tabs.find((t) => t.id === firstId)?.name).toBe('Kept name');
	});
});
