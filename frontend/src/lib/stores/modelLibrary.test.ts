import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listModelCollections: vi.fn()
	}
}));

vi.mock('$lib/utils/logger', () => ({
	logger: { error: vi.fn() },
	getErrorMessage: (error: unknown) => String(error)
}));

import { api } from '$lib/services/api/index';
import { modelLibraryStore } from './modelLibrary';

const listModelCollections = vi.mocked(api.listModelCollections);

describe('modelLibraryStore', () => {
	beforeEach(() => {
		modelLibraryStore.reset();
		listModelCollections.mockReset();
	});

	it('deduplicates concurrent collection loads and caches the result', async () => {
		let resolveRequest!: (value: any) => void;
		listModelCollections.mockReturnValue(
			new Promise((resolve) => {
				resolveRequest = resolve;
			})
		);

		const first = modelLibraryStore.load();
		const second = modelLibraryStore.load();

		expect(listModelCollections).toHaveBeenCalledTimes(1);
		resolveRequest({ success: true, data: { collections: [] } });
		await Promise.all([first, second]);

		await modelLibraryStore.load();
		expect(listModelCollections).toHaveBeenCalledTimes(1);
	});

	it('supports an explicit refresh after the cache is populated', async () => {
		listModelCollections.mockResolvedValue({ success: true, data: { collections: [] } } as any);

		await modelLibraryStore.load();
		await modelLibraryStore.load(true);

		expect(listModelCollections).toHaveBeenCalledTimes(2);
	});

	it('queues a forced refresh behind an in-flight initial load', async () => {
		let resolveInitial!: (value: any) => void;
		listModelCollections
			.mockImplementationOnce(
				() => new Promise((resolve) => {
					resolveInitial = resolve;
				})
			)
			.mockResolvedValue({ success: true, data: { collections: [] } } as any);

		const initial = modelLibraryStore.load();
		const forced = modelLibraryStore.load(true);
		resolveInitial({ success: true, data: { collections: [] } });
		await Promise.all([initial, forced]);

		expect(listModelCollections).toHaveBeenCalledTimes(2);
	});
});
