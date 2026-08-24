import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockListLibraryItems = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		listLibraryItems: (...args: unknown[]) => mockListLibraryItems(...args),
		getLibraryFacets: vi.fn(),
		getTags: vi.fn()
	}
}));

import { libraryStore } from './library';

describe('libraryStore.reset()', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		libraryStore.reset();
		mockListLibraryItems.mockResolvedValue({
			success: true,
			data: { items: [{ id: 'item-1' }], total: 1 }
		});
	});

	it('clears the selected-item detail a stale route remount would otherwise render', async () => {
		await libraryStore.load();
		const [item] = get(libraryStore).items;
		libraryStore.setSelectedItem(item as any);
		expect(get(libraryStore).selectedItem).not.toBeNull();

		libraryStore.reset();

		expect(get(libraryStore).selectedItem).toBeNull();
	});

	it('also drops the loaded list, selection, and paging back to their initial values', async () => {
		await libraryStore.load();
		libraryStore.toggleSelect('item-1');
		libraryStore.setPage(3);

		libraryStore.reset();

		const state = get(libraryStore);
		expect(state.items).toEqual([]);
		expect(state.totalCount).toBe(0);
		expect(state.currentPage).toBe(1);
		expect(state.selectedIds).toEqual([]);
		expect(state.selectionMode).toBe(false);
	});
});
