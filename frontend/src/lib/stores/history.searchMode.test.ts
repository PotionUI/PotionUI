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

describe('stores/history search mode (keyword vs semantic)', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		historyStore.reset();
		mockGetGenerationHistory.mockResolvedValue({
			success: true,
			data: { generations: [], total: 0 }
		});
	});

	it('defaults to keyword mode and sends search, not semanticQuery', async () => {
		expect(get(historyStore).filters.searchMode).toBe('keyword');
		historyStore.setFilter('search', 'red fox');
		await historyStore.loadGenerations();
		expect(mockGetGenerationHistory).toHaveBeenCalledWith(
			expect.objectContaining({ search: 'red fox', semanticQuery: undefined })
		);
	});

	it('semantic mode sends semanticQuery instead of search', async () => {
		historyStore.setFilter('search', 'castle at dusk');
		historyStore.setFilter('searchMode', 'semantic');
		await historyStore.loadGenerations();
		expect(mockGetGenerationHistory).toHaveBeenCalledWith(
			expect.objectContaining({ search: undefined, semanticQuery: 'castle at dusk' })
		);
	});

	it('switching mode resets pagination', () => {
		historyStore.setPage(3);
		historyStore.setFilter('searchMode', 'semantic');
		expect(get(historyStore).currentPage).toBe(1);
	});

	it('clearFilters restores keyword mode', () => {
		historyStore.setFilter('searchMode', 'semantic');
		historyStore.clearFilters();
		expect(get(historyStore).filters.searchMode).toBe('keyword');
	});
});
