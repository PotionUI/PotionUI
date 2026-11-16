import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import type { EditRecord, EditSummary } from '$lib/services/api/edits';
import type { GenerationHistoryItem } from '$lib/types/history';
import { readEditLink } from './editLink';
import {
	createEditPictures,
	createRecentEdits,
	editLabel,
	EDITS_SEARCH_DEBOUNCE_MS,
	fileStem,
	historyPicks,
	openEditTabs,
	pageCount,
	relativeTime,
	resolveEditSourceRef,
	type RestorePatch,
	type TabLike
} from './recentEdits';
import { emptyImageState, writeImageState } from './state';

function summary(n: number, extra: Partial<EditSummary> = {}): EditSummary {
	return {
		id: `e${n}`,
		tool: 'image',
		name: null,
		source: { kind: 'library', id: `item-${n}`, name: `picture-${n}.png` },
		thumbnail_upload_id: null,
		revision: 1,
		created_at: '2026-11-16T10:00:00Z',
		updated_at: '2026-11-16T10:00:00Z',
		...extra
	};
}

function rows(total: number): EditSummary[] {
	return Array.from({ length: total }, (_, index) => summary(index + 1));
}

function failure(status: number) {
	return { response: { status, data: { detail: {} } } };
}

function fixture(total = 27, extra: { tabs?: TabLike[] } = {}) {
	let all = rows(total);
	const restored: RestorePatch[] = [];
	const focused: string[] = [];
	const edits = {
		listEdits: vi.fn(async (params: { limit?: number; offset?: number; search?: string }) => {
			const needle = (params.search ?? '').toLowerCase();
			const matching = needle ? all.filter((row) => `${row.name ?? ''} ${row.source.name ?? ''}`.toLowerCase().includes(needle)) : all;
			const offset = params.offset ?? 0;
			return { success: true, data: { edits: matching.slice(offset, offset + (params.limit ?? 12)), total: matching.length, limit: params.limit ?? 12, offset } };
		}),
		getEdit: vi.fn(async (id: string) => {
			const found = all.find((row) => row.id === id);
			if (!found) throw failure(404);
			const state = writeImageState({ ...emptyImageState('effects') });
			return { success: true, data: { edit: { ...found, revision: 4, state } as EditRecord } };
		}),
		renameEdit: vi.fn(async (id: string, name: string | null) => {
			const found = all.find((row) => row.id === id);
			if (!found) throw failure(404);
			found.name = name;
			return { success: true, data: { edit: { ...found } } };
		}),
		duplicateEdit: vi.fn(async (id: string) => {
			const found = all.find((row) => row.id === id);
			if (!found) throw failure(404);
			const copy = summary(900, { name: `${found.name ?? 'picture'} copy` });
			all = [copy, ...all];
			return { success: true, data: { edit: { ...copy, state: {} } as EditRecord } };
		}),
		deleteEdit: vi.fn(async (id: string) => {
			if (!all.some((row) => row.id === id)) throw failure(404);
			all = all.filter((row) => row.id !== id);
		})
	};
	const library = {
		getLibraryItem: vi.fn(async (id: string) => ({
			success: true,
			data: { id, filename: `${id}.png`, original_filename: `${id}-original.png`, media_type: 'image', url: `/api/media/uploads/${id}.png`, width: 10, height: 10 }
		}))
	};
	const tabs: TabLike[] = extra.tabs ?? [{ id: 'tab-self', kind: 'tool', tool: 'image', state: null }];
	const manager = createRecentEdits({
		edits: edits as never,
		library: library as never,
		tabId: 'tab-self',
		openTabs: () => tabs,
		focusTab: (id) => focused.push(id),
		restoreTab: (patch) => restored.push(patch)
	});
	return { manager, edits, library, restored, focused, tabs, state: () => get(manager), remaining: () => all };
}

