import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockGetPhrasebookCategory = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getPhrasebookCategories: vi.fn(),
		getPhrasebookCategory: (...args: unknown[]) => mockGetPhrasebookCategory(...args)
	}
}));

import { phrasebookStore } from './phrasebook';

describe('phrasebookStore.reset()', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		phrasebookStore.reset();
		mockGetPhrasebookCategory.mockResolvedValue({
			success: true,
			data: { values: [{ id: 'val-1', category_id: 'cat-1', label: 'Sunset', value: 'sunset', sort_order: 0 }] }
		});
	});

	it('clears the selected category/value a stale route remount would otherwise render', async () => {
		phrasebookStore.setSelectedCategoryId('cat-1');
		phrasebookStore.setSelectedValueId('val-1');
		await phrasebookStore.loadCategoryValues('cat-1');
		expect(get(phrasebookStore).categoryValues['cat-1']).toBeDefined();

		phrasebookStore.reset();

		const state = get(phrasebookStore);
		expect(state.selectedCategoryId).toBeNull();
		expect(state.selectedValueId).toBeNull();
		expect(state.categoryValues).toEqual({});
	});

	it('also drops the loaded category tree back to its initial value', () => {
		phrasebookStore.setSelectedCategoryId('cat-1');

		phrasebookStore.reset();

		const state = get(phrasebookStore);
		expect(state.categories).toEqual({});
		expect(state.rootCategoryIds).toEqual([]);
		expect(state.allCategories).toEqual([]);
		expect(state.editMode).toBe('none');
	});
});
