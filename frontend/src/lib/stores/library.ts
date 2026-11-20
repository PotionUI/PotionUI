import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';
import { logger, getErrorMessage } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import { tagCatalog } from '$lib/stores/tagCatalog';
import { libraryCollectionsStore } from '$lib/stores/collections';
import type { BulkDeleteLibraryByCriteriaRequest, LibraryFacetScope, LibraryItem } from '$lib/services/api/library';
import { collectionQueryParams } from '$lib/components/collections/selection';
import type { Tag } from '$lib/types/history';
import {
	DEFAULT_LIBRARY_FILTERS,
	buildLibraryQuery,
	clampLibraryPage,
	hasActiveLibraryFilters,
	totalLibraryPages,
	type LibraryFilters
} from '$lib/library/libraryQuery';
import { collectCopyableFileIds, type CopyableGeneration } from '$lib/library/copyToLibrary';
import {
	isSameLibraryRow,
	libraryItemFromEdit,
	mergeEditedLibraryItem,
	type EditedLibraryResult
} from '$lib/library/libraryItemEdit';
import type { EditedMediaItem } from '$lib/services/api/media';
import { isPerItemError, refusalMessage } from '$lib/plans/refusal';
import { blockedUploadMessage } from '$lib/plans/uploadLimit';

export const LIBRARY_ITEMS_PER_PAGE_OPTIONS = [12, 24, 48, 96] as const;

const LIBRARY_ITEMS_PER_PAGE_STORAGE_KEY = 'library-items-per-page';
const DEFAULT_ITEMS_PER_PAGE = 24;

/** Library tags are their own vocabulary (`type = 'UPLOAD'`), never generation tags. */
export const LIBRARY_TAG_TYPE = 'UPLOAD' as const;

export interface LibraryPageState {
	items: LibraryItem[];
	totalCount: number;
	loading: boolean;
	uploading: boolean;
	currentPage: number;
	itemsPerPage: number;
	filters: LibraryFilters;
	selectedItem: LibraryItem | null;
	selectionMode: boolean;
	selectedIds: string[];
	availableTags: Tag[];
	mediaTypeCounts: Record<string, number>;
	freshIds: string[];
	placedIds: string[];
}

const initialState: LibraryPageState = {
	items: [],
	totalCount: 0,
	loading: false,
	uploading: false,
	currentPage: 1,
	itemsPerPage: DEFAULT_ITEMS_PER_PAGE,
	filters: { ...DEFAULT_LIBRARY_FILTERS },
	selectedItem: null,
	selectionMode: false,
	selectedIds: [],
	availableTags: [],
	mediaTypeCounts: {},
	freshIds: [],
	placedIds: []
};

function isItemsPerPageOption(value: number): boolean {
	return LIBRARY_ITEMS_PER_PAGE_OPTIONS.includes(
		value as (typeof LIBRARY_ITEMS_PER_PAGE_OPTIONS)[number]
	);
}

function loadItemsPerPage(): number {
	if (!browser) return DEFAULT_ITEMS_PER_PAGE;
	try {
		const stored = Number(localStorage.getItem(LIBRARY_ITEMS_PER_PAGE_STORAGE_KEY));
		return isItemsPerPageOption(stored) ? stored : DEFAULT_ITEMS_PER_PAGE;
	} catch {
		// localStorage may be unavailable; the default still lets the library load.
		return DEFAULT_ITEMS_PER_PAGE;
	}
}