describe('recent edits data', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('loads the first page of twelve with the totals', async () => {
		const { manager, edits, state } = fixture(27);
		expect(state().status).toBe('loading');
		await manager.load();
		expect(edits.listEdits).toHaveBeenCalledWith({ tool: 'image', limit: 12, offset: 0 });
		expect(state().status).toBe('ready');
		expect(state().edits).toHaveLength(12);
		expect(state().total).toBe(27);
		expect(state().overall).toBe(27);
		expect(pageCount(27)).toBe(3);
	});

	it('moves between pages by offset and ignores a page outside the range', async () => {
		const { manager, edits, state } = fixture(27);
		await manager.load();
		manager.setPage(3);
		await vi.advanceTimersByTimeAsync(0);
		expect(edits.listEdits).toHaveBeenLastCalledWith({ tool: 'image', limit: 12, offset: 24 });
		expect(state().edits).toHaveLength(3);
		const calls = edits.listEdits.mock.calls.length;
		manager.setPage(9);
		manager.setPage(3);
		expect(edits.listEdits.mock.calls.length).toBe(calls);
		expect(state().page).toBe(3);
	});

	it('debounces the search, goes back to page one and keeps the overall count', async () => {
		const { manager, edits, state } = fixture(27);
		await manager.load();
		manager.setPage(2);
		await vi.advanceTimersByTimeAsync(0);
		const calls = edits.listEdits.mock.calls.length;
		manager.setSearch('p');
		manager.setSearch('picture-2');
		expect(state().search).toBe('picture-2');
		await vi.advanceTimersByTimeAsync(EDITS_SEARCH_DEBOUNCE_MS - 1);
		expect(edits.listEdits.mock.calls.length).toBe(calls);
		await vi.advanceTimersByTimeAsync(1);
		expect(edits.listEdits.mock.calls.length).toBe(calls + 1);
		expect(edits.listEdits).toHaveBeenLastCalledWith({ tool: 'image', limit: 12, offset: 0, search: 'picture-2' });
		expect(state().page).toBe(1);
		expect(state().total).toBe(state().edits.length);
		expect(state().overall).toBe(27);
	});

	it('reports a search with no match and clears it at once', async () => {
		const { manager, edits, state } = fixture(27);
		await manager.load();
		manager.setSearch('lantern');
		await vi.advanceTimersByTimeAsync(EDITS_SEARCH_DEBOUNCE_MS);
		expect(state().edits).toEqual([]);
		expect(state().total).toBe(0);
		expect(state().overall).toBe(27);
		manager.clearSearch();
		await vi.advanceTimersByTimeAsync(0);
		expect(state().search).toBe('');
		expect(state().edits).toHaveLength(12);
		expect(edits.listEdits).toHaveBeenLastCalledWith({ tool: 'image', limit: 12, offset: 0 });
	});

	it('keeps the list while a new page loads and drops answers that arrive late', async () => {
		const { manager, edits, state } = fixture(27);
		await manager.load();
		let release: () => void = () => undefined;
		edits.listEdits.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					release = () => resolve({ success: true, data: { edits: [summary(99)], total: 1, limit: 12, offset: 0 } });
				}) as never
		);
		manager.setPage(2);
		expect(state().status).toBe('ready');
		expect(state().refreshing).toBe(true);
		expect(state().edits).toHaveLength(12);
		manager.setPage(3);
		await vi.advanceTimersByTimeAsync(0);
		release();
		await vi.advanceTimersByTimeAsync(0);
		expect(state().edits.map((row) => row.id)).not.toContain('e99');
		expect(state().refreshing).toBe(false);
	});

	it('shows the error state and recovers on retry', async () => {
		const { manager, edits, state } = fixture(3);
		edits.listEdits.mockRejectedValueOnce(failure(500));
		await manager.load();
		expect(state().status).toBe('error');
		await manager.retry();
		expect(state().status).toBe('ready');
		expect(state().edits).toHaveLength(3);
	});

	it('is first use when there is nothing yet', async () => {
		const { manager, state } = fixture(0);
		await manager.load();
		expect(state().status).toBe('ready');
		expect(state().overall).toBe(0);
		expect(state().edits).toEqual([]);
	});

	it('renames in place, sends null for an empty name and skips an unchanged one', async () => {
		const { manager, edits, state } = fixture(3);
		await manager.load();
		expect(await manager.rename('e1', '  Cover crop  ')).toMatchObject({ kind: 'done' });
		expect(edits.renameEdit).toHaveBeenCalledWith('e1', 'Cover crop');
		expect(state().edits.find((row) => row.id === 'e1')?.name).toBe('Cover crop');
		expect(await manager.rename('e1', 'Cover crop')).toEqual({ kind: 'unchanged' });
		expect(await manager.rename('e1', '   ')).toMatchObject({ kind: 'done' });
		expect(edits.renameEdit).toHaveBeenLastCalledWith('e1', null);
		expect(state().edits.find((row) => row.id === 'e1')?.name).toBeNull();
	});

	it('reports a rename that fails and one on an edit that is gone', async () => {
		const { manager, edits, state } = fixture(3);
		await manager.load();
		edits.renameEdit.mockRejectedValueOnce(failure(500));
		expect(await manager.rename('e2', 'New')).toEqual({ kind: 'failed' });
		edits.renameEdit.mockRejectedValueOnce(failure(404));
		expect(await manager.rename('e2', 'New')).toEqual({ kind: 'missing' });
		expect(state().status).toBe('ready');
	});

	it('puts a duplicate at the top of the first page', async () => {
		const { manager, state } = fixture(27);
		await manager.load();
		manager.setPage(2);
		await vi.advanceTimersByTimeAsync(0);
		const result = await manager.duplicate('e14');
		expect(result.kind).toBe('done');
		expect(state().page).toBe(1);
		expect(state().edits[0].id).toBe('e900');
		expect(state().total).toBe(28);
	});

	it('deletes, refreshes the totals and steps back when the last page empties', async () => {
		const { manager, state, remaining } = fixture(13);
		await manager.load();
		manager.setPage(2);
		await vi.advanceTimersByTimeAsync(0);
		expect(state().edits).toHaveLength(1);
		expect(await manager.remove('e13')).toEqual({ kind: 'done' });
		expect(remaining()).toHaveLength(12);
		expect(state().page).toBe(1);
		expect(state().edits).toHaveLength(12);
		expect(state().total).toBe(12);
	});

	it('treats deleting an edit that is already gone as removed', async () => {
		const { manager, state } = fixture(3);
		await manager.load();
		expect(await manager.remove('nope')).toEqual({ kind: 'missing' });
		expect(state().edits).toHaveLength(3);
	});

	it('reports a delete that fails', async () => {
		const { manager, edits } = fixture(3);
		await manager.load();
		edits.deleteEdit.mockRejectedValueOnce(failure(500));
		expect(await manager.remove('e1')).toEqual({ kind: 'failed' });
	});
});

