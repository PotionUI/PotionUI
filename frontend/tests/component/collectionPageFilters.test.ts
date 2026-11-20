import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { settle } from './helpers/settle';

const collections = [
	{ id: 'potions', parent_id: null, name: 'Potion studies', item_count: 42, scope: 'library', created_at: '' },
	{ id: 'glass', parent_id: 'potions', name: 'Blue glass', item_count: 18, scope: 'library', created_at: '' }
];

vi.mock('$lib/services/api/index', () => ({
	api: {
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn(),
		listLibraryItems: vi.fn(),
		getLibraryFacets: vi.fn(),
		listCollections: vi.fn(),
		listInspirations: vi.fn(),
		listInspirationCollections: vi.fn()
	}
}));

vi.mock('$lib/stores/confirm', () => ({ confirmDialog: vi.fn() }));

const { api } = await import('$lib/services/api/index');
const { libraryStore } = await import('$lib/stores/library');
const { libraryCollectionsStore } = await import('$lib/stores/collections');
const { inspirationsStore } = await import('$lib/stores/inspirations');
const { inspirationsCollectionsStore } = await import('$lib/stores/inspirationsCollections');
const { resetLayerStackForTests } = await import('$lib/actions/layerStack');
const { default: LibraryToolbar } = await import('../../src/routes/library/components/LibraryToolbar.svelte');
const { default: InspirationsToolbar } = await import(
	'../../src/routes/inspirations/components/InspirationsToolbar.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

const filtersButton = () =>
	Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.includes('Filters'));
const includeBox = () =>
	document.querySelector<HTMLInputElement>('[data-testid="include-subcollections"] input[type="checkbox"]');
const clearAll = () =>
	Array.from(document.querySelectorAll<HTMLButtonElement>('[data-filters-popover] button')).find(
		(b) => b.textContent?.trim() === 'Clear all'
	)!;

let destroy: (() => void) | undefined;

function mount(component: unknown, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: component as never, target, props });
	destroy = () => {
		instance.$destroy();
		target.remove();
	};
}

beforeEach(async () => {
	resetLayerStackForTests();
	vi.mocked(api.listLibraryItems).mockResolvedValue({ success: true, data: { items: [], total: 0 } } as never);
	vi.mocked(api.getLibraryFacets).mockResolvedValue({ success: true, data: { media_types: {} } } as never);
	vi.mocked(api.listCollections).mockResolvedValue({
		success: true,
		data: { collections, total: collections.length, smart_counts: null }
	} as never);
	vi.mocked(api.listInspirations).mockResolvedValue({ success: true, data: { items: [], total: 0 } } as never);
	vi.mocked(api.listInspirationCollections).mockResolvedValue({
		success: true,
		data: { items: collections, smart_counts: null }
	} as never);
	await libraryCollectionsStore.load();
	await inspirationsCollectionsStore.load();
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
	libraryStore.setFilter('collectionId', undefined);
	libraryStore.setFilter('directOnly', false);
	inspirationsStore.setFilter('collectionId', undefined);
	inspirationsStore.setFilter('directOnly', false);
	document.body.innerHTML = '';
});

const pages = [
	{
		name: 'Library',
		mountPage: () => mount(LibraryToolbar, { onOpenAddTag: () => {}, onPickFiles: () => {}, onOpenDeleteByCriteria: () => {} }),
		select: (id: string | undefined) => libraryStore.setFilter('collectionId', id),
		directOnly: () => !!get(libraryStore).filters.directOnly,
		reload: () => api.listLibraryItems
	},
	{
		name: 'Inspirations',
		mountPage: () => mount(InspirationsToolbar, {}),
		select: (id: string | undefined) => inspirationsStore.setFilter('collectionId', id),
		directOnly: () => !!get(inspirationsStore).filters.directOnly,
		reload: () => api.listInspirations
	}
];

describe.each(pages)('$name toolbar subcollection filters', (page) => {
	it('shows the Filters button only for a collection with subcollections', async () => {
		page.select('glass');
		page.mountPage();
		await settle();
		expect(filtersButton()).toBeUndefined();
		page.select('potions');
		await settle();
		expect(filtersButton()).toBeDefined();
	});

	it('turning the row off sets direct only, reloads and counts in the badge; Clear all restores it', async () => {
		page.select('potions');
		page.mountPage();
		await settle();
		filtersButton()!.click();
		await settle();
		vi.mocked(page.reload()).mockClear();
		includeBox()!.click();
		await settle();
		expect(page.directOnly()).toBe(true);
		expect(page.reload()).toHaveBeenCalled();
		expect(filtersButton()!.textContent?.replace(/\s+/g, ' ').trim()).toBe('Filters 1');
		clearAll().click();
		await settle();
		expect(page.directOnly()).toBe(false);
		expect(filtersButton()!.textContent?.trim()).toBe('Filters');
	});
});
