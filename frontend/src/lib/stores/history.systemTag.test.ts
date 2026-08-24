import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockGetGenerationHistory = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationHistory: (...args: unknown[]) => mockGetGenerationHistory(...args),
		getHistoryFacets: vi.fn(),
		getTags: vi.fn()
	}
}));

import { historyStore } from './history';

describe('stores/history system-tag facet', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		historyStore.reset();
		mockGetGenerationHistory.mockResolvedValue({
			success: true,
			data: { generations: [], total: 0 }
		});
	});

	it('setSystemTagFilter stores the tag and resets pagination', () => {
		historyStore.setPage(3);
		historyStore.setSystemTagFilter('1girl');
		const state = get(historyStore);
		expect(state.filters.systemTag).toBe('1girl');
		expect(state.currentPage).toBe(1);
	});

	it('loadGenerations forwards systemTag to the API', async () => {
		historyStore.setSystemTagFilter('outdoors');
		await historyStore.loadGenerations();
		expect(mockGetGenerationHistory).toHaveBeenCalledWith(
			expect.objectContaining({ systemTag: 'outdoors' })
		);
	});

	it('setSystemTagFilter(null) and clearFilters both clear the facet', async () => {
		historyStore.setSystemTagFilter('1girl');
		historyStore.setSystemTagFilter(null);
		expect(get(historyStore).filters.systemTag).toBeUndefined();

		historyStore.setSystemTagFilter('1girl');
		historyStore.clearFilters();
		expect(get(historyStore).filters.systemTag).toBeUndefined();

		await historyStore.loadGenerations();
		expect(mockGetGenerationHistory).toHaveBeenCalledWith(
			expect.objectContaining({ systemTag: undefined })
		);
	});
});
