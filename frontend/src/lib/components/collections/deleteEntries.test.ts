import { describe, it, expect, vi } from 'vitest';
import {
	createEntriesActions,
	deleteEntriesCopy,
	deleteEntriesSummary,
	runDeleteEntries,
	type CollectionEntriesActions,
	type DeleteEntriesResult,
	type DeleteEntriesUi,
	type EntriesApi
} from './deleteEntries';

const COLLECTION = { id: 'col-1', name: 'Blue glass' };

function ui(confirmed = true) {
	return {
		confirm: vi.fn(async () => confirmed),
		success: vi.fn((_message: string) => {}),
		info: vi.fn((_message: string) => {}),
		error: vi.fn((_message: string) => {}),
		failed: vi.fn((_error: unknown) => {})
	} satisfies DeleteEntriesUi;
}

function actions(count: number, result: DeleteEntriesResult | Error, scope: 'history' | 'library' = 'history') {
	return {
		scope,
		count: vi.fn(async () => count),
		deleteAll: vi.fn(async () => {
			if (result instanceof Error) throw result;
			return result;
		})
	} satisfies CollectionEntriesActions;
}

describe('the delete entries dialog copy', () => {
	it('states the count, that items go everywhere, that the collection stays, and that it cannot be undone', () => {
		const copy = deleteEntriesCopy('Blue glass', 12, 'history');
		expect(copy.variant).toBe('danger');
		expect(copy.title).toBe('Delete entries?');
		expect(copy.message).toContain('12 generations');
		expect(copy.message).toContain('“Blue glass”');
		expect(copy.message).toContain('deleted everywhere, not only removed from this collection');
		expect(copy.message).toContain('The collection itself stays');
		expect(copy.message).toContain('cannot be undone');
		expect(copy.message).toContain('with their files');
		expect(copy.confirmLabel).toBe('Delete 12 generations');
	});

	it('names library entries as items and uses the singular for one', () => {
		const copy = deleteEntriesCopy('Refs', 1, 'library');
		expect(copy.message).toContain('Delete all 1 item in “Refs”, with its files?');
		expect(copy.message).toContain('It is deleted everywhere');
		expect(copy.confirmLabel).toBe('Delete 1 item');
	});
});

describe('the delete entries outcome', () => {
	it('reports every entry deleted', () => {
		expect(deleteEntriesSummary({ deleted: 3, blocked: [], total: 3 }, 'library')).toEqual({
			ok: true,
			message: 'Deleted 3 items.'
		});
	});

	it('reports what was kept and why', () => {
		const summary = deleteEntriesSummary(
			{ deleted: 2, blocked: [{ id: 'g3', reason: 'A brew still uses this image.' }], total: 3 },
			'history'
		);
		expect(summary.ok).toBe(false);
		expect(summary.message).toBe('Deleted 2 of 3. 1 could not be deleted: A brew still uses this image.');
	});
});

