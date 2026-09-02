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

const VALUES = [
	{ id: 'v1', category_id: 'cat-1', label: 'Golden hour', value: 'golden', sort_order: 0, is_active: true, preview_file_id: 'file-1', created_at: '', updated_at: '' },
	{ id: 'v2', category_id: 'cat-1', label: 'Softbox', value: 'softbox', sort_order: 1, is_active: true, created_at: '', updated_at: '' },
	{ id: 'v3', category_id: 'cat-1', label: 'Neon', value: 'neon', sort_order: 2, is_active: true, created_at: '', updated_at: '' },
	{ id: 'v4', category_id: 'cat-1', label: 'Hard noon sun', value: 'noon', sort_order: 3, is_active: false, created_at: '', updated_at: '' }
];

describe('phrasebookStore.selectValuesWithoutPreview()', () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		phrasebookStore.reset();
		mockGetPhrasebookCategory.mockResolvedValue({ success: true, data: { values: VALUES } });
		phrasebookStore.setSelectedCategoryId('cat-1');
		await phrasebookStore.loadCategoryValues('cat-1');
	});

	it('selects exactly the active values that have no preview yet', () => {
		phrasebookStore.selectValuesWithoutPreview();

		const state = get(phrasebookStore);
		expect(state.selectedValueIds).toEqual(new Set(['v2', 'v3']));
	});

	it('replaces whatever was selected before, rather than adding to it', () => {
		phrasebookStore.selectValueIds(['v1']);

		phrasebookStore.selectValuesWithoutPreview();

		expect(get(phrasebookStore).selectedValueIds).toEqual(new Set(['v2', 'v3']));
	});
});
