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

describe('historyStore.reset()', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		historyStore.reset();
		mockGetGenerationHistory.mockResolvedValue({
			success: true,
			data: { generations: [{ id: 'gen-1', status: 'completed' }], total: 1 }
		});
	});

	it('clears the selected-generation detail a stale route remount would otherwise render', async () => {
		await historyStore.loadGenerations();
		const [generation] = get(historyStore).generations;
		historyStore.setSelectedGeneration(generation as any, 2);
		expect(get(historyStore).selectedGeneration).not.toBeNull();

		historyStore.reset();

		const state = get(historyStore);
		expect(state.selectedGeneration).toBeNull();
		expect(state.selectedFileIndex).toBe(0);
	});

	it('also drops the loaded list, selection, and paging back to their initial values', async () => {
		await historyStore.loadGenerations();
		historyStore.toggleSelect('gen-1');
		historyStore.setPage(3);

		historyStore.reset();

		const state = get(historyStore);
		expect(state.generations).toEqual([]);
		expect(state.totalCount).toBe(0);
		expect(state.currentPage).toBe(1);
		expect(state.selectedGenerationIds).toEqual([]);
		expect(state.selectionMode).toBe(false);
	});
});
