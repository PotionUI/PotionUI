import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { settle } from './helpers/settle';

const longName = 'Cinematic lighting studies, glass, liquids and reflections';

const collections = [
	{ id: 'potions', parent_id: null, name: 'Potion studies', item_count: 42, scope: 'history', created_at: '' },
	{ id: 'glass', parent_id: 'potions', name: longName, item_count: 18, scope: 'history', created_at: '' }
];

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn(),
		getModelTypes: vi.fn(),
		getGenerationHistory: vi.fn(),
		getHistoryFacets: vi.fn(),
		searchPhrasebook: vi.fn(),
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn(),
		listPresets: vi.fn().mockResolvedValue({ success: true, data: [] }),
		listCollections: vi.fn()
	}
}));

vi.mock('$lib/stores/confirm', () => ({ confirmDialog: vi.fn() }));

const { api } = await import('$lib/services/api/index');
const { historyStore } = await import('$lib/stores/history');
const { historyCollectionsStore } = await import('$lib/stores/collections');
const { resetLayerStackForTests } = await import('$lib/actions/layerStack');
const { default: HistoryToolbar } = await import('../../src/routes/history/components/HistoryToolbar.svelte');
const { createClassComponent } = await import('svelte/legacy');

describe('History toolbar with a collection selected', () => {
	let destroy: () => void;

	async function mountToolbar(collectionId: string | undefined) {
		historyStore.setFilter('collectionId', collectionId);
		await historyStore.loadGenerations();
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({
			component: HistoryToolbar as never,
			target,
			props: { onOpenUpload: () => {}, onOpenAddTag: () => {}, onOpenDeleteByCriteria: () => {} }
		});
		destroy = () => {
			component.$destroy();
			target.remove();
		};
		await settle();
		return target;
	}

	beforeEach(async () => {
		resetLayerStackForTests();
		vi.mocked(api.getGenerationHistory).mockResolvedValue({ success: true, data: { generations: [], total: 15 } } as never);
		vi.mocked(api.getHistoryFacets).mockResolvedValue({
			success: true,
			data: { modes: [], presets: [], models: [] }
		} as never);
		vi.mocked(api.listCollections).mockResolvedValue({
			success: true,
			data: { collections, total: collections.length, smart_counts: { all: 248, favorites: 0, unsorted: 0 } }
		} as never);
		await historyCollectionsStore.load();
	});

	afterEach(() => {
		destroy();
		historyStore.setFilter('collectionId', undefined);
		document.body.innerHTML = '';
	});

	it('keeps the page title and the grid total, and adds the collection actions', async () => {
		const target = await mountToolbar('glass');
		const heading = target.querySelector('h1')!;
		expect(heading.textContent).toBe('History');
		expect(heading.parentElement?.parentElement?.textContent?.replace(/\s+/g, ' ')).toContain('15 generations');
		expect(target.textContent).not.toContain(longName);
		expect(target.querySelectorAll('h1, h2').length).toBe(1);
		expect(target.querySelector('button[aria-label="Collection actions"]')).not.toBeNull();
	});

	it('hides the collection actions outside a collection', async () => {
		const target = await mountToolbar(undefined);
		expect(target.querySelector('h1')?.textContent).toBe('History');
		expect(target.querySelector('button[aria-label="Collection actions"]')).toBeNull();
	});
});

describe('History filters: include subcollections', () => {
	let destroy: () => void;

	const filtersButton = () =>
		Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.includes('Filters'))!;
	const includeBox = () =>
		document.querySelector<HTMLInputElement>('[data-testid="include-subcollections"] input[type="checkbox"]');

	async function openFilters(collectionId: string | undefined) {
		historyStore.clearFilters();
		historyStore.setFilter('collectionId', collectionId);
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({
			component: HistoryToolbar as never,
			target,
			props: { onOpenUpload: () => {}, onOpenAddTag: () => {}, onOpenDeleteByCriteria: () => {} }
		});
		destroy = () => {
			component.$destroy();
			target.remove();
		};
		await settle();
		filtersButton().click();
		await settle();
	}

	beforeEach(async () => {
		resetLayerStackForTests();
		vi.mocked(api.getGenerationHistory).mockResolvedValue({ success: true, data: { generations: [], total: 0 } } as never);
		vi.mocked(api.getHistoryFacets).mockResolvedValue({
			success: true,
			data: { modes: [], presets: [], models: [] }
		} as never);
		vi.mocked(api.listCollections).mockResolvedValue({
			success: true,
			data: { collections, total: collections.length, smart_counts: { all: 248, favorites: 0, unsorted: 0 } }
		} as never);
		await historyCollectionsStore.load();
	});

	afterEach(() => {
		destroy();
		historyStore.clearFilters();
		document.body.innerHTML = '';
	});

	it('shows the row only for a collection with subcollections', async () => {
		await openFilters('glass');
		expect(includeBox()).toBeNull();
		destroy();
		await openFilters('potions');
		expect(includeBox()).not.toBeNull();
		expect(includeBox()!.checked).toBe(true);
	});

	it('turning it off sets direct only, reloads and counts in the Filters badge', async () => {
		await openFilters('potions');
		expect(filtersButton().textContent?.trim()).toBe('Filters');
		vi.mocked(api.getGenerationHistory).mockClear();
		includeBox()!.click();
		await settle();
		expect(get(historyStore).filters.directOnly).toBe(true);
		expect(api.getGenerationHistory).toHaveBeenCalled();
		expect(filtersButton().textContent?.replace(/\s+/g, ' ').trim()).toBe('Filters 1');
		expect(document.body.textContent).toContain('Without subcollections');
	});

	it('Clear all restores including subcollections', async () => {
		await openFilters('potions');
		includeBox()!.click();
		await settle();
		const clearAll = Array.from(document.querySelectorAll<HTMLButtonElement>('[data-filters-popover] button')).find(
			(b) => b.textContent?.trim() === 'Clear all'
		)!;
		expect(clearAll.disabled).toBe(false);
		clearAll.click();
		await settle();
		expect(get(historyStore).filters.directOnly).toBe(false);
		expect(filtersButton().textContent?.trim()).toBe('Filters');
	});
});

