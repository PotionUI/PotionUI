import { get, writable } from 'svelte/store';
import type { APIResponse } from '$lib/types/api';
import {
	describeEditFailure,
	type EditList,
	type EditRecord,
	type EditSource,
	type EditSourceKind,
	type EditSummary,
	type ListEditsParams
} from '$lib/services/api/edits';
import type { LibraryItem } from '$lib/services/api/library';
import type { GenerationHistoryItem } from '$lib/types/history';
import type { MediaRef } from '$lib/types/tabs';
import { readEditLink, withEditLink } from './editLink';
import { isImageFile, historyFileRef, libraryItemRef } from './sourceRefs';

export const EDITS_PAGE_SIZE = 12;
export const EDITS_SEARCH_DEBOUNCE_MS = 300;
export const UNTITLED_EDIT = 'Untitled edit';

export interface EditsPort {
	listEdits(params: ListEditsParams): Promise<APIResponse<EditList>>;
	getEdit(id: string): Promise<APIResponse<{ edit: EditRecord }>>;
	renameEdit(id: string, name: string | null): Promise<APIResponse<{ edit: EditSummary }>>;
	duplicateEdit(id: string): Promise<APIResponse<{ edit: EditRecord }>>;
	deleteEdit(id: string): Promise<void>;
}

export interface LibraryPort {
	getLibraryItem(id: string): Promise<APIResponse<LibraryItem>>;
}

export interface HistoryPort {
	getGenerationHistory(params: { limit: number; mediaType: 'image'; status: 'completed' }): Promise<APIResponse<{ generations: GenerationHistoryItem[] }>>;
}

export interface TabLike {
	id: string;
	kind: string;
	tool?: string;
	state?: unknown;
}

export interface RestorePatch {
	source: MediaRef;
	state: Record<string, unknown>;
	activeTool: string | null;
}

export interface RecentEditsDeps {
	edits: EditsPort;
	library: LibraryPort;
	tabId: string;
	openTabs: () => TabLike[];
	focusTab: (tabId: string) => void;
	restoreTab: (patch: RestorePatch) => void;
	pageSize?: number;
	debounceMs?: number;
}

export type RecentEditsStatus = 'loading' | 'ready' | 'error';

export interface RecentEditsState {
	status: RecentEditsStatus;
	refreshing: boolean;
	edits: EditSummary[];
	total: number;
	overall: number;
	page: number;
	search: string;
	query: string;
}

export type ContinueResult =
	| { kind: 'opened' }
	| { kind: 'focused'; tabId: string }
	| { kind: 'missing' }
	| { kind: 'source-missing' }
	| { kind: 'busy' }
	| { kind: 'failed' };

export type EditActionResult = { kind: 'done'; edit?: EditSummary } | { kind: 'unchanged' } | { kind: 'missing' } | { kind: 'failed' };

export interface SourceLook {
	label: string;
	icon: string;
}

const SOURCE_LOOKS: Record<EditSourceKind, SourceLook> = {
	history: { label: 'History', icon: 'clock' },
	library: { label: 'Library', icon: 'photo' },
	upload: { label: 'Uploaded file', icon: 'upload' }
};

export function sourceLook(kind: EditSourceKind): SourceLook {
	return SOURCE_LOOKS[kind] ?? SOURCE_LOOKS.upload;
}

export function fileStem(name: string): string {
	const trimmed = name.trim();
	const dot = trimmed.lastIndexOf('.');
	return dot > 0 ? trimmed.slice(0, dot) : trimmed;
}

export function editLabel(edit: Pick<EditSummary, 'name' | 'source'>): { text: string; unnamed: boolean } {
	const name = (edit.name ?? '').trim();
	if (name) return { text: name, unnamed: false };
	const picture = fileStem(edit.source.name ?? '');
	return { text: picture || UNTITLED_EDIT, unnamed: true };
}

export function pageCount(total: number, size = EDITS_PAGE_SIZE): number {
	return Math.max(1, Math.ceil(Math.max(0, total) / size));
}

