import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

type Deferred<T> = {
	promise: Promise<T>;
	resolve: (value: T) => void;
	reject: (reason: unknown) => void;
};

function deferred<T>(): Deferred<T> {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

function pageOf(rows: Array<{ id: string; status?: string; progress?: number }>, total?: number) {
	return {
		success: true,
		data: {
			generations: rows.map((row) => ({ status: 'completed', ...row })),
			total: total ?? rows.length
		}
	};
}

function ids(store: { generations: Array<{ id: string }> }) {
	return store.generations.map((generation) => generation.id);
}

type HistoryModule = typeof import('./history');

describe('stores/history request lifecycle', () => {
	let historyStore: HistoryModule['historyStore'];

	beforeEach(async () => {
		mockGetGenerationHistory.mockReset();
		vi.spyOn(console, 'error').mockImplementation(() => {});
		vi.resetModules();
		({ historyStore } = await import('./history'));
	});

	afterEach(() => vi.restoreAllMocks());

	it('commits the newest issued request when an older one resolves last', async () => {
		const stale = deferred<unknown>();
		const fresh = deferred<unknown>();
		mockGetGenerationHistory.mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise);

		// Same query, two intents: the foreground load and the background poll the
		// history page fires every 6s. Neither coalesces onto the other.
		const first = historyStore.loadGenerations();
		const second = historyStore.loadGenerations({ silent: true, merge: true });
		expect(mockGetGenerationHistory).toHaveBeenCalledTimes(2);

		fresh.resolve(pageOf([{ id: 'fresh-1' }]));
		await second;
		stale.resolve(pageOf([{ id: 'stale-1' }], 99));
		await first;

		const state = get(historyStore);
		expect(ids(state)).toEqual(['fresh-1']);
		expect(state.totalCount).toBe(1);
	});

	it('drops a response whose filters no longer match the store', async () => {
		const stale = deferred<unknown>();
		const fresh = deferred<unknown>();
		mockGetGenerationHistory.mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise);

		const first = historyStore.loadGenerations();
		historyStore.setFilter('search', 'red fox');
		const second = historyStore.loadGenerations();

		fresh.resolve(pageOf([{ id: 'fox-1' }]));
		await second;
		stale.resolve(pageOf([{ id: 'unfiltered-1' }]));
		await first;

		expect(ids(get(historyStore))).toEqual(['fox-1']);
	});

	it('drops a response whose page no longer matches the store', async () => {
		const stale = deferred<unknown>();
		const fresh = deferred<unknown>();
		mockGetGenerationHistory.mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise);

		const first = historyStore.loadGenerations();
		historyStore.setPage(2);
		const second = historyStore.loadGenerations();
		expect(mockGetGenerationHistory).toHaveBeenLastCalledWith(
			expect.objectContaining({ offset: 24 })
		);

		fresh.resolve(pageOf([{ id: 'page2-1' }]));
		await second;
		stale.resolve(pageOf([{ id: 'page1-1' }]));
		await first;

		expect(ids(get(historyStore))).toEqual(['page2-1']);
	});

	it('commits the query the user came back to when it resolves before the abandoned one', async () => {
		const onA = deferred<unknown>();
		const onB = deferred<unknown>();
		mockGetGenerationHistory.mockReturnValueOnce(onA.promise).mockReturnValueOnce(onB.promise);

		const firstA = historyStore.loadGenerations();
		historyStore.setFilter('search', 'red fox');
		const forB = historyStore.loadGenerations();
		historyStore.setFilter('search', '');
		const secondA = historyStore.loadGenerations();

		// The re-request rides the still-pending fetch for the same query.
		expect(mockGetGenerationHistory).toHaveBeenCalledTimes(2);

		onA.resolve(pageOf([{ id: 'unfiltered-1' }]));
		onB.resolve(pageOf([{ id: 'fox-1' }]));
		await Promise.all([firstA, forB, secondA]);

		const state = get(historyStore);
		expect(ids(state)).toEqual(['unfiltered-1']);
		expect(state.loading).toBe(false);
	});

	it('commits the query the user came back to when the abandoned one resolves first', async () => {
		const onA = deferred<unknown>();
		const onB = deferred<unknown>();
		mockGetGenerationHistory.mockReturnValueOnce(onA.promise).mockReturnValueOnce(onB.promise);

		const firstA = historyStore.loadGenerations();
		historyStore.setFilter('search', 'red fox');
		const forB = historyStore.loadGenerations();
		historyStore.setFilter('search', '');
		const secondA = historyStore.loadGenerations();

		onB.resolve(pageOf([{ id: 'fox-1' }]));
		onA.resolve(pageOf([{ id: 'unfiltered-1' }]));
		await Promise.all([firstA, forB, secondA]);

		const state = get(historyStore);
		expect(ids(state)).toEqual(['unfiltered-1']);
		expect(state.loading).toBe(false);
	});

	it('drops the spinner once the current request resolves, with an obsolete one still running', async () => {
		const abandoned = deferred<unknown>();
		const current = deferred<unknown>();
		mockGetGenerationHistory
			.mockReturnValueOnce(abandoned.promise)
			.mockReturnValueOnce(current.promise);

		const first = historyStore.loadGenerations();
		historyStore.setPage(2);
		const second = historyStore.loadGenerations();

		current.resolve(pageOf([{ id: 'page2-1' }]));
		await second;
		expect(get(historyStore).loading).toBe(false);

		abandoned.resolve(pageOf([{ id: 'page1-1' }]));
		await first;

		const state = get(historyStore);
		expect(ids(state)).toEqual(['page2-1']);
		expect(state.loading).toBe(false);
	});

	it('an obsolete completion leaves the current request spinner up', async () => {
		const abandoned = deferred<unknown>();
		const current = deferred<unknown>();
		mockGetGenerationHistory
			.mockReturnValueOnce(abandoned.promise)
			.mockReturnValueOnce(current.promise);

		const first = historyStore.loadGenerations();
		historyStore.setPage(2);
		const second = historyStore.loadGenerations();

		abandoned.resolve(pageOf([{ id: 'page1-1' }]));
		await first;
		expect(get(historyStore).loading).toBe(true);
		expect(get(historyStore).generations).toEqual([]);

		current.resolve(pageOf([{ id: 'page2-1' }]));
		await second;

		const state = get(historyStore);
		expect(ids(state)).toEqual(['page2-1']);
		expect(state.loading).toBe(false);
	});

	it('a superseded failure leaves the newer result and its loading state alone', async () => {
		const stale = deferred<unknown>();
		const fresh = deferred<unknown>();
		mockGetGenerationHistory.mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise);

		const first = historyStore.loadGenerations();
		historyStore.setPage(2);
		const second = historyStore.loadGenerations();

		fresh.resolve(pageOf([{ id: 'page2-1' }]));
		await second;
		expect(get(historyStore).loading).toBe(false);

		stale.reject(new Error('network down'));
		await expect(first).resolves.toBeUndefined();

		const state = get(historyStore);
		expect(ids(state)).toEqual(['page2-1']);
		expect(state.loading).toBe(false);
	});

	it('a superseded success does not resurrect the old page after the newest request failed', async () => {
		const stale = deferred<unknown>();
		const fresh = deferred<unknown>();
		mockGetGenerationHistory.mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise);

		const first = historyStore.loadGenerations();
		historyStore.setPage(2);
		const second = historyStore.loadGenerations();

		fresh.reject(new Error('boom'));
		await second;
		expect(get(historyStore).generations).toEqual([]);

		stale.resolve(pageOf([{ id: 'page1-1' }]));
		await first;

		const state = get(historyStore);
		expect(state.generations).toEqual([]);
		expect(state.loading).toBe(false);
	});

	it('coalesces two identical background refreshes into one request', async () => {
		const inFlight = deferred<unknown>();
		mockGetGenerationHistory.mockReturnValueOnce(inFlight.promise);

		const first = historyStore.loadGenerations({ silent: true, merge: true });
		const second = historyStore.loadGenerations({ silent: true, merge: true });
		expect(mockGetGenerationHistory).toHaveBeenCalledTimes(1);

		inFlight.resolve(pageOf([{ id: 'gen-1' }]));
		await Promise.all([first, second]);

		expect(ids(get(historyStore))).toEqual(['gen-1']);
	});

	it('a foreground load during a background refresh owns the loading state and wins', async () => {
		const background = deferred<unknown>();
		const foreground = deferred<unknown>();
		mockGetGenerationHistory
			.mockReturnValueOnce(background.promise)
			.mockReturnValueOnce(foreground.promise);

		const silent = historyStore.loadGenerations({ silent: true, merge: true });
		expect(get(historyStore).loading).toBe(false);

		const visible = historyStore.loadGenerations();
		expect(mockGetGenerationHistory).toHaveBeenCalledTimes(2);
		expect(get(historyStore).loading).toBe(true);

		background.resolve(pageOf([{ id: 'polled-1' }]));
		await silent;
		expect(get(historyStore).loading).toBe(true);

		foreground.resolve(pageOf([{ id: 'requested-1' }]));
		await visible;

		const state = get(historyStore);
		expect(state.loading).toBe(false);
		expect(ids(state)).toEqual(['requested-1']);
	});

	it('keeps a live progress update that arrives while a merging refresh is in flight', async () => {
		mockGetGenerationHistory.mockResolvedValueOnce(
			pageOf([{ id: 'gen-1', status: 'pending', progress: 0 }])
		);
		await historyStore.loadGenerations();

		const refresh = deferred<unknown>();
		mockGetGenerationHistory.mockReturnValueOnce(refresh.promise);
		const pending = historyStore.loadGenerations({ silent: true, merge: true });

		historyStore.applyLiveStatus('gen-1', 'running', 77);
		refresh.resolve(pageOf([{ id: 'gen-1', status: 'pending', progress: 0 }]));
		await pending;

		expect(get(historyStore).generations[0]).toMatchObject({ status: 'running', progress: 77 });
	});

	it('lets a terminal server status overwrite the local live status on merge', async () => {
		mockGetGenerationHistory.mockResolvedValueOnce(
			pageOf([{ id: 'gen-1', status: 'pending', progress: 0 }])
		);
		await historyStore.loadGenerations();
		historyStore.applyLiveStatus('gen-1', 'running', 50);

		mockGetGenerationHistory.mockResolvedValueOnce(
			pageOf([{ id: 'gen-1', status: 'completed', progress: 100 }], 3)
		);
		await historyStore.loadGenerations({ silent: true, merge: true });

		const state = get(historyStore);
		expect(state.generations[0]).toMatchObject({ status: 'completed', progress: 100 });
		expect(state.totalCount).toBe(3);
	});

	it('a non-merging load overwrites the local live status outright', async () => {
		mockGetGenerationHistory.mockResolvedValueOnce(
			pageOf([{ id: 'gen-1', status: 'pending', progress: 0 }])
		);
		await historyStore.loadGenerations();
		historyStore.applyLiveStatus('gen-1', 'running', 50);

		mockGetGenerationHistory.mockResolvedValueOnce(
			pageOf([{ id: 'gen-1', status: 'pending', progress: 0 }])
		);
		historyStore.setPage(2);
		await historyStore.loadGenerations();

		expect(get(historyStore).generations[0]).toMatchObject({ status: 'pending', progress: 0 });
	});

	it('clears loading when the request fails outright', async () => {
		mockGetGenerationHistory.mockRejectedValueOnce(new Error('network down'));
		await historyStore.loadGenerations();
		expect(get(historyStore).loading).toBe(false);
	});

	it('clears loading when the API answers unsuccessfully', async () => {
		mockGetGenerationHistory.mockResolvedValueOnce({ success: false });
		await historyStore.loadGenerations();
		expect(get(historyStore).loading).toBe(false);
	});
});

