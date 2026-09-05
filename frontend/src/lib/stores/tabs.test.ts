import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from './tabs';
import { TABS_STORAGE_KEY } from '$lib/types/tabs';
import {
	setGenerationOutputs,
	peekGenerationOutputs,
	isGenerationOutputsRetired,
	setGenerationUnsubscribeHandler,
	resetGenerationOutputsRetirementForTests
} from '$lib/generation/messages/generationOutputs';

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

describe('removeTab retires the closed tab\'s abandoned generations', () => {
	beforeEach(() => {
		tabsStore.reset();
		resetGenerationOutputsRetirementForTests();
		setGenerationUnsubscribeHandler(null);
	});

	it('retires a generation only the closed tab claimed (cache dropped, WebSocket unsubscribed)', () => {
		const unsubscribe = vi.fn();
		setGenerationUnsubscribeHandler(unsubscribe);
		setGenerationOutputs('gen-1', {
			images: [],
			videos: [{ url: '/v.mp4', originalUrl: '/v.mp4' } as any],
			audios: [],
			meshes: []
		});
		const closingTabId = tabsStore.addTabWithData('Closing', { activeGenerationId: 'gen-1' });

		tabsStore.removeTab(closingTabId);

		expect(unsubscribe).toHaveBeenCalledWith('gen-1');
		expect(peekGenerationOutputs('gen-1')).toEqual({ images: [], videos: [], audios: [], meshes: [] });
		expect(isGenerationOutputsRetired('gen-1')).toBe(true);
	});

	it('does not retire a generation a still-open tab also claims (shared consumer)', () => {
		const unsubscribe = vi.fn();
		setGenerationUnsubscribeHandler(unsubscribe);
		setGenerationOutputs('gen-shared', {
			images: [],
			videos: [{ url: '/v.mp4', originalUrl: '/v.mp4' } as any],
			audios: [],
			meshes: []
		});
		// Two tabs both reference the same generation id (e.g. a Video Director
		// run linked from more than one place) -- closing one must not
		// unsubscribe or drop the cache the other still needs.
		tabsStore.addTabWithData('Also watching', { activeGenerationId: 'gen-shared' });
		const closingTabId = tabsStore.addTabWithData('Closing', { activeGenerationId: 'gen-shared' });

		tabsStore.removeTab(closingTabId);

		expect(unsubscribe).not.toHaveBeenCalled();
		expect(isGenerationOutputsRetired('gen-shared')).toBe(false);
		expect(peekGenerationOutputs('gen-shared').videos).toHaveLength(1);
	});

	it('retires every generation the closed tab uniquely claimed via activeGenerationId, queue and directorRunLinks', () => {
		const unsubscribe = vi.fn();
		setGenerationUnsubscribeHandler(unsubscribe);
		const tab = get(tabsStore).tabs[0];
		const closingTabId = tabsStore.addTabWithData('Closing', {
			activeGenerationId: 'gen-active',
			generation: {
				...tab.generation,
				queue: [
					{ generation_id: 'gen-active', queue_position: null, status: 'running' },
					{ generation_id: 'gen-queued', queue_position: 1, status: 'pending' }
				]
			},
			directorRunLinks: { 'gen-director': ['shot-1'] }
		});

		tabsStore.removeTab(closingTabId);

		expect(unsubscribe).toHaveBeenCalledWith('gen-active');
		expect(unsubscribe).toHaveBeenCalledWith('gen-queued');
		expect(unsubscribe).toHaveBeenCalledWith('gen-director');
		expect(unsubscribe).toHaveBeenCalledTimes(3);
	});

	it('does nothing when the closed tab claims no generations', () => {
		const unsubscribe = vi.fn();
		setGenerationUnsubscribeHandler(unsubscribe);
		const closingTabId = tabsStore.addTabWithData('Idle', {});

		tabsStore.removeTab(closingTabId);

		expect(unsubscribe).not.toHaveBeenCalled();
	});

	it('retires nothing (there is nothing left to close) when removing the sole remaining tab', () => {
		const unsubscribe = vi.fn();
		setGenerationUnsubscribeHandler(unsubscribe);
		setGenerationOutputs('gen-only', { images: [], videos: [], audios: [], meshes: [] });
		const state = get(tabsStore);
		tabsStore.updateTab(state.tabs[0].id, { activeGenerationId: 'gen-only' });

		tabsStore.removeTab(state.tabs[0].id);

		expect(get(tabsStore).tabs.length).toBe(1);
		expect(unsubscribe).not.toHaveBeenCalled();
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

	it('hydrates a persisted workbenchCollapsed flag, same tier as leftPanelCollapsed', async () => {
		localStorage.setItem(
			TABS_STORAGE_KEY,
			JSON.stringify({
				tabs: [
					{
						id: 'folded-1',
						name: 'Generation 1',
						selectedPreset: null,
						selectedMode: null,
						selectedSessionId: null,
						activeGenerationId: null,
						workbenchCollapsed: true
					}
				],
				activeTabId: 'folded-1'
			})
		);

		vi.doMock('$app/environment', () => ({ browser: true }));
		vi.resetModules();
		try {
			const { tabsStore: freshTabsStore } = await import('./tabs');
			const state = get(freshTabsStore);
			const tab = state.tabs.find((t) => t.id === 'folded-1');
			expect(tab?.workbenchCollapsed).toBe(true);
		} finally {
			vi.doUnmock('$app/environment');
			vi.resetModules();
		}
	});
});
