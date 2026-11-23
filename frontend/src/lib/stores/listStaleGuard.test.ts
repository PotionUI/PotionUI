import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockListLibraryItems = vi.fn();
const mockListInspirations = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		listLibraryItems: (...args: unknown[]) => mockListLibraryItems(...args),
		listInspirations: (...args: unknown[]) => mockListInspirations(...args),
		getLibraryFacets: vi.fn().mockResolvedValue({ success: true, data: { media_types: {} } }),
		getTags: vi.fn()
	}
}));

import { libraryStore } from './library';
import { inspirationsStore } from './inspirations';

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

describe('list stores ignore out-of-order responses', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		libraryStore.reset();
	});

	it('library keeps the newest query result when an older one lands last', async () => {
		const slow = deferred<unknown>();
		const fast = deferred<unknown>();
		mockListLibraryItems.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);

		const first = libraryStore.load();
		const second = libraryStore.load();
		fast.resolve({ success: true, data: { items: [{ id: 'new' }], total: 1 } });
		await second;
		slow.resolve({ success: true, data: { items: [{ id: 'old' }], total: 1 } });
		await first;

		expect(get(libraryStore).items.map((item) => item.id)).toEqual(['new']);
		expect(get(libraryStore).loading).toBe(false);
	});

	it('library drops a stale failure instead of clearing loading early', async () => {
		const slow = deferred<unknown>();
		const fast = deferred<unknown>();
		mockListLibraryItems.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);

		const first = libraryStore.load();
		const second = libraryStore.load();
		slow.resolve(Promise.reject(new Error('boom')));
		await first;
		expect(get(libraryStore).loading).toBe(true);
		fast.resolve({ success: true, data: { items: [{ id: 'new' }], total: 1 } });
		await second;
		expect(get(libraryStore).loading).toBe(false);
	});

	it('inspirations keeps the newest query result when an older one lands last', async () => {
		const slow = deferred<unknown>();
		const fast = deferred<unknown>();
		mockListInspirations.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);

		const first = inspirationsStore.load();
		const second = inspirationsStore.load();
		fast.resolve({ success: true, data: { items: [{ id: 'new' }], total: 1 } });
		await second;
		slow.resolve({ success: true, data: { items: [{ id: 'old' }], total: 1 } });
		await first;

		expect(get(inspirationsStore).items.map((item) => item.id)).toEqual(['new']);
	});
});
