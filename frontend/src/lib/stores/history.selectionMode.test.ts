import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockGetGenerationHistory = vi.fn();

const mockBulkDeleteGenerations = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationHistory: (...args: unknown[]) => mockGetGenerationHistory(...args),
		getHistoryFacets: vi.fn(),
		getTags: vi.fn(),
		bulkDeleteGenerations: (...args: unknown[]) => mockBulkDeleteGenerations(...args)
	}
}));

import { historyStore } from './history';

describe('stores/history selectionMode invariant', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		historyStore.reset();
		mockGetGenerationHistory.mockResolvedValue({
			success: true,
			data: {
				generations: [
					{ id: 'gen-1', status: 'completed' },
					{ id: 'gen-2', status: 'completed' }
				],
				total: 2
			}
		});
	});

	it('clearSelection() turns selectionMode off, matching selectedGenerationIds being empty', async () => {
		historyStore.toggleSelect('gen-1');
		expect(get(historyStore).selectionMode).toBe(true);

		historyStore.clearSelection();
		const state = get(historyStore);
		expect(state.selectedGenerationIds).toEqual([]);
		expect(state.selectionMode).toBe(false);
	});

	it('selectAll() with a non-empty page turns selectionMode on', async () => {
		await historyStore.loadGenerations();
		historyStore.selectAll();
		const state = get(historyStore);
		expect(state.selectedGenerationIds).toEqual(['gen-1', 'gen-2']);
		expect(state.selectionMode).toBe(true);
	});

	it('toggleGenerationSelection() keeps selectionMode in sync with the id list', () => {
		historyStore.toggleGenerationSelection('gen-1');
		expect(get(historyStore).selectionMode).toBe(true);

		historyStore.toggleGenerationSelection('gen-1');
		const state = get(historyStore);
		expect(state.selectedGenerationIds).toEqual([]);
		expect(state.selectionMode).toBe(false);
	});

	it('bulkDeleteGenerations() leaves selectionMode off once the selection is empty', async () => {
		mockBulkDeleteGenerations.mockResolvedValue({ success: true });
		mockGetGenerationHistory.mockResolvedValue({
			success: true,
			data: { generations: [], total: 0 }
		});

		historyStore.toggleSelect('gen-1');
		await historyStore.bulkDeleteGenerations();

		const state = get(historyStore);
		expect(state.selectedGenerationIds).toHaveLength(0);
		expect(state.selectionMode).toBe(false);
	});
});
