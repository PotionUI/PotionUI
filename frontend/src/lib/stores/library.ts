import { writable, derived } from 'svelte/store';
import { browser } from '$app/environment';
import { logger, getErrorMessage } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import type { LibraryItem } from '$lib/services/api/library';
import type { Tag } from '$lib/types/history';
import {
	DEFAULT_LIBRARY_FILTERS,
	buildLibraryQuery,
	clampLibraryPage,
	totalLibraryPages,
	type LibraryFilters
} from '$lib/library/libraryQuery';
import { collectCopyableFileIds, type CopyableGeneration } from '$lib/library/copyToLibrary';
import { isSameLibraryRow, mergeEditedLibraryItem } from '$lib/library/libraryItemEdit';
import type { EditedMediaItem } from '$lib/services/api/media';

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
	mediaTypeCounts: {}
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

	/** Current state without waiting a tick - the store is synchronous. */
	function snapshot(): LibraryPageState {
		let state!: LibraryPageState;
		subscribe((s) => (state = s))();
		return state;
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
					loading: false
				}));
			} else {
				update((state) => ({ ...state, loading: false }));
			}
		} catch (error) {
			logger.error('Failed to load library items:', getErrorMessage(error));
			update((state) => ({ ...state, loading: false }));
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

		async loadFacets() {
			try {
				const response = await api.getLibraryFacets();
				if (response.success && response.data) {
					const counts = response.data.media_types ?? {};
					update((state) => ({ ...state, mediaTypeCounts: counts }));
				}
			} catch (error) {
				logger.error('Failed to load library facets:', getErrorMessage(error));
			}
		},

		async loadTags() {
			try {
				const response = await api.getTags(LIBRARY_TAG_TYPE);
				if (response.success && response.data) {
					const tags = response.data.tags as Tag[];
					update((state) => ({ ...state, availableTags: tags }));
				}
			} catch (error) {
				logger.error('Failed to load library tags:', getErrorMessage(error));
			}
		},

		async createTag(name: string) {
			const response = await api.createTag(name, LIBRARY_TAG_TYPE);
			if (response.success) await this.loadTags();
			return response;
		},

		setPage(page: number) {
			update((state) => ({ ...state, currentPage: page }));
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
			update((state) => ({ ...state, itemsPerPage, currentPage: 1 }));
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
				currentPage: 1
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
					currentPage: 1
				};
			});
		},

		clearTagFilters() {
			update((state) => ({
				...state,
				filters: { ...state.filters, selectedTagIds: [] },
				currentPage: 1
			}));
		},

		clearFilters() {
			update((state) => ({
				...state,
				filters: { ...DEFAULT_LIBRARY_FILTERS },
				currentPage: 1
			}));
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
				await this.loadFacets();
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
			await this.loadFacets();
			return { deleted, failed };
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
		async upload(files: File[]): Promise<{ uploaded: number; failed: number }> {
			update((state) => ({ ...state, uploading: true }));
			let uploaded = 0;
			let failed = 0;

			for (const file of files) {
				try {
					const response = await api.uploadLibraryMedia(file);
					if (response.success) uploaded += 1;
					else failed += 1;
				} catch (error) {
					logger.error('Failed to upload library file:', getErrorMessage(error));
					failed += 1;
				}
			}

			update((state) => ({ ...state, uploading: false, currentPage: 1 }));
			if (uploaded > 0) {
				await load();
				await this.loadFacets();
			}
			return { uploaded, failed };
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
			set({ ...initialState, filters: { ...DEFAULT_LIBRARY_FILTERS } });
		}
	};
}

export const libraryStore = createLibraryStore();

export const libraryTotalPages = derived(libraryStore, ($library) =>
	totalLibraryPages($library.totalCount, $library.itemsPerPage)
);
