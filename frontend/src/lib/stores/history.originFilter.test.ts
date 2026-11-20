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

import { buildHistoryRequest, historyQueryKey, historyStore } from './history';

describe('the History source filter', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		historyStore.reset();
		mockGetGenerationHistory.mockResolvedValue({ success: true, data: { generations: [], total: 0 } });
	});

	it('sends no origin until one is chosen', () => {
		expect(buildHistoryRequest(get(historyStore)).origin).toBeUndefined();
	});

	it('asks the server for the chosen source, from the first page', async () => {
		historyStore.setPage(3);
		historyStore.setOriginFilter('mcp');
		const state = get(historyStore);
		expect(state.currentPage).toBe(1);
		expect(buildHistoryRequest(state).origin).toBe('mcp');
		await historyStore.loadGenerations();
		expect(mockGetGenerationHistory).toHaveBeenLastCalledWith(expect.objectContaining({ origin: 'mcp' }));
	});

	it('changes the list identity, so a stale answer for the old source cannot land', () => {
		const before = historyQueryKey(get(historyStore));
		historyStore.setOriginFilter('chat');
		expect(historyQueryKey(get(historyStore))).not.toBe(before);
	});

	it('clears with the other filters and on a null choice', () => {
		historyStore.setOriginFilter('editor');
		historyStore.setOriginFilter(null);
		expect(get(historyStore).filters.origin).toBeUndefined();
		historyStore.setOriginFilter('tab');
		historyStore.clearFilters();
		expect(get(historyStore).filters.origin).toBeUndefined();
	});
});
