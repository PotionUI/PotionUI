import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const mockGetGenerationHistory = vi.fn();

vi.mock('$app/environment', () => ({ browser: false }));
vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationHistory: (...args: unknown[]) => mockGetGenerationHistory(...args),
		getHistoryFacets: vi.fn(),
		getTags: vi.fn()
	}
}));

function page(prefix: string, count: number) {
	return Array.from({ length: count }, (_, i) => ({
		id: `${prefix}-${i}`,
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files: [],
		rating: 0,
		is_favorite: false
	}));
}

const PAGE_SIZE = 6;
const TOTAL = 12;
const first = page('a', PAGE_SIZE);
const second = page('b', PAGE_SIZE);

// The store is a module singleton, so each test imports a fresh copy.
async function freshStore() {
	vi.resetModules();
	const module = await import('./history');
	module.historyStore.setItemsPerPage(PAGE_SIZE);
	return module;
}

function answerWith(pages: Record<number, unknown[]>, total = TOTAL) {
	mockGetGenerationHistory.mockImplementation((request: { offset: number }) => {
		const generations = pages[request.offset / PAGE_SIZE] ?? [];
		return Promise.resolve({ success: true, data: { generations, total } });
	});
}

describe('stores/history selectAdjacentGeneration', () => {
	beforeEach(() => {
		mockGetGenerationHistory.mockReset();
	});

	it('steps to the next generation inside the loaded page without refetching', async () => {
		const { historyStore } = await freshStore();
		answerWith({ 0: first });
		await historyStore.loadGenerations();
		historyStore.setSelectedGeneration(first[2] as never, 0);
		mockGetGenerationHistory.mockClear();

		await expect(historyStore.selectAdjacentGeneration(1)).resolves.toBe(true);

		expect(get(historyStore).selectedGeneration?.id).toBe('a-3');
		expect(get(historyStore).selectedFileIndex).toBe(0);
		expect(mockGetGenerationHistory).not.toHaveBeenCalled();
	});

	it('steps back inside the page and asks for the neighbour last file', async () => {
		const { historyStore } = await freshStore();
		answerWith({ 0: first });
		await historyStore.loadGenerations();
		historyStore.setSelectedGeneration(first[2] as never, 0);

		await expect(historyStore.selectAdjacentGeneration(-1)).resolves.toBe(true);

		expect(get(historyStore).selectedGeneration?.id).toBe('a-1');
		expect(get(historyStore).selectedFileIndex).toBe(-1);
	});

	it('crosses forward over a page edge and selects the next page first item', async () => {
		const { historyStore } = await freshStore();
		answerWith({ 0: first, 1: second });
		await historyStore.loadGenerations();
		historyStore.setSelectedGeneration(first[PAGE_SIZE - 1] as never, 0);

		await expect(historyStore.selectAdjacentGeneration(1)).resolves.toBe(true);

		const state = get(historyStore);
		expect(state.currentPage).toBe(2);
		expect(state.selectedGeneration?.id).toBe('b-0');
		expect(state.selectedFileIndex).toBe(0);
		expect(mockGetGenerationHistory).toHaveBeenLastCalledWith(
			expect.objectContaining({ offset: PAGE_SIZE, limit: PAGE_SIZE })
		);
	});

	it('crosses backward over a page edge and selects the previous page last item', async () => {
		const { historyStore } = await freshStore();
		answerWith({ 0: first, 1: second });
		historyStore.setPage(2);
		await historyStore.loadGenerations();
		historyStore.setSelectedGeneration(second[0] as never, 0);

		await expect(historyStore.selectAdjacentGeneration(-1)).resolves.toBe(true);

		const state = get(historyStore);
		expect(state.currentPage).toBe(1);
		expect(state.selectedGeneration?.id).toBe('a-5');
		expect(state.selectedFileIndex).toBe(-1);
	});

	it('is a no-op at either end of the whole list', async () => {
		const { historyStore } = await freshStore();
		answerWith({ 0: first, 1: second });
		historyStore.setPage(2);
		await historyStore.loadGenerations();
		historyStore.setSelectedGeneration(second[PAGE_SIZE - 1] as never, 0);
		mockGetGenerationHistory.mockClear();

		await expect(historyStore.selectAdjacentGeneration(1)).resolves.toBe(false);
		expect(get(historyStore).selectedGeneration?.id).toBe('b-5');
		expect(get(historyStore).currentPage).toBe(2);
		expect(mockGetGenerationHistory).not.toHaveBeenCalled();
	});

	it('keeps the page and selection when the adjacent page fails to load', async () => {
		const { historyStore } = await freshStore();
		answerWith({ 0: first, 1: second });
		await historyStore.loadGenerations();
		historyStore.setSelectedGeneration(first[PAGE_SIZE - 1] as never, 0);

		mockGetGenerationHistory.mockResolvedValue({ success: false, error: 'boom' });
		await expect(historyStore.selectAdjacentGeneration(1)).rejects.toThrow();

		const state = get(historyStore);
		expect(state.currentPage).toBe(1);
		expect(state.selectedGeneration?.id).toBe('a-5');
		expect(state.generations).toHaveLength(PAGE_SIZE);
	});

	it('reports the position of the open generation across pages', async () => {
		const { historyStore, selectedPosition, hasPreviousGeneration, hasNextGeneration } =
			await freshStore();
		answerWith({ 0: first, 1: second });
		historyStore.setPage(2);
		await historyStore.loadGenerations();

		historyStore.setSelectedGeneration(second[0] as never, 0);
		expect(get(selectedPosition)).toEqual({ index: 7, total: TOTAL });
		expect(get(hasPreviousGeneration)).toBe(true);
		expect(get(hasNextGeneration)).toBe(true);

		historyStore.setSelectedGeneration(second[PAGE_SIZE - 1] as never, 0);
		expect(get(selectedPosition)).toEqual({ index: TOTAL, total: TOTAL });
		expect(get(hasNextGeneration)).toBe(false);

		historyStore.setSelectedGeneration(null);
		expect(get(selectedPosition)).toBeNull();
		expect(get(hasPreviousGeneration)).toBe(false);
	});
});
