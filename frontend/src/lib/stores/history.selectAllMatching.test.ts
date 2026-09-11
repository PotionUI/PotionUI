import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockGetGenerationHistory = vi.fn();
const mockGetMatchingGenerationIds = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationHistory: (...args: unknown[]) => mockGetGenerationHistory(...args),
		getMatchingGenerationIds: (...args: unknown[]) => mockGetMatchingGenerationIds(...args),
		getHistoryFacets: vi.fn(),
		getTags: vi.fn()
	}
}));

import { historyStore } from './history';

describe('stores/history setSelection / selectAllMatching', () => {
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
				total: 50
			}
		});
	});

	it('setSelection() replaces the selection outright and turns selection mode on', () => {
		historyStore.toggleSelect('gen-1');

		historyStore.setSelection(['gen-2', 'gen-3']);

		const state = get(historyStore);
		expect(state.selectedGenerationIds).toEqual(['gen-2', 'gen-3']);
		expect(state.selectionMode).toBe(true);
	});

	it('setSelection([]) turns selection mode back off', () => {
		historyStore.setSelection(['gen-1']);
		historyStore.setSelection([]);

		const state = get(historyStore);
		expect(state.selectedGenerationIds).toEqual([]);
		expect(state.selectionMode).toBe(false);
	});

	it('selectAllMatching() replaces the selection with every matching id, not just the loaded page', async () => {
		await historyStore.loadGenerations();
		historyStore.selectAll();
		mockGetMatchingGenerationIds.mockResolvedValue({
			success: true,
			data: { ids: ['gen-1', 'gen-2', 'gen-3', 'gen-4'], total: 4, truncated: false }
		});

		const result = await historyStore.selectAllMatching();

		const state = get(historyStore);
		expect(state.selectedGenerationIds).toEqual(['gen-1', 'gen-2', 'gen-3', 'gen-4']);
		expect(state.selectionMode).toBe(true);
		expect(result).toEqual({ count: 4, total: 4, truncated: false });
	});

	it('selectAllMatching() reports truncation without pretending the capped ids are everything', async () => {
		mockGetMatchingGenerationIds.mockResolvedValue({
			success: true,
			data: { ids: ['gen-1', 'gen-2'], total: 500, truncated: true }
		});

		const result = await historyStore.selectAllMatching();

		expect(result).toEqual({ count: 2, total: 500, truncated: true });
		expect(get(historyStore).selectedGenerationIds).toHaveLength(2);
	});

	it('selectAllMatching() leaves the selection untouched and returns null on failure', async () => {
		historyStore.setSelection(['gen-1']);
		mockGetMatchingGenerationIds.mockResolvedValue({ success: false });

		const result = await historyStore.selectAllMatching();

		expect(result).toBeNull();
		expect(get(historyStore).selectedGenerationIds).toEqual(['gen-1']);
	});
});