export function openEditTabs(tabs: readonly TabLike[], exceptTabId?: string): Map<string, string> {
	const open = new Map<string, string>();
	for (const tab of tabs) {
		if (tab.kind !== 'tool' || tab.tool !== 'image' || tab.id === exceptTabId) continue;
		const link = readEditLink(tab.state);
		if (link && !open.has(link.id)) open.set(link.id, tab.id);
	}
	return open;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
	if (!iso) return '';
	const at = Date.parse(iso);
	if (Number.isNaN(at)) return '';
	const minutes = Math.max(0, Math.floor((now - at) / 60000));
	if (minutes < 1) return 'Just now';
	if (minutes < 60) return `${minutes} min ago`;
	if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
	if (minutes < 2880) return 'Yesterday';
	if (minutes < 10080) return `${Math.floor(minutes / 1440)} days ago`;
	const date = new Date(at);
	return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

export async function resolveEditSourceRef(source: EditSource, library: LibraryPort): Promise<MediaRef | null> {
	const name = (source.name ?? '').trim();
	if (source.kind === 'history') {
		const url = `/api/media/files/${encodeURIComponent(source.id)}`;
		return { path: url, relative_path: url, url, name: name || 'image', type: 'image', generation_file_id: source.id };
	}
	try {
		const item = (await library.getLibraryItem(source.id)).data;
		if (!item) return null;
		const ref = libraryItemRef(item);
		if (source.kind === 'library') return ref;
		const { item_id: _dropped, ...rest } = ref;
		return { ...rest, artifact_id: item.id };
	} catch {
		return null;
	}
}

export interface ContinueDeps {
	getEdit: (id: string) => Promise<APIResponse<{ edit: EditRecord }>>;
	library: LibraryPort;
	findOpen: (id: string) => string | null;
}

export type PreparedContinue =
	| { kind: 'focused'; tabId: string }
	| { kind: 'ready'; record: EditRecord; source: MediaRef; state: Record<string, unknown>; activeTool: string | null }
	| { kind: 'missing' }
	| { kind: 'source-missing' }
	| { kind: 'failed' };

export function findEditTab(tabs: readonly TabLike[], id: string): string | null {
	for (const tab of tabs) {
		if (tab.kind !== 'tool') continue;
		if (readEditLink(tab.state)?.id === id) return tab.id;
	}
	return null;
}

function activeToolOf(state: unknown): string | null {
	const value = state && typeof state === 'object' ? (state as Record<string, unknown>).activeTool : null;
	return typeof value === 'string' && value ? value : null;
}

export async function prepareContinue(id: string, deps: ContinueDeps): Promise<PreparedContinue> {
	const open = deps.findOpen(id);
	if (open) return { kind: 'focused', tabId: open };
	let record: EditRecord | undefined;
	try {
		record = (await deps.getEdit(id)).data?.edit;
	} catch (error) {
		return describeEditFailure(error).kind === 'not_found' ? { kind: 'missing' } : { kind: 'failed' };
	}
	if (!record) return { kind: 'failed' };
	const raced = deps.findOpen(record.id);
	if (raced) return { kind: 'focused', tabId: raced };
	const source = await resolveEditSourceRef(record.source, deps.library);
	if (!source) return { kind: 'source-missing' };
	const state = withEditLink(record.state, { id: record.id, revision: record.revision });
	return { kind: 'ready', record, source, state, activeTool: activeToolOf(state) };
}

export function pictureKey(edit: Pick<EditSummary, 'source' | 'thumbnail_upload_id'>): string {
	return edit.thumbnail_upload_id ? `upload:${edit.thumbnail_upload_id}` : `${edit.source.kind}:${edit.source.id}`;
}

export function createEditPictures(library: LibraryPort) {
	const cache = new Map<string, Promise<string | null>>();

	function urlFor(kind: EditSourceKind, id: string): Promise<string | null> {
		if (kind === 'history') return Promise.resolve(`/api/media/files/${encodeURIComponent(id)}?size=medium`);
		return library
			.getLibraryItem(id)
			.then((response) => response.data?.url ?? null)
			.catch(() => null);
	}

	function resolve(edit: Pick<EditSummary, 'source' | 'thumbnail_upload_id'>): Promise<string | null> {
		const kind: EditSourceKind = edit.thumbnail_upload_id ? 'upload' : edit.source.kind;
		const id = edit.thumbnail_upload_id ?? edit.source.id;
		const key = pictureKey(edit);
		let found = cache.get(key);
		if (!found) {
			found = urlFor(kind, id);
			cache.set(key, found);
		}
		return found;
	}

	return { resolve };
}

export interface HistoryPick {
	ref: MediaRef;
	at: string | null;
}

export function historyPicks(generations: readonly GenerationHistoryItem[], limit = 8): HistoryPick[] {
	const picks: HistoryPick[] = [];
	for (const generation of generations) {
		for (const file of generation.files ?? []) {
			if (!file.is_final || !isImageFile(file)) continue;
			picks.push({ ref: historyFileRef(file), at: file.created_at ?? generation.created_at ?? null });
			if (picks.length >= limit) return picks;
		}
	}
	return picks;
}

export async function loadHistoryPicks(port: HistoryPort, limit = 8): Promise<HistoryPick[]> {
	try {
		const response = await port.getGenerationHistory({ limit: 12, mediaType: 'image', status: 'completed' });
		return response.success && response.data ? historyPicks(response.data.generations, limit) : [];
	} catch {
		return [];
	}
}

export function createRecentEdits(deps: RecentEditsDeps) {
	const size = deps.pageSize ?? EDITS_PAGE_SIZE;
	const debounceMs = deps.debounceMs ?? EDITS_SEARCH_DEBOUNCE_MS;
	const store = writable<RecentEditsState>({
		status: 'loading',
		refreshing: false,
		edits: [],
		total: 0,
		overall: 0,
		page: 1,
		search: '',
		query: ''
	});

	let sequence = 0;
	let timer: ReturnType<typeof setTimeout> | null = null;
	let continuing = false;
	let destroyed = false;

	function patch(next: Partial<RecentEditsState>): void {
		store.update((state) => ({ ...state, ...next }));
	}

	async function load(initial = false): Promise<void> {
		const mine = ++sequence;
		const before = get(store);
		const keep = !initial && before.status === 'ready';
		patch(keep ? { refreshing: true } : { status: 'loading', refreshing: false });
		try {
			const state = get(store);
			const response = await deps.edits.listEdits({
				tool: 'image',
				limit: size,
				offset: (state.page - 1) * size,
				...(state.query ? { search: state.query } : {})
			});
			if (mine !== sequence || destroyed) return;
			const data = response.data;
			if (!response.success || !data) {
				patch({ status: 'error', refreshing: false });
				return;
			}
			const last = pageCount(data.total, size);
			if (data.edits.length === 0 && data.total > 0 && state.page > last) {
				patch({ page: last });
				await load();
				return;
			}
			patch({
				status: 'ready',
				refreshing: false,
				edits: data.edits.slice(0, size),
				total: data.total,
				overall: state.query ? Math.max(get(store).overall, data.total) : data.total
			});
		} catch {
			if (mine !== sequence || destroyed) return;
			patch({ status: 'error', refreshing: false });
		}
	}

	function setPage(page: number): void {
		const state = get(store);
		const next = Math.min(Math.max(1, Math.floor(page)), pageCount(state.total, size));
		if (next === state.page) return;
		patch({ page: next });
		void load();
	}

	function applySearch(): void {
		timer = null;
		const state = get(store);
		const query = state.search.trim();
		if (query === state.query) return;
		patch({ query, page: 1 });
		void load();
	}

	function setSearch(text: string): void {
		patch({ search: text });
		if (timer) clearTimeout(timer);
		timer = setTimeout(applySearch, debounceMs);
	}

	function clearSearch(): void {
		if (timer) clearTimeout(timer);
		timer = null;
		patch({ search: '' });
		applySearch();
	}

	function replaceRow(edit: EditSummary): void {
		store.update((state) => ({ ...state, edits: state.edits.map((row) => (row.id === edit.id ? { ...row, ...edit } : row)) }));
	}

	async function rename(id: string, draft: string): Promise<EditActionResult> {
		const current = get(store).edits.find((row) => row.id === id);
		const name = draft.trim() || null;
		if (current && (current.name ?? null) === name) return { kind: 'unchanged' };
		try {
			const edit = (await deps.edits.renameEdit(id, name)).data?.edit;
			if (!edit) return { kind: 'failed' };
			replaceRow(edit);
			return { kind: 'done', edit };
		} catch (error) {
			if (describeEditFailure(error).kind === 'not_found') {
				await load();
				return { kind: 'missing' };
			}
			return { kind: 'failed' };
		}
	}

	async function duplicate(id: string): Promise<EditActionResult> {
		try {
			const edit = (await deps.edits.duplicateEdit(id)).data?.edit;
			if (!edit) return { kind: 'failed' };
			patch({ page: 1 });
			await load();
			return { kind: 'done', edit };
		} catch (error) {
			if (describeEditFailure(error).kind === 'not_found') {
				await load();
				return { kind: 'missing' };
			}
			return { kind: 'failed' };
		}
	}

	async function remove(id: string): Promise<EditActionResult> {
		try {
			await deps.edits.deleteEdit(id);
		} catch (error) {
			if (describeEditFailure(error).kind !== 'not_found') return { kind: 'failed' };
			await load();
			return { kind: 'missing' };
		}
		await load();
		return { kind: 'done' };
	}

	function openElsewhere(id: string): string | null {
		return openEditTabs(deps.openTabs(), deps.tabId).get(id) ?? null;
	}

	async function continueEdit(id: string): Promise<ContinueResult> {
		const elsewhere = openElsewhere(id);
		if (elsewhere) {
			deps.focusTab(elsewhere);
			return { kind: 'focused', tabId: elsewhere };
		}
		if (continuing) return { kind: 'busy' };
		continuing = true;
		try {
			const prepared = await prepareContinue(id, {
				getEdit: deps.edits.getEdit,
				library: deps.library,
				findOpen: openElsewhere
			});
			if (prepared.kind === 'focused') {
				deps.focusTab(prepared.tabId);
				return prepared;
			}
			if (prepared.kind === 'missing') {
				await load();
				return prepared;
			}
			if (prepared.kind !== 'ready') return prepared;
			deps.restoreTab({ source: prepared.source, state: prepared.state, activeTool: prepared.activeTool });
			return { kind: 'opened' };
		} finally {
			continuing = false;
		}
	}

	function destroy(): void {
		destroyed = true;
		if (timer) clearTimeout(timer);
		timer = null;
	}

	return {
		subscribe: store.subscribe,
		load: () => load(true),
		retry: () => load(true),
		setPage,
		setSearch,
		clearSearch,
		rename,
		duplicate,
		remove,
		continueEdit,
		openElsewhere,
		destroy
	};
}

export type RecentEdits = ReturnType<typeof createRecentEdits>;