describe('running delete entries', () => {
	it('asks first and deletes only after a yes', async () => {
		const a = actions(4, { deleted: 4, blocked: [], total: 4 });
		const u = ui(true);
		expect(await runDeleteEntries(COLLECTION, a, u)).toBe('deleted');
		expect(u.confirm).toHaveBeenCalledWith(deleteEntriesCopy('Blue glass', 4, 'history'));
		expect(a.deleteAll).toHaveBeenCalledWith('col-1');
		expect(u.success).toHaveBeenCalledWith('Deleted 4 generations.');
	});

	it('deletes nothing when the dialog is cancelled', async () => {
		const a = actions(4, { deleted: 4, blocked: [], total: 4 });
		const u = ui(false);
		expect(await runDeleteEntries(COLLECTION, a, u)).toBe('cancelled');
		expect(a.deleteAll).not.toHaveBeenCalled();
		expect(u.success).not.toHaveBeenCalled();
	});

	it('opens no dialog for an empty collection', async () => {
		const a = actions(0, { deleted: 0, blocked: [], total: 0 });
		const u = ui(true);
		expect(await runDeleteEntries(COLLECTION, a, u)).toBe('empty');
		expect(u.confirm).not.toHaveBeenCalled();
		expect(a.deleteAll).not.toHaveBeenCalled();
		expect(u.info).toHaveBeenCalledWith('“Blue glass” has no entries to delete.');
	});

	it('opens no dialog when the count cannot be read', async () => {
		const failure = new Error('offline');
		const a = { ...actions(3, { deleted: 0, blocked: [], total: 0 }), count: vi.fn(async () => Promise.reject(failure)) };
		const u = ui(true);
		expect(await runDeleteEntries(COLLECTION, a, u)).toBe('failed');
		expect(u.confirm).not.toHaveBeenCalled();
		expect(a.deleteAll).not.toHaveBeenCalled();
		expect(u.failed).toHaveBeenCalledWith(failure);
	});

	it('reports blocked entries as an error', async () => {
		const a = actions(2, { deleted: 1, blocked: [{ id: 'x', reason: 'In use by a brew.' }], total: 2 });
		const u = ui(true);
		expect(await runDeleteEntries(COLLECTION, a, u)).toBe('partial');
		expect(u.error).toHaveBeenCalledWith('Deleted 1 of 2. 1 could not be deleted: In use by a brew.');
		expect(u.success).not.toHaveBeenCalled();
	});

	it('hands a failed request to the shared delete error', async () => {
		const failure = new Error('Collection not found');
		const a = actions(2, failure);
		const u = ui(true);
		expect(await runDeleteEntries(COLLECTION, a, u)).toBe('failed');
		expect(u.failed).toHaveBeenCalledWith(failure);
	});
});

describe('the delete entries actions for a scope', () => {
	function entriesApi(overrides: Partial<EntriesApi> = {}) {
		return {
			list: vi.fn(async () => ({
				success: true,
				data: {
					collections: [
						{ id: 'col-1', item_count: 5 },
						{ id: 'col-2', item_count: null }
					]
				}
			})),
			deleteEntries: vi.fn(async () => ({ success: true, data: { deleted: 5, blocked: [], total: 5 } })),
			...overrides
		} as EntriesApi & { list: ReturnType<typeof vi.fn>; deleteEntries: ReturnType<typeof vi.fn> };
	}

	it('counts only the collection’s own entries', async () => {
		const api = entriesApi();
		const a = createEntriesActions('library', api, vi.fn(async () => undefined));
		expect(await a.count('col-1')).toBe(5);
		expect(await a.count('col-2')).toBe(0);
		expect(api.list).toHaveBeenCalledWith('library', true);
	});

	it('fails the count for a collection that is gone or a failed listing', async () => {
		await expect(createEntriesActions('history', entriesApi(), vi.fn()).count('missing')).rejects.toThrow(
			'This collection no longer exists.'
		);
		const refused = entriesApi({ list: vi.fn(async () => ({ success: false, message: 'Nope' })) });
		await expect(createEntriesActions('history', refused, vi.fn()).count('col-1')).rejects.toThrow('Nope');
	});

	it('returns the server result and reloads the page and collections', async () => {
		const api = entriesApi();
		const reload = vi.fn(async () => undefined);
		const result = await createEntriesActions('history', api, reload).deleteAll('col-1');
		expect(result).toEqual({ deleted: 5, blocked: [], total: 5 });
		expect(api.deleteEntries).toHaveBeenCalledWith('col-1');
		expect(reload).toHaveBeenCalledTimes(1);
	});

	it('still reloads when the request fails, since some entries may already be gone', async () => {
		const failure = new Error('boom');
		const reload = vi.fn(async () => undefined);
		const api = entriesApi({ deleteEntries: vi.fn(async () => Promise.reject(failure)) });
		await expect(createEntriesActions('history', api, reload).deleteAll('col-1')).rejects.toBe(failure);
		expect(reload).toHaveBeenCalledTimes(1);
		const refused = entriesApi({ deleteEntries: vi.fn(async () => ({ success: false, message: 'Not allowed' })) });
		await expect(createEntriesActions('history', refused, reload).deleteAll('col-1')).rejects.toThrow('Not allowed');
		expect(reload).toHaveBeenCalledTimes(2);
	});
});