describe('continue an edit', () => {
	it('puts the picture, the saved steps and the link into this tab', async () => {
		const { manager, restored, library } = fixture(3);
		await manager.load();
		const result = await manager.continueEdit('e2');
		expect(result).toEqual({ kind: 'opened' });
		expect(restored).toHaveLength(1);
		const [patch] = restored;
		expect(patch.source).toMatchObject({ item_id: 'item-2', name: 'item-2-original.png', url: '/api/media/uploads/item-2.png' });
		expect(patch.activeTool).toBe('effects');
		expect(readEditLink(patch.state)).toEqual({ id: 'e2', revision: 4 });
		expect(patch.state.v).toBe(writeImageState(emptyImageState()).v);
		expect(library.getLibraryItem).toHaveBeenCalledWith('item-2');
	});

	it('uses the file for a History picture and an upload id for an uploaded one', async () => {
		const history = await resolveEditSourceRef({ kind: 'history', id: '501', name: 'dusk.png' }, { getLibraryItem: vi.fn() } as never);
		expect(history).toMatchObject({ generation_file_id: '501', url: '/api/media/files/501', name: 'dusk.png' });
		const library = { getLibraryItem: vi.fn(async (id: string) => ({ success: true, data: { id, filename: 'a.png', original_filename: 'a.png', media_type: 'image', url: '/api/media/uploads/a.png' } })) };
		const upload = await resolveEditSourceRef({ kind: 'upload', id: 'art-1', name: null }, library as never);
		expect(upload?.artifact_id).toBe('art-1');
		expect(upload?.item_id).toBeUndefined();
	});

	it('focuses the tab that already has the edit instead of opening a second copy', async () => {
		const { manager, restored, focused, edits } = fixture(3, {
			tabs: [
				{ id: 'tab-self', kind: 'tool', tool: 'image', state: null },
				{ id: 'tab-9', kind: 'tool', tool: 'image', state: { edit: { id: 'e1', revision: 2 } } }
			]
		});
		await manager.load();
		expect(manager.openElsewhere('e1')).toBe('tab-9');
		expect(manager.openElsewhere('e2')).toBeNull();
		expect(await manager.continueEdit('e1')).toEqual({ kind: 'focused', tabId: 'tab-9' });
		expect(focused).toEqual(['tab-9']);
		expect(restored).toEqual([]);
		expect(edits.getEdit).not.toHaveBeenCalled();
	});

	it('refreshes the list and reports a deleted edit', async () => {
		const { manager, edits, restored, state } = fixture(3);
		await manager.load();
		edits.listEdits.mockClear();
		expect(await manager.continueEdit('gone')).toEqual({ kind: 'missing' });
		expect(restored).toEqual([]);
		expect(edits.listEdits).toHaveBeenCalledTimes(1);
		expect(state().status).toBe('ready');
	});

	it('does not open an edit whose picture is gone', async () => {
		const { manager, library, restored } = fixture(3);
		await manager.load();
		library.getLibraryItem.mockRejectedValueOnce(failure(404));
		expect(await manager.continueEdit('e1')).toEqual({ kind: 'source-missing' });
		expect(restored).toEqual([]);
	});

	it('reports a failure to load the edit and ignores a second click while one is opening', async () => {
		const { manager, edits, restored } = fixture(3);
		await manager.load();
		edits.getEdit.mockRejectedValueOnce(failure(500));
		expect(await manager.continueEdit('e1')).toEqual({ kind: 'failed' });
		const first = manager.continueEdit('e2');
		expect(await manager.continueEdit('e2')).toEqual({ kind: 'busy' });
		expect(await first).toEqual({ kind: 'opened' });
		expect(restored).toHaveLength(1);
	});
});