function createLibraryStore() {
	const { subscribe, set, update } = writable<LibraryPageState>(initialState);

	tagCatalog.tags(LIBRARY_TAG_TYPE).subscribe((tags) => {
		update((state) => ({ ...state, availableTags: tags as unknown as Tag[] }));
	});

	/** Current state without waiting a tick - the store is synchronous. */
	function snapshot(): LibraryPageState {
		let state!: LibraryPageState;
		subscribe((s) => (state = s))();
		return state;
	}

	let facetsScopeKey = '{}';

	function facetScope(filters: LibraryFilters): LibraryFacetScope {
		return {
			...collectionQueryParams(filters),
			...(filters.favoritesOnly ? { favorites_only: true } : {})
		};
	}

	async function load(opts?: { silent?: boolean }) {
		if (!opts?.silent) update((state) => ({ ...state, loading: true }));

		const { filters, currentPage, itemsPerPage } = snapshot();
		try {
			const response = await api.listLibraryItems(
				buildLibraryQuery(filters, currentPage, itemsPerPage)
			);
			if (response.success && response.data) {
				const data = response.data;
				update((state) => ({
					...state,
					items: data.items,
					totalCount: data.total,
					loading: false,
					freshIds: [],
					placedIds: []
				}));
				if (JSON.stringify(facetScope(filters)) !== facetsScopeKey) await loadFacets();
			} else {
				update((state) => ({ ...state, loading: false }));
			}
		} catch (error) {
			logger.error('Failed to load library items:', getErrorMessage(error));
			update((state) => ({ ...state, loading: false }));
		}
	}

	async function loadFacets() {
		const scope = facetScope(snapshot().filters);
		try {
			const response = await api.getLibraryFacets(scope);
			if (response.success && response.data) {
				const counts = response.data.media_types ?? {};
				facetsScopeKey = JSON.stringify(scope);
				update((state) => ({ ...state, mediaTypeCounts: counts }));
			}
		} catch (error) {
			logger.error('Failed to load library facets:', getErrorMessage(error));
		}
	}

	/**
	 * Reload after a removal. The page the user is on can no longer exist once
	 * the last item on it is gone, so land on the last populated page instead of
	 * an empty one.
	 */
	async function reloadAfterRemoval(removed: number) {
		const { currentPage, itemsPerPage, totalCount } = snapshot();
		const nextPage = clampLibraryPage(currentPage, Math.max(0, totalCount - removed), itemsPerPage);
		update((state) => ({ ...state, currentPage: nextPage }));
		await load();
	}

	return {
		subscribe,

		load,

		loadFacets,

		async loadTags() {
			try {
				await tagCatalog.get(LIBRARY_TAG_TYPE, { fresh: true });
			} catch (error) {
				logger.error('Failed to load library tags:', getErrorMessage(error));
			}
		},

		async createTag(name: string) {
			const response = await tagCatalog.create(name, LIBRARY_TAG_TYPE);
			if (response.success) await this.loadTags();
			return response;
		},

		setPage(page: number) {
			update((state) => ({ ...state, currentPage: page, freshIds: [], placedIds: [] }));
		},

		setItemsPerPage(itemsPerPage: number) {
			if (!isItemsPerPageOption(itemsPerPage)) return;
			if (browser) {
				try {
					localStorage.setItem(LIBRARY_ITEMS_PER_PAGE_STORAGE_KEY, String(itemsPerPage));
				} catch {
					// Keep the in-memory selection when localStorage is unavailable.
				}
			}
			update((state) => ({ ...state, itemsPerPage, currentPage: 1, freshIds: [], placedIds: [] }));
		},

		// Restores the viewer's display preference after hydration, before the
		// first request - keeping the initial state deterministic avoids an
		// SSR/client hydration mismatch.
		restoreItemsPerPage() {
			const itemsPerPage = loadItemsPerPage();
			update((state) => ({ ...state, itemsPerPage }));
		},

		setFilter<K extends keyof LibraryFilters>(key: K, value: LibraryFilters[K]) {
			update((state) => ({
				...state,
				filters: { ...state.filters, [key]: value },
				currentPage: 1,
				freshIds: [],
				placedIds: []
			}));
		},

		toggleTagFilter(tagId: string) {
			update((state) => {
				const selected = state.filters.selectedTagIds.includes(tagId);
				return {
					...state,
					filters: {
						...state.filters,
						selectedTagIds: selected
							? state.filters.selectedTagIds.filter((id) => id !== tagId)
							: [...state.filters.selectedTagIds, tagId]
					},
					currentPage: 1,
					freshIds: [],
					placedIds: []
				};
			});
		},

		clearTagFilters() {
			update((state) => ({
				...state,
				filters: { ...state.filters, selectedTagIds: [] },
				currentPage: 1,
				freshIds: [],
				placedIds: []
			}));
		},

		clearFilters() {
			update((state) => ({
				...state,
				filters: { ...DEFAULT_LIBRARY_FILTERS, sortBy: state.filters.sortBy, sortDir: state.filters.sortDir },
				currentPage: 1,
				freshIds: [],
				placedIds: []
			}));
		},

		markFresh(ids: readonly string[]) {
			update((state) => ({ ...state, freshIds: [...new Set([...state.freshIds, ...ids])] }));
		},

		setSelectedItem(item: LibraryItem | null) {
			update((state) => ({ ...state, selectedItem: item }));
		},

		toggleSelect(itemId: string) {
			update((state) => {
				const isSelected = state.selectedIds.includes(itemId);
				const selectedIds = isSelected
					? state.selectedIds.filter((id) => id !== itemId)
					: [...state.selectedIds, itemId];
				return { ...state, selectedIds, selectionMode: selectedIds.length > 0 };
			});
		},

		selectAll() {
			update((state) => {
				const selectedIds = state.items.map((item) => item.id);
				return { ...state, selectedIds, selectionMode: selectedIds.length > 0 };
			});
		},

		/** Replaces the selection outright - used by shift-range and marquee drag. */
		setSelection(ids: string[]) {
			update((state) => ({ ...state, selectedIds: ids, selectionMode: ids.length > 0 }));
		},

		clearSelection() {
			update((state) => ({ ...state, selectedIds: [], selectionMode: false }));
		},

		toggleSelectionMode() {
			update((state) => ({
				...state,
				selectionMode: !state.selectionMode,
				selectedIds: []
			}));
		},

		async deleteItem(itemId: string) {
			const response = await api.deleteLibraryItem(itemId);
			if (response.success) {
				update((state) => ({
					...state,
					selectedItem: state.selectedItem?.id === itemId ? null : state.selectedItem,
					selectedIds: state.selectedIds.filter((id) => id !== itemId)
				}));
				await reloadAfterRemoval(1);
				await Promise.all([this.loadFacets(), libraryCollectionsStore.load()]);
			}
			return response;
		},

		/**
		 * Deletes the current selection. The route deletes one item at a time, so
		 * a failure part-way through leaves the rest deleted - the count of what
		 * actually went is what the caller reports.
		 */
		async bulkDelete(): Promise<{ deleted: number; failed: number }> {
			const ids = snapshot().selectedIds;
			let deleted = 0;
			let failed = 0;

			for (const id of ids) {
				try {
					const response = await api.deleteLibraryItem(id);
					if (response.success) deleted += 1;
					else failed += 1;
				} catch (error) {
					logger.error('Failed to delete library item:', getErrorMessage(error));
					failed += 1;
				}
			}

			update((state) => ({ ...state, selectedIds: [], selectionMode: false }));
			await reloadAfterRemoval(deleted);
			await Promise.all([this.loadFacets(), deleted > 0 ? libraryCollectionsStore.load() : undefined]);
			return { deleted, failed };
		},

		/** Deletes every library item matching criteria, AND-ed. */
		async bulkDeleteByCriteria(criteria: BulkDeleteLibraryByCriteriaRequest) {
			const response = await api.bulkDeleteLibraryItemsByCriteria(criteria);
			if (response.success && response.data) {
				await reloadAfterRemoval(response.data.deleted_count);
				await Promise.all([this.loadFacets(), libraryCollectionsStore.load()]);
			}
			return response;
		},

		/** Replaces one item's tags, keeping the loaded page and open preview in step. */
		async setItemTags(itemId: string, tagIds: string[]) {
			const response = await api.setLibraryItemTags(itemId, tagIds);
			if (response.success && response.data) {
				const tags = response.data.tags;
				update((state) => ({
					...state,
					items: state.items.map((item) => (item.id === itemId ? { ...item, tags } : item)),
					selectedItem:
						state.selectedItem?.id === itemId
							? { ...state.selectedItem, tags }
							: state.selectedItem
				}));
			}
			return response;
		},

		async setItemFavorite(itemId: string, isFavorite: boolean): Promise<boolean> {
			const previous = snapshot();
			const before =
				previous.items.find((item) => item.id === itemId)?.is_favorite ??
				(previous.selectedItem?.id === itemId ? previous.selectedItem.is_favorite : undefined) ??
				false;
			const patch = (value: boolean) =>
				update((state) => ({
					...state,
					items: state.items.map((item) =>
						item.id === itemId ? { ...item, is_favorite: value } : item
					),
					selectedItem:
						state.selectedItem?.id === itemId
							? { ...state.selectedItem, is_favorite: value }
							: state.selectedItem
				}));

			patch(isFavorite);
			try {
				const response = await api.setLibraryFavorite(itemId, isFavorite);
				if (!response.success) throw new Error('Favorite update failed');
			} catch (error) {
				logger.error('Failed to set library favorite:', getErrorMessage(error));
				patch(before);
				return false;
			}

			if (!isFavorite && snapshot().filters.favoritesOnly) {
				await reloadAfterRemoval(1);
				await loadFacets();
			}
			await libraryCollectionsStore.load();
			return true;
		},

		/**
		 * Folds an edit's result back in.
		 *
		 * A replace kept the row, so the loaded page and the open preview can be
		 * patched where they stand - and must be, because the file behind the
		 * row is a different one at a different url. A save-as-new is a row this
		 * page may not even hold, so it is a reload onto page 1, where the
		 * newest-first listing puts it.
		 */
		async applyEditResult(edited: EditedMediaItem, replaced: boolean) {
			if (replaced) {
				update((state) => ({
					...state,
					items: state.items.map((item) =>
						isSameLibraryRow(item, edited) ? mergeEditedLibraryItem(item, edited) : item
					),
					selectedItem:
						state.selectedItem && isSameLibraryRow(state.selectedItem, edited)
							? mergeEditedLibraryItem(state.selectedItem, edited)
							: state.selectedItem
				}));
				await this.loadFacets();
				return;
			}

			await this.showNewRows();
		},

		async applyToolResult(
			results: EditedLibraryResult[],
			sourceIds: string[],
			options: { replaced?: boolean } = {}
		): Promise<{ added: number; replaced: number }> {
			const before = snapshot();
			const known = new Set(before.items.map((item) => item.id));
			if (before.selectedItem) known.add(before.selectedItem.id);
			const patched = results.filter((result) => known.has(result.id));
			const added = options.replaced === true ? [] : results.filter((result) => !known.has(result.id));

			if (patched.length > 0) {
				const byId = new Map(patched.map((result) => [result.id, result]));
				update((state) => ({
					...state,
					items: state.items.map((item) => {
						const edited = byId.get(item.id);
						return edited ? mergeEditedLibraryItem(item, edited) : item;
					}),
					selectedItem:
						state.selectedItem && byId.has(state.selectedItem.id)
							? mergeEditedLibraryItem(state.selectedItem, byId.get(state.selectedItem.id)!)
							: state.selectedItem
				}));
			}

			if (added.length > 0) {
				const fresh = added.map((result) => result.id);
				const markFresh = (placed: boolean) =>
					update((state) => ({
						...state,
						freshIds: [...state.freshIds, ...fresh],
						placedIds: placed ? [...state.placedIds, ...fresh] : state.placedIds
					}));
				const sourceIndex = before.items.findIndex((item) => sourceIds.includes(item.id));

				if (hasActiveLibraryFilters(before.filters)) {
					await load({ silent: true });
					markFresh(false);
				} else if (sourceIndex >= 0) {
					const rows = added.map(libraryItemFromEdit);
					update((state) => {
						const at = state.items.findIndex((item) => sourceIds.includes(item.id));
						const index = at >= 0 ? at : 0;
						return {
							...state,
							items: [...state.items.slice(0, index), ...rows, ...state.items.slice(index)],
							totalCount: state.totalCount + rows.length
						};
					});
					markFresh(true);
				} else {
					await this.showNewRows();
					markFresh(false);
				}
				await Promise.all([this.loadFacets(), libraryCollectionsStore.load()]);
			} else {
				await this.loadFacets();
			}

			return { added: added.length, replaced: patched.length };
		},

		clearFresh() {
			update((state) =>
				state.freshIds.length > 0 || state.placedIds.length > 0
					? { ...state, freshIds: [], placedIds: [] }
					: state
			);
		},

		/**
		 * Brings rows this page may not hold into view: reload onto page 1, where
		 * the newest-first listing puts anything just created. Used by a
		 * save-as-new edit and by a split, which makes several rows at once.
		 */
		async showNewRows() {
			update((state) => ({ ...state, currentPage: 1 }));
			await load();
			await this.loadFacets();
		},

		/** Uploads files into the library, one request each, then reloads page 1. */
		async upload(files: File[]): Promise<{ uploaded: number; failed: number; refusal: string | null }> {
			update((state) => ({ ...state, uploading: true }));
			let uploaded = 0;
			let failed = 0;
			let refusal: string | null = null;

			for (const file of files) {
				const blocked = blockedUploadMessage([file]);
				if (blocked) {
					failed += 1;
					refusal = refusal ?? blocked;
					continue;
				}
				try {
					const response = await api.uploadLibraryMedia(file);
					if (response.success) uploaded += 1;
					else failed += 1;
				} catch (error) {
					logger.error('Failed to upload library file:', getErrorMessage(error));
					failed += 1;
					const message = refusalMessage(error);
					if (message) {
						refusal = refusal ?? message;
						if (!isPerItemError(error)) break;
					}
				}
			}

			update((state) => ({ ...state, uploading: false, currentPage: 1 }));
			if (uploaded > 0) {
				await load();
				await this.loadFacets();
			}
			return { uploaded, failed, refusal };
		},

		/**
		 * Copies generated files into the library. A copy, never a move - the
		 * generations stay in history untouched.
		 */
		async copyFromGenerations(
			generations: CopyableGeneration[]
		): Promise<{ copied: number; failed: number }> {
			const fileIds = collectCopyableFileIds(generations);
			let copied = 0;
			let failed = 0;

			for (const fileId of fileIds) {
				try {
					const response = await api.copyGenerationFileToLibrary(fileId);
					if (response.success) copied += 1;
					else failed += 1;
				} catch (error) {
					logger.error('Failed to copy generated file into library:', getErrorMessage(error));
					failed += 1;
				}
			}

			return { copied, failed };
		},

		reset() {
			facetsScopeKey = '{}';
			set({ ...initialState, filters: { ...DEFAULT_LIBRARY_FILTERS } });
		}
	};
}

export const libraryStore = createLibraryStore();

export const libraryTotalPages = derived(libraryStore, ($library) =>
	totalLibraryPages($library.totalCount, $library.itemsPerPage)
);
