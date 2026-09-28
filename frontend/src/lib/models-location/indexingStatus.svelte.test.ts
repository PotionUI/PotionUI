import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { IndexingStatus } from '$lib/services/api/models';

vi.mock('$lib/services/api', () => ({
	api: { getIndexingStatus: vi.fn() }
}));

function ok(data: IndexingStatus) {
	return { success: true, data };
}

beforeEach(() => {
	vi.resetModules();
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
	vi.clearAllMocks();
});

describe('indexingStatusStore', () => {
	it('fetches once on first subscribe and does not poll while idle', async () => {
		const { api } = await import('$lib/services/api');
		vi.mocked(api.getIndexingStatus).mockResolvedValue(ok({ state: 'idle' }));
		const { indexingStatusStore } = await import('./indexingStatus.svelte');

		const unsubscribe = indexingStatusStore.subscribe();
		await indexingStatusStore.refresh();

		expect(api.getIndexingStatus).toHaveBeenCalledTimes(1);
		expect(indexingStatusStore.status?.state).toBe('idle');

		await vi.advanceTimersByTimeAsync(10_000);
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(1);

		unsubscribe();
	});

	it('polls every 2s while indexing and stops once the run finishes', async () => {
		const { api } = await import('$lib/services/api');
		vi.mocked(api.getIndexingStatus)
			.mockResolvedValueOnce(ok({ state: 'indexing', processed: 1, total: 10 }))
			.mockResolvedValueOnce(ok({ state: 'indexing', processed: 2, total: 10 }))
			.mockResolvedValueOnce(ok({ state: 'done', indexed: 10, found_on_disk: 10 }));
		const { indexingStatusStore } = await import('./indexingStatus.svelte');

		const unsubscribe = indexingStatusStore.subscribe();
		await indexingStatusStore.refresh();
		expect(indexingStatusStore.status?.state).toBe('indexing');

		await vi.advanceTimersByTimeAsync(2000);
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(2);
		expect(indexingStatusStore.status?.processed).toBe(2);

		await vi.advanceTimersByTimeAsync(2000);
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(3);
		expect(indexingStatusStore.status?.state).toBe('done');

		await vi.advanceTimersByTimeAsync(10_000);
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(3);

		unsubscribe();
	});

	it('keeps polling while restart_pending even if the reported state looks terminal', async () => {
		const { api } = await import('$lib/services/api');
		vi.mocked(api.getIndexingStatus).mockResolvedValue(
			ok({ state: 'done', indexed: 5, found_on_disk: 5, restart_pending: true })
		);
		const { indexingStatusStore } = await import('./indexingStatus.svelte');

		const unsubscribe = indexingStatusStore.subscribe();
		await indexingStatusStore.refresh();

		await vi.advanceTimersByTimeAsync(2000);
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(2);

		unsubscribe();
	});

	it('stops polling once the last subscriber unsubscribes', async () => {
		const { api } = await import('$lib/services/api');
		vi.mocked(api.getIndexingStatus).mockResolvedValue(ok({ state: 'indexing', processed: 0, total: 10 }));
		const { indexingStatusStore } = await import('./indexingStatus.svelte');

		const unsubscribe = indexingStatusStore.subscribe();
		await indexingStatusStore.refresh();
		unsubscribe();

		await vi.advanceTimersByTimeAsync(10_000);
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(1);
	});

	it('a second subscriber reuses the running poller instead of fetching again immediately', async () => {
		const { api } = await import('$lib/services/api');
		vi.mocked(api.getIndexingStatus).mockResolvedValue(ok({ state: 'indexing', processed: 0, total: 10 }));
		const { indexingStatusStore } = await import('./indexingStatus.svelte');

		const unsubscribeA = indexingStatusStore.subscribe();
		await indexingStatusStore.refresh();
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(1);

		const unsubscribeB = indexingStatusStore.subscribe();
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(1);

		unsubscribeA();
		await vi.advanceTimersByTimeAsync(2000);
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(2);

		unsubscribeB();
		await vi.advanceTimersByTimeAsync(2000);
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(2);
	});

	it('notifyRunStarted seeds the status immediately and starts polling without waiting for a fetch', async () => {
		const { api } = await import('$lib/services/api');
		vi.mocked(api.getIndexingStatus).mockResolvedValue(ok({ state: 'indexing', processed: 5, total: 10 }));
		const { indexingStatusStore } = await import('./indexingStatus.svelte');

		const unsubscribe = indexingStatusStore.subscribe();
		await indexingStatusStore.refresh();
		vi.mocked(api.getIndexingStatus).mockClear();

		indexingStatusStore.notifyRunStarted({ state: 'scanning' });
		expect(indexingStatusStore.status?.state).toBe('scanning');
		expect(api.getIndexingStatus).not.toHaveBeenCalled();

		await vi.advanceTimersByTimeAsync(2000);
		expect(api.getIndexingStatus).toHaveBeenCalledTimes(1);

		unsubscribe();
	});
});