describe('open elsewhere detection', () => {
	it('maps edit ids to image tabs and ignores this tab, other tools and unlinked tabs', () => {
		const tabs: TabLike[] = [
			{ id: 'a', kind: 'tool', tool: 'image', state: { edit: { id: 'e1', revision: 1 } } },
			{ id: 'b', kind: 'tool', tool: 'image', state: { edit: { id: 'e2', revision: 3 } } },
			{ id: 'c', kind: 'tool', tool: 'video', state: { edit: { id: 'e3', revision: 1 } } },
			{ id: 'd', kind: 'generation', state: { edit: { id: 'e4', revision: 1 } } },
			{ id: 'e', kind: 'tool', tool: 'image', state: { edit: { id: 'e5', revision: 0 } } },
			{ id: 'f', kind: 'tool', tool: 'image', state: null }
		];
		expect(Object.fromEntries(openEditTabs(tabs))).toEqual({ e1: 'a', e2: 'b' });
		expect(Object.fromEntries(openEditTabs(tabs, 'a'))).toEqual({ e2: 'b' });
	});
});

describe('names and times', () => {
	it('shows the edit name, else the picture file name without its extension, else a plain fallback', () => {
		expect(editLabel(summary(1, { name: ' Poster, final ' }))).toEqual({ text: 'Poster, final', unnamed: false });
		expect(editLabel(summary(1, { name: '   ', source: { kind: 'history', id: '1', name: 'glass-study.png' } }))).toEqual({ text: 'glass-study', unnamed: true });
		expect(editLabel(summary(1, { source: { kind: 'history', id: '1', name: null } }))).toEqual({ text: 'Untitled edit', unnamed: true });
		expect(editLabel(summary(1, { source: { kind: 'history', id: '1' } }))).toEqual({ text: 'Untitled edit', unnamed: true });
		expect(fileStem('a.b.png')).toBe('a.b');
		expect(fileStem('.hidden')).toBe('.hidden');
	});

	it('writes time in plain words', () => {
		const now = Date.parse('2026-11-16T12:00:00Z');
		const ago = (minutes: number) => new Date(now - minutes * 60000).toISOString();
		expect(relativeTime(ago(0), now)).toBe('Just now');
		expect(relativeTime(ago(4), now)).toBe('4 min ago');
		expect(relativeTime(ago(95), now)).toBe('2 h ago');
		expect(relativeTime(ago(1500), now)).toBe('Yesterday');
		expect(relativeTime(ago(4400), now)).toBe('3 days ago');
		expect(relativeTime(ago(60 * 24 * 12), now)).toMatch(/^\d{1,2} [A-Z][a-z]{2}$/);
		expect(relativeTime(null, now)).toBe('');
		expect(relativeTime('nonsense', now)).toBe('');
	});
});

describe('tile pictures', () => {
	it('prefers the saved thumbnail, uses the file for History and asks once per picture', async () => {
		const library = { getLibraryItem: vi.fn(async (id: string) => ({ success: true, data: { url: `/u/${id}.png` } })) };
		const pictures = createEditPictures(library as never);
		expect(await pictures.resolve(summary(1, { source: { kind: 'history', id: '77' } }))).toBe('/api/media/files/77?size=medium');
		expect(await pictures.resolve(summary(1, { thumbnail_upload_id: 'thumb-1' }))).toBe('/u/thumb-1.png');
		await pictures.resolve(summary(2, { thumbnail_upload_id: 'thumb-1' }));
		expect(await pictures.resolve(summary(3))).toBe('/u/item-3.png');
		expect(library.getLibraryItem).toHaveBeenCalledTimes(2);
	});

	it('gives null for a picture that no longer exists', async () => {
		const library = { getLibraryItem: vi.fn().mockRejectedValue(failure(404)) };
		expect(await createEditPictures(library as never).resolve(summary(1))).toBeNull();
	});

	it('lists the latest final History pictures with their time', () => {
		const generations = [
			{
				created_at: '2026-11-16T09:00:00Z',
				files: [
					{ id: 1, file_path: 'outputs/d/gen-1/a.png', file_type: 'image', is_final: true, created_at: '2026-11-16T09:01:00Z' },
					{ id: 2, file_path: 'outputs/d/gen-1/b.png', file_type: 'image', is_final: false, created_at: '2026-11-16T09:01:00Z' },
					{ id: 3, file_path: 'outputs/d/gen-1/c.mp4', file_type: 'video', is_final: true, created_at: '2026-11-16T09:01:00Z' }
				]
			}
		] as unknown as GenerationHistoryItem[];
		const picks = historyPicks(generations, 8);
		expect(picks).toHaveLength(1);
		expect(picks[0].ref.generation_file_id).toBe('1');
		expect(picks[0].at).toBe('2026-11-16T09:01:00Z');
	});
});
