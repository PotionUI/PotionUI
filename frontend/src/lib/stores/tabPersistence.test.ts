import { describe, it, expect, vi, beforeEach } from 'vitest';
import { migrateLegacyTabIds, isLegacyTabId } from './tabPersistence';
import type { PersistedTabsState, Tab } from '$lib/types/tabs';

// vitest runs with `environment: 'node'` (vite.config.ts), so `$app/environment`'s
// `browser` is false and saveTabsToLocalStorage/loadTabsFromLocalStorage no-op —
// force it true here (see statsRowLimits.test.ts for the same pattern elsewhere)
// and stub the global `localStorage` node doesn't provide, so the
// serialize/restore round trip can actually be exercised.
vi.mock('$app/environment', () => ({ browser: true }));

function persistedTab(overrides: Partial<PersistedTabsState['tabs'][number]> & { id: string }) {
	return {
		name: 'Generation 1',
		selectedPreset: null,
		selectedMode: null,
		selectedSessionId: null,
		activeGenerationId: null,
		...overrides
	};
}

describe('isLegacyTabId', () => {
	it('flags the old `tab-1` / `tab-<timestamp>-<seq>` shapes', () => {
		expect(isLegacyTabId('tab-1')).toBe(true);
		expect(isLegacyTabId('tab-1699999999999-3')).toBe(true);
	});

	it('does not flag a crypto.randomUUID() id', () => {
		expect(isLegacyTabId('161aa91e-3aca-46c1-9e3e-ce4f452d956e')).toBe(false);
	});
});

describe('migrateLegacyTabIds', () => {
	it('is a no-op when no tab has a legacy id', () => {
		const state: PersistedTabsState = {
			tabs: [persistedTab({ id: '161aa91e-3aca-46c1-9e3e-ce4f452d956e' })],
			activeTabId: '161aa91e-3aca-46c1-9e3e-ce4f452d956e'
		};

		const { state: migrated, changed } = migrateLegacyTabIds(state);
		expect(changed).toBe(false);
		expect(migrated).toBe(state);
	});

	it('rewrites every legacy tab id to a fresh unique id, preserving every other field', () => {
		const state: PersistedTabsState = {
			tabs: [
				persistedTab({ id: 'tab-1', name: 'Browser A', selectedPreset: 'native/SDXL/realistic' }),
				persistedTab({ id: 'tab-1700000000000-2', name: 'Browser B', color: 'blue' } as any)
			],
			activeTabId: 'tab-1'
		};

		const { state: migrated, changed } = migrateLegacyTabIds(state);
		expect(changed).toBe(true);

		// Every id is rewritten, and rewritten ids are unique from each other and from the originals.
		const newIds = migrated.tabs.map((t) => t.id);
		expect(newIds).not.toContain('tab-1');
		expect(newIds).not.toContain('tab-1700000000000-2');
		expect(new Set(newIds).size).toBe(2);
		newIds.forEach((id) => expect(isLegacyTabId(id)).toBe(false));

		// Every other field survives untouched.
		expect(migrated.tabs[0].name).toBe('Browser A');
		expect(migrated.tabs[0].selectedPreset).toBe('native/SDXL/realistic');
		expect(migrated.tabs[1].name).toBe('Browser B');
		expect((migrated.tabs[1] as any).color).toBe('blue');

		// The active tab id is rewritten consistently to match its tab's new id.
		expect(migrated.activeTabId).toBe(migrated.tabs[0].id);
	});

	it('leaves a non-legacy activeTabId untouched even if other tabs are migrated', () => {
		const state: PersistedTabsState = {
			tabs: [
				persistedTab({ id: 'tab-1' }),
				persistedTab({ id: 'already-a-uuid-1234' })
			],
			activeTabId: 'already-a-uuid-1234'
		};

		const { state: migrated } = migrateLegacyTabIds(state);
		expect(migrated.activeTabId).toBe('already-a-uuid-1234');
	});
});

describe('saveTabsToLocalStorage / loadTabsFromLocalStorage', () => {
	beforeEach(() => {
		const store = new Map<string, string>();
		(globalThis as any).localStorage = {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key),
			clear: () => store.clear()
		};
	});

	function fakeTab(overrides: Partial<Tab> = {}): Tab {
		return {
			id: 't1',
			name: 'Generation 1',
			selectedPreset: 'native/SDXL/realistic',
			selectedMode: 'txt2img',
			selectedVariant: 'default',
			selectedSessionId: null,
			prompt: 'a cat',
			negativePrompt: 'blurry',
			promptSegments: [{ id: 's1', content: 'a cat' } as any],
			negativePromptSegments: [],
			formData: { steps: 30 },
			variables: {},
			seed: 42,
			selectedBackendId: 'backend-1',
			activeGenerationId: null,
			generation: { queue: [] } as any,
			workbenchMaxHeight: '600',
			leftPanelWidth: 380,
			layoutMode: 'two',
			promptPanelWidth: 420,
			autoTagIds: [],
			autoCollectionIds: [],
			color: null,
			...overrides
		} as Tab;
	}

	it('round-trips an unsaved tab\'s content fields through localStorage', async () => {
		const { saveTabsToLocalStorage, loadTabsFromLocalStorage } = await import('./tabPersistence');
		const tab = fakeTab();

		saveTabsToLocalStorage([tab], tab.id);
		const restored = loadTabsFromLocalStorage();

		expect(restored).not.toBeNull();
		const restoredTab = restored!.tabs[0];
		expect(restoredTab.prompt).toBe('a cat');
		expect(restoredTab.negativePrompt).toBe('blurry');
		expect(restoredTab.promptSegments).toEqual([{ id: 's1', content: 'a cat' }]);
		expect(restoredTab.formData).toEqual({ steps: 30 });
		expect(restoredTab.seed).toBe(42);
		expect(restoredTab.selectedBackendId).toBe('backend-1');
		expect(restoredTab.selectedVariant).toBe('default');
	});

	it('strips data: URI strings out of formData/variables before persisting', async () => {
		const { saveTabsToLocalStorage, loadTabsFromLocalStorage } = await import('./tabPersistence');
		const tab = fakeTab({
			formData: {
				label: 'ok',
				maskPreview: 'data:image/png;base64,AAAAAAA=',
				nested: { thumb: 'data:image/png;base64,BBBB=' }
			},
			variables: { note: { type: 'text', value: 'data:text/plain,hello' } } as any
		});

		saveTabsToLocalStorage([tab], tab.id);
		const restored = loadTabsFromLocalStorage();

		const formData = restored!.tabs[0].formData as any;
		expect(formData.label).toBe('ok');
		expect(formData.maskPreview).toBeNull();
		expect(formData.nested.thumb).toBeNull();

		const variables = restored!.tabs[0].variables as any;
		expect(variables.note.value).toBeNull();
	});

	it('does not touch ordinary media references (path/url strings)', async () => {
		const { saveTabsToLocalStorage, loadTabsFromLocalStorage } = await import('./tabPersistence');
		const tab = fakeTab({
			formData: {
				image: { path: '/media/uploads/a.png', url: 'https://example.com/a.png' }
			}
		});

		saveTabsToLocalStorage([tab], tab.id);
		const restored = loadTabsFromLocalStorage();

		expect((restored!.tabs[0].formData as any).image).toEqual({
			path: '/media/uploads/a.png',
			url: 'https://example.com/a.png'
		});
	});
});