describe('historyQueryKey', () => {
	let historyQueryKey: HistoryModule['historyQueryKey'];

	beforeEach(async () => {
		vi.resetModules();
		({ historyQueryKey } = await import('./history'));
	});

	function baseState() {
		return {
			currentPage: 1,
			itemsPerPage: 24,
			filters: {
				status: 'all',
				datePreset: 'all',
				dateFrom: undefined,
				dateTo: undefined,
				selectedTagIds: [],
				mediaType: 'all',
				search: '',
				searchMode: 'keyword',
				minRating: undefined,
				favoritesOnly: false,
				mode: undefined,
				presetId: undefined,
				modelName: undefined,
				collectionId: undefined,
				usedPhrasebookValueId: undefined,
				usedPhrasebookLabel: undefined,
				systemTag: undefined,
				sortBy: 'created_at',
				sortDir: 'desc'
			}
		} as Parameters<HistoryModule['historyQueryKey']>[0];
	}

	it('is stable for identical state', () => {
		expect(historyQueryKey(baseState())).toBe(historyQueryKey(baseState()));
	});

	it.each([
		['page', (s: ReturnType<typeof baseState>) => (s.currentPage = 2)],
		['page size', (s: ReturnType<typeof baseState>) => (s.itemsPerPage = 48)],
		['search text', (s: ReturnType<typeof baseState>) => (s.filters.search = 'fox')],
		['status', (s: ReturnType<typeof baseState>) => (s.filters.status = 'completed')],
		['media type', (s: ReturnType<typeof baseState>) => (s.filters.mediaType = 'video')],
		['tags', (s: ReturnType<typeof baseState>) => (s.filters.selectedTagIds = ['tag-1'])],
		['collection', (s: ReturnType<typeof baseState>) => (s.filters.collectionId = 'col-1')],
		['system tag', (s: ReturnType<typeof baseState>) => (s.filters.systemTag = '1girl')],
		['favourites', (s: ReturnType<typeof baseState>) => (s.filters.favoritesOnly = true)],
		['rating', (s: ReturnType<typeof baseState>) => (s.filters.minRating = 4)],
		['sort direction', (s: ReturnType<typeof baseState>) => (s.filters.sortDir = 'asc')]
	])('changes when %s changes', (_label, mutate) => {
		const changed = baseState();
		mutate(changed);
		expect(historyQueryKey(changed)).not.toBe(historyQueryKey(baseState()));
	});

	it('changes when the search mode changes with search text present', () => {
		const keyword = baseState();
		keyword.filters.search = 'fox';
		const semantic = baseState();
		semantic.filters.search = 'fox';
		semantic.filters.searchMode = 'semantic';
		expect(historyQueryKey(semantic)).not.toBe(historyQueryKey(keyword));
	});

	it('ignores the search mode while the search box is empty', () => {
		const semantic = baseState();
		semantic.filters.searchMode = 'semantic';
		expect(historyQueryKey(semantic)).toBe(historyQueryKey(baseState()));
	});
});
