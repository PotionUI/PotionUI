import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore, activeTab, activeToolTab, currentTab, generatingTab } from './tabs';
import { TABS_STORAGE_KEY, isToolTab, type GenerationTab, type MediaRef, type ToolTab } from '$lib/types/tabs';
import { toPersistedTab } from './tabPersistence';

const dusk: MediaRef = { path: '/media/observatory-dusk.png', relative_path: 'library/observatory-dusk.png', url: '/m/observatory-dusk.png' };
const fox: MediaRef = { path: '/media/fox.webp', url: '/m/fox.webp' };
const clip: MediaRef = { path: '/media/timelapse.mp4', url: '/m/timelapse.mp4' };

function state() {
	return get(tabsStore);
}

function toolTabs(): ToolTab[] {
	return state().tabs.filter(isToolTab);
}

function tabById(id: string) {
	return state().tabs.find((tab) => tab.id === id);
}

describe('openToolTab', () => {
	beforeEach(() => tabsStore.reset());

	it('adds an active, empty tool tab named after the tool when there is no source', () => {
		const id = tabsStore.openToolTab('image', null, { name: 'Image editor' });
		const tab = tabById(id);
		expect(isToolTab(tab)).toBe(true);
		expect(tab).toMatchObject({ kind: 'tool', tool: 'image', source: null, state: null, dirty: 0, name: 'Image editor' });
		expect(state().activeTabId).toBe(id);
		expect(state().tabs).toHaveLength(2);
	});

	it('names a tab opened on an item after the file stem', () => {
		const id = tabsStore.openToolTab('image', dusk, { name: 'Image editor', activeTool: 'crop' });
		expect(tabById(id)).toMatchObject({ name: 'observatory-dusk', source: dusk, activeTool: 'crop' });
	});

	it('focuses the tab that already holds the item and switches its tool instead of opening another', () => {
		const first = tabsStore.openToolTab('image', dusk, { activeTool: 'crop' });
		const generationId = state().tabs[0].id;
		tabsStore.setActiveTab(generationId);

		const again = tabsStore.openToolTab('image', { ...dusk, url: '/other-url.png' }, { activeTool: 'effects' });

		expect(again).toBe(first);
		expect(toolTabs()).toHaveLength(1);
		expect(state().activeTabId).toBe(first);
		expect(tabById(first)).toMatchObject({ activeTool: 'effects' });
	});

	it('does not treat the same item in a different tool as already open', () => {
		tabsStore.openToolTab('image', dusk);
		tabsStore.openToolTab('acme.stamp', dusk);
		expect(toolTabs().map((tab) => tab.tool)).toEqual(['image', 'acme.stamp']);
	});

	it('loads the item into an empty editor tab of the same kind, preferring the active one', () => {
		const emptyA = tabsStore.openToolTab('image', null, { name: 'Image editor' });
		const emptyB = tabsStore.openToolTab('image', null, { name: 'Image editor' });
		tabsStore.setActiveTab(emptyB);

		const id = tabsStore.openToolTab('image', fox);

		expect(id).toBe(emptyB);
		expect(toolTabs()).toHaveLength(2);
		expect(tabById(emptyB)).toMatchObject({ source: fox, name: 'fox' });
		expect(tabById(emptyA)).toMatchObject({ source: null });
	});

	it('never fills an empty tab of another kind', () => {
		const emptyVideo = tabsStore.openToolTab('video', null, { name: 'Video editor' });
		const id = tabsStore.openToolTab('image', fox);
		expect(id).not.toBe(emptyVideo);
		expect(tabById(emptyVideo)).toMatchObject({ source: null });
	});

	it('keeps a renamed tab name when it is filled', () => {
		const empty = tabsStore.openToolTab('image', null, { name: 'Image editor' });
		tabsStore.renameTab(empty, 'Hero shot');
		tabsStore.openToolTab('image', fox);
		expect(tabById(empty)).toMatchObject({ name: 'Hero shot', renamed: true, source: fox });
	});

	it('opens N items as N tabs with the first one active', () => {
		const ids = tabsStore.openToolTabs('image', [dusk, fox, { ...dusk }]);
		expect(ids).toHaveLength(3);
		expect(ids[2]).toBe(ids[0]);
		expect(toolTabs()).toHaveLength(2);
		expect(state().activeTabId).toBe(ids[0]);
	});

	it('finds the tab open on an item', () => {
		const id = tabsStore.openToolTab('video', clip);
		expect(tabsStore.findToolTabFor(clip)?.id).toBe(id);
		expect(tabsStore.findToolTabFor(clip, 'image')).toBeNull();
		expect(tabsStore.findToolTabFor(fox)).toBeNull();
	});
});

