import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const values = new Map<string, string>();
const mockGetGenerationHistory = vi.fn();

vi.mock('$app/environment', () => ({ browser: true }));
vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationHistory: (...args: unknown[]) => mockGetGenerationHistory(...args),
		getHistoryFacets: vi.fn(),
		getTags: vi.fn()
	}
}));

describe('stores/history pagination preference', () => {
	beforeEach(() => {
		values.clear();
		mockGetGenerationHistory.mockReset();
		mockGetGenerationHistory.mockResolvedValue({
			success: true,
			data: { generations: [], total: 0 }
		});
		vi.stubGlobal('localStorage', {
			getItem: (key: string) => values.get(key) ?? null,
			setItem: (key: string, value: string) => values.set(key, value)
		});
	});

	afterEach(() => vi.unstubAllGlobals());

	it('restores a saved page size before the initial history request', async () => {
		vi.resetModules();
		const { historyStore } = await import('./history');

		values.set('history-items-per-page', '48');
		historyStore.restoreItemsPerPage();
		expect(get(historyStore).itemsPerPage).toBe(48);
		await historyStore.loadGenerations();
		expect(mockGetGenerationHistory).toHaveBeenCalledWith(
			expect.objectContaining({ limit: 48, offset: 0 })
		);

		historyStore.setPage(3);
		historyStore.setItemsPerPage(48);
		expect(get(historyStore)).toMatchObject({ itemsPerPage: 48, currentPage: 1 });
		expect(values.get('history-items-per-page')).toBe('48');
	});

	it('falls back to the default for a malformed saved page size', async () => {
		values.set('history-items-per-page', '25');
		vi.resetModules();
		const { historyStore } = await import('./history');

		historyStore.restoreItemsPerPage();
		expect(get(historyStore).itemsPerPage).toBe(24);
	});

	it('keeps history usable when browser storage throws', async () => {
		vi.stubGlobal('localStorage', {
			getItem: () => {
				throw new Error('Storage blocked');
			},
			setItem: () => {
				throw new Error('Storage blocked');
			}
		});
		vi.resetModules();
		const { historyStore } = await import('./history');

		historyStore.restoreItemsPerPage();
		historyStore.setItemsPerPage(12);
		expect(get(historyStore).itemsPerPage).toBe(12);
	});
});
