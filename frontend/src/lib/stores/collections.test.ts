import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import type { Collection } from '$lib/types/history';

const mockListCollections = vi.fn();
const mockCreateCollection = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		listCollections: (...args: unknown[]) => mockListCollections(...args),
		createCollection: (...args: unknown[]) => mockCreateCollection(...args),
		renameCollection: vi.fn(),
		moveCollection: vi.fn(),
		bulkMoveCollections: vi.fn(),
		deleteCollection: vi.fn(),
		addToCollection: vi.fn()
	}
}));

import { historyCollectionsStore, libraryCollectionsStore } from './collections';

/** A resolvable/rejectable promise for driving out-of-order async responses. */
function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (error: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

function collection(overrides: Partial<Collection> = {}): Collection {
	return {
		id: 'c1',
		name: 'Root',
		scope: 'history',
		parent_id: null,
		created_at: '2026-01-01T00:00:00Z',
		item_count: 0,
		...overrides
	};
}

function listResponse(collections: Collection[]) {
	return { success: true, data: { collections, total: collections.length } };
}

describe('stores/collections request sequencing', () => {
	beforeEach(() => {
		vi.resetAllMocks();
		historyCollectionsStore.reset();
		libraryCollectionsStore.reset();
	});

	it('a reversed completion order keeps the later-issued response, not the later-arriving one', async () => {
		const first = deferred<unknown>();
		const second = deferred<unknown>();
		mockListCollections.mockImplementationOnce(() => first.promise);
		const firstLoad = historyCollectionsStore.load();
		mockListCollections.mockImplementationOnce(() => second.promise);
		const secondLoad = historyCollectionsStore.load();

		// The later-issued request's response arrives first.
		second.resolve(listResponse([collection({ id: 'c1', name: 'Renamed' })]));
		await secondLoad;
		expect(get(historyCollectionsStore).collections[0].name).toBe('Renamed');

		// The earlier-issued (stale) response arrives after - must not
		// regress the fresher, already-applied state.
		first.resolve(listResponse([collection({ id: 'c1', name: 'Old Name' })]));
		await firstLoad;

		expect(get(historyCollectionsStore).collections[0].name).toBe('Renamed');
	});

	it('an older failure settling after a newer request has applied does not clear loading it does not own', async () => {
		const first = deferred<unknown>();
		mockListCollections.mockImplementationOnce(() => first.promise);
		const firstLoad = historyCollectionsStore.load();

		const second = deferred<unknown>();
		mockListCollections.mockImplementationOnce(() => second.promise);
		const secondLoad = historyCollectionsStore.load();

		// The newer (second) request finishes first and owns loading afterwards.
		second.resolve(listResponse([collection()]));
		await secondLoad;
		expect(get(historyCollectionsStore).loading).toBe(false);

		// The stale first request now fails - must not flip loading back on,
		// nor overwrite the already-applied collections.
		first.reject(new Error('stale network error'));
		await firstLoad;

		expect(get(historyCollectionsStore).loading).toBe(false);
		expect(get(historyCollectionsStore).collections).toHaveLength(1);
	});

	it('a mutation refresh keeps its result, including the optimistic create row, over an earlier list load finishing later', async () => {
		const staleList = deferred<unknown>();
		mockListCollections.mockImplementationOnce(() => staleList.promise);
		const staleLoad = historyCollectionsStore.load();

		mockCreateCollection.mockResolvedValueOnce({
			success: true,
			data: { message: 'ok', collection: collection({ id: 'c2', name: 'New Collection' }) }
		});
		const mutationRefresh = deferred<unknown>();
		mockListCollections.mockImplementationOnce(() => mutationRefresh.promise);
		const createPromise = historyCollectionsStore.create('New Collection');

		// The optimistic row is visible immediately, before either list call settles.
		await Promise.resolve();
		await Promise.resolve();
		expect(get(historyCollectionsStore).collections.map((c) => c.id)).toContain('c2');

		// The mutation's own refresh (newer) resolves first, confirming the row.
		mutationRefresh.resolve(listResponse([collection({ id: 'c2', name: 'New Collection' })]));
		await createPromise;
		expect(get(historyCollectionsStore).collections.map((c) => c.id)).toEqual(['c2']);

		// The earlier, now-stale list load finally resolves without the new row.
		staleList.resolve(listResponse([]));
		await staleLoad;

		expect(get(historyCollectionsStore).collections.map((c) => c.id)).toEqual(['c2']);
	});

	it('reset() during a pending request stops that request from repopulating the store once it settles', async () => {
		const pending = deferred<unknown>();
		mockListCollections.mockImplementationOnce(() => pending.promise);
		const load = historyCollectionsStore.load();

		historyCollectionsStore.reset();
		expect(get(historyCollectionsStore).collections).toEqual([]);
		expect(get(historyCollectionsStore).loading).toBe(false);

		pending.resolve(listResponse([collection()]));
		await load;

		expect(get(historyCollectionsStore).collections).toEqual([]);
		expect(get(historyCollectionsStore).loading).toBe(false);
	});

	it('the history and library scoped stores race independently of each other', async () => {
		const historyPending = deferred<unknown>();
		const libraryPending = deferred<unknown>();
		mockListCollections.mockImplementation((scope: string) =>
			scope === 'history' ? historyPending.promise : libraryPending.promise
		);

		const historyLoad = historyCollectionsStore.load();
		const libraryLoad = libraryCollectionsStore.load();

		libraryPending.resolve(listResponse([collection({ id: 'lib1', scope: 'library' })]));
		await libraryLoad;
		historyPending.resolve(listResponse([collection({ id: 'hist1', scope: 'history' })]));
		await historyLoad;

		expect(get(historyCollectionsStore).collections.map((c) => c.id)).toEqual(['hist1']);
		expect(get(libraryCollectionsStore).collections.map((c) => c.id)).toEqual(['lib1']);
	});
});