describe('tool tabs and generation-only helpers', () => {
	beforeEach(() => tabsStore.reset());

	it('ignores generation fields written to a tool tab but applies name and colour', () => {
		const id = tabsStore.openToolTab('image', dusk);
		const before = tabById(id);
		tabsStore.updateTab(id, { prompt: 'a fox', selectedPreset: 'p', formData: { steps: 3 } });
		expect(tabById(id)).toBe(before);

		tabsStore.updateTab(id, { color: '#22c55e', prompt: 'ignored' });
		const after = tabById(id) as unknown as Record<string, unknown>;
		expect(after.color).toBe('#22c55e');
		expect(after.prompt).toBeUndefined();
	});

	it('updateAllTabs only patches generation tabs', () => {
		const id = tabsStore.openToolTab('image', dusk);
		tabsStore.updateAllTabs({ soundOnComplete: false });
		expect((tabById(id) as unknown as Record<string, unknown>).soundOnComplete).toBeUndefined();
		expect((state().tabs[0] as GenerationTab).soundOnComplete).toBe(false);
	});

	it('updateToolTab writes tool fields and leaves generation tabs alone', () => {
		const generationId = state().tabs[0].id;
		const id = tabsStore.openToolTab('image', dusk);
		tabsStore.updateToolTab(id, { dirty: 2, state: { steps: [{ op: 'crop' }] } });
		tabsStore.updateToolTab(generationId, { dirty: 5 });
		expect(tabById(id)).toMatchObject({ dirty: 2, state: { steps: [{ op: 'crop' }] } });
		expect((tabById(generationId) as unknown as Record<string, unknown>).dirty).toBeUndefined();
	});

	it('setToolTabTitle does not override a name the user chose', () => {
		const id = tabsStore.openToolTab('image', null, { name: 'Image editor' });
		tabsStore.setToolTabTitle(id, 'fox');
		expect(tabById(id)?.name).toBe('fox');
		tabsStore.renameTab(id, 'Mine');
		tabsStore.setToolTabTitle(id, 'other');
		expect(tabById(id)?.name).toBe('Mine');
	});

	it('keeps activeTab on the last generation tab while a tool tab is active', () => {
		const generationId = state().tabs[0].id;
		const toolId = tabsStore.openToolTab('image', dusk);
		expect(get(currentTab).id).toBe(toolId);
		expect(get(activeToolTab)?.id).toBe(toolId);
		expect(get(activeTab).id).toBe(generationId);
		expect(get(activeTab).kind).toBe('generation');

		tabsStore.setActiveTab(generationId);
		expect(get(activeToolTab)).toBeNull();
	});

	it('generatingTab never reports a tool tab', () => {
		tabsStore.openToolTab('video', clip);
		expect(get(generatingTab)).toBeNull();
	});

	it('addTab numbers generation tabs without counting tool tabs', () => {
		tabsStore.openToolTab('image', dusk);
		tabsStore.addTab();
		expect(state().tabs[state().tabs.length - 1].name).toBe('Generation 2');
	});

	it('closes a tool tab and moves focus back to the first tab', () => {
		const id = tabsStore.openToolTab('image', dusk);
		tabsStore.removeTab(id);
		expect(toolTabs()).toHaveLength(0);
		expect(state().activeTabId).toBe(state().tabs[0].id);
	});
});

describe('tool tab persistence', () => {
	beforeEach(() => {
		const store = new Map<string, string>();
		(globalThis as any).localStorage = {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key),
			clear: () => store.clear()
		};
	});

	afterEach(() => {
		vi.doUnmock('$app/environment');
		vi.resetModules();
	});

	it('persists the source as a reference, the state as JSON and the run, without inline pixels', () => {
		const tab: ToolTab = {
			kind: 'tool',
			id: 't1',
			name: 'observatory-dusk',
			color: '#3b82f6',
			tool: 'image',
			source: dusk,
			state: { steps: [{ op: 'crop', rect: [0, 0, 1, 1] }], preview: 'data:image/png;base64,AAAA' },
			run: { runId: 'run-9', progress: 42 },
			activeTool: 'crop',
			dirty: 1,
			thumbnail: 'data:image/png;base64,BBBB',
			renamed: false
		};
		expect(toPersistedTab(tab)).toEqual({
			kind: 'tool',
			id: 't1',
			name: 'observatory-dusk',
			color: '#3b82f6',
			tool: 'image',
			source: dusk,
			state: { steps: [{ op: 'crop', rect: [0, 0, 1, 1] }], preview: null },
			run: { runId: 'run-9', progress: 42 },
			activeTool: 'crop',
			dirty: 1,
			thumbnail: null,
			renamed: false
		});
	});

	it('round-trips tool tabs next to generation tabs through a reload', async () => {
		vi.doMock('$app/environment', () => ({ browser: true }));
		vi.resetModules();
		const first = await import('./tabs');
		const toolId = first.tabsStore.openToolTab('image', dusk, { activeTool: 'effects' });
		first.tabsStore.renameTab(toolId, 'Hero');
		first.tabsStore.updateToolTab(toolId, { dirty: 2, state: { steps: [1, 2] }, run: { runId: 'r1', progress: 10 } });
		first.tabsStore.persistNow();

		vi.resetModules();
		const second = await import('./tabs');
		const restored = get(second.tabsStore);
		expect(restored.activeTabId).toBe(toolId);
		expect(restored.tabs.map((tab) => tab.kind)).toEqual(['generation', 'tool']);
		expect(restored.tabs[1]).toMatchObject({
			id: toolId,
			name: 'Hero',
			renamed: true,
			tool: 'image',
			source: dusk,
			state: { steps: [1, 2] },
			run: { runId: 'r1', progress: 10 },
			activeTool: 'effects',
			dirty: 2
		});
	});

	it('loads a persisted tab with no kind as a generation tab', async () => {
		localStorage.setItem(
			`${TABS_STORAGE_KEY}::anon`,
			JSON.stringify({
				tabs: [{ id: 'legacy-1', name: 'Old', selectedPreset: 'p', selectedMode: 'm', selectedSessionId: null, activeGenerationId: null }],
				activeTabId: 'legacy-1'
			})
		);
		vi.doMock('$app/environment', () => ({ browser: true }));
		vi.resetModules();
		const { tabsStore: fresh } = await import('./tabs');
		const tab = get(fresh).tabs[0] as GenerationTab;
		expect(tab.kind).toBe('generation');
		expect(tab.selectedPreset).toBe('p');
		expect(tab.generation.isGenerating).toBe(false);
	});
});
