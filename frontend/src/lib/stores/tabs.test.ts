import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from './tabs';
import { TABS_STORAGE_KEY } from '$lib/types/tabs';

describe('tabsStore tab ids', () => {
	beforeEach(() => tabsStore.reset());

	it('generates unique ids when many tabs are added in the same millisecond', () => {
		// Regression: ids were `tab-${Date.now()}`, so a synchronous loop
		// (e.g. restoring a workspace) produced duplicate keyed-each keys.
		for (let i = 0; i < 20; i++) {
			tabsStore.addTab();
		}

		const ids = get(tabsStore).tabs.map((t) => t.id);
		expect(new Set(ids).size).toBe(ids.length);
	});

	it('activates the newly added tab', () => {
		tabsStore.addTab();
		const state = get(tabsStore);
		expect(state.activeTabId).toBe(state.tabs[state.tabs.length - 1].id);
	});

	it('keeps at least one tab when removing the last remaining tab', () => {
		const state = get(tabsStore);
		tabsStore.removeTab(state.tabs[0].id);
		expect(get(tabsStore).tabs.length).toBe(1);
	});

	it('addTabWithData creates and activates a tab with the given fields', () => {
		const id = tabsStore.addTabWithData('Reused: My Preset', {
			selectedPreset: 'native/SDXL/realistic',
			selectedMode: 'txt2img',
			formData: { steps: 30, seed: 123 },
			promptSegments: [{ id: 's1', content: 'a cat' }]
		});
		const state = get(tabsStore);
		expect(state.activeTabId).toBe(id);
		const tab = state.tabs.find(t => t.id === id);
		expect(tab?.selectedPreset).toBe('native/SDXL/realistic');
		expect(tab?.formData).toEqual({ steps: 30, seed: 123 });
		expect(tab?.promptSegments).toEqual([{ id: 's1', content: 'a cat' }]);
	});
});

describe('restoring a tab persisted with the removed Flow-view fields', () => {
	beforeEach(() => {
		const store = new Map<string, string>();
		(globalThis as any).localStorage = {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key),
			clear: () => store.clear()
		};
	});

	it('restores silently (the unknown editorView/flowAppearance keys are simply ignored), and neither key survives the next persisted save', async () => {
		localStorage.setItem(
			TABS_STORAGE_KEY,
			JSON.stringify({
				tabs: [
					{
						id: 'legacy-1',
						name: 'Generation 1',
						selectedPreset: null,
						selectedMode: null,
						selectedSessionId: null,
						activeGenerationId: null,
						editorView: 'flow',
						flowAppearance: { lineHeight: 2.8, underline: 'strong' }
					}
				],
				activeTabId: 'legacy-1'
			})
		);

		vi.doMock('$app/environment', () => ({ browser: true }));
		vi.resetModules();
		try {
			const { tabsStore: freshTabsStore } = await import('./tabs');
			const state = get(freshTabsStore);

			const tab = state.tabs.find((t) => t.id === 'legacy-1');
			expect(tab).toBeDefined();
			expect((tab as any).editorView).toBeUndefined();
			expect((tab as any).flowAppearance).toBeUndefined();

			const { saveTabsToLocalStorage } = await import('./tabPersistence');
			saveTabsToLocalStorage(state.tabs, state.activeTabId);

			const raw = localStorage.getItem(TABS_STORAGE_KEY)!;
			expect(raw).not.toContain('editorView');
			expect(raw).not.toContain('flowAppearance');
		} finally {
			vi.doUnmock('$app/environment');
			vi.resetModules();
		}
	});
});
