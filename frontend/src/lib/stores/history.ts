import { logger, getErrorMessage } from '$lib/utils/logger';
import { writable, derived, get } from 'svelte/store';
import { browser } from '$app/environment';
import type {
	GenerationHistoryItem,
	GenerationHistoryFilters,
	HistoryPageState,
	Tag,
	DatePreset,
	SortBy,
	SortDir
} from '$lib/types/history';
import { api } from '$lib/services/api/index';

export const HISTORY_ITEMS_PER_PAGE_OPTIONS = [6, 12, 24, 48, 96] as const;

const HISTORY_ITEMS_PER_PAGE_STORAGE_KEY = 'history-items-per-page';
const DEFAULT_ITEMS_PER_PAGE = 24;

function isItemsPerPageOption(value: number): value is (typeof HISTORY_ITEMS_PER_PAGE_OPTIONS)[number] {
	return HISTORY_ITEMS_PER_PAGE_OPTIONS.includes(value as (typeof HISTORY_ITEMS_PER_PAGE_OPTIONS)[number]);
}

function loadItemsPerPage(): number {
	if (!browser) return DEFAULT_ITEMS_PER_PAGE;
	try {
		const stored = Number(localStorage.getItem(HISTORY_ITEMS_PER_PAGE_STORAGE_KEY));
		return isItemsPerPageOption(stored) ? stored : DEFAULT_ITEMS_PER_PAGE;
	} catch {
		// localStorage may be unavailable; the default still lets history load.
		return DEFAULT_ITEMS_PER_PAGE;
	}
}

// Helper function to format date for API (using local timezone)
function formatDate(date: Date): string {
	const year = date.getFullYear();
	const month = String(date.getMonth() + 1).padStart(2, '0');
	const day = String(date.getDate()).padStart(2, '0');
	return `${year}-${month}-${day}`;
}

// Helper function to get date range for preset
function getDateRangeForPreset(preset: DatePreset): { createdFrom?: string; createdTo?: string } {
	const today = new Date();
	today.setHours(0, 0, 0, 0);

	switch (preset) {
		case 'today':
			return {
				createdFrom: formatDate(today),
				createdTo: formatDate(today)
			};

		case 'yesterday':
			const yesterday = new Date(today);
			yesterday.setDate(yesterday.getDate() - 1);
			return {
				createdFrom: formatDate(yesterday),
				createdTo: formatDate(yesterday)
			};

		case 'last_week':
			const weekAgo = new Date(today);
			weekAgo.setDate(weekAgo.getDate() - 7);
			return {
				createdFrom: formatDate(weekAgo),
				createdTo: formatDate(today)
			};

		case 'last_month':
			const monthAgo = new Date(today);
			monthAgo.setMonth(monthAgo.getMonth() - 1);
			return {
				createdFrom: formatDate(monthAgo),
				createdTo: formatDate(today)
			};

		case 'all':
		default:
			return {};
	}
}

// Initial state
const initialFilters: GenerationHistoryFilters = {
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
};

const initialState: HistoryPageState = {
	generations: [],
	totalCount: 0,
	loading: false,
	currentPage: 1,
	itemsPerPage: DEFAULT_ITEMS_PER_PAGE,
	filters: initialFilters,
	selectedGeneration: null,
	selectedFileIndex: 0,
	selectionMode: false,
	selectedGenerationIds: [],
	availableTags: [],
	facets: { modes: [], presets: [], models: [] }
};

export type HistoryQueryState = Pick<HistoryPageState, 'currentPage' | 'itemsPerPage' | 'filters'>;

// The exact payload sent to GET /api/generations. Also the source of the query
// key, so anything that changes the request necessarily changes the key.
function buildHistoryRequest(state: HistoryQueryState) {
	const { currentPage, itemsPerPage, filters } = state;
	return {
		limit: itemsPerPage,
		offset: (currentPage - 1) * itemsPerPage,
		status: filters.status === 'all' ? undefined : filters.status,
		createdFrom: filters.dateFrom,
		createdTo: filters.dateTo,
		tagIds: filters.selectedTagIds.length > 0 ? filters.selectedTagIds : undefined,
		includeTags: true,
		mediaType: filters.mediaType === 'all' ? undefined : filters.mediaType,
		search: filters.searchMode === 'semantic' ? undefined : filters.search || undefined,
		semanticQuery: filters.searchMode === 'semantic' ? filters.search || undefined : undefined,
		mode: filters.mode || undefined,
		presetId: filters.presetId || undefined,
		modelName: filters.modelName || undefined,
		collectionId: filters.collectionId || undefined,
		usedPhrasebookValueId: filters.usedPhrasebookValueId || undefined,
		systemTag: filters.systemTag || undefined,
		minRating: filters.minRating || undefined,
		favoritesOnly: filters.favoritesOnly || undefined,
		sortBy: filters.sortBy,
		sortDir: filters.sortDir
	};
}

// Stable identity of the list the current page/filter state describes.
export function historyQueryKey(state: HistoryQueryState): string {
	return JSON.stringify(buildHistoryRequest(state));
}

export interface RequestLifecycleRun<T> {
	// Identity of the query being issued, compared against currentKey() on resolve.
	key: string;
	// Requests sharing a key but applying results differently (replace vs merge)
	// must not coalesce onto each other.
	bucket?: string;
	silent?: boolean;
	fetch: () => Promise<T>;
	// Invoked only when the response is still the one the caller is waiting for.
	commit: (value: T) => void;
}

export interface RequestLifecycle {
	run<T>(request: RequestLifecycleRun<T>): Promise<boolean>;
	hasForegroundInFlight(): boolean;
}

// Guards an async read against out-of-order responses: a response commits only
// if its epoch is the newest issued and the caller's state still describes the
// same query. Identical in-flight requests share one fetch; a foreground
// request never joins a silent one, so it can own the loading state.
export function createRequestLifecycle(currentKey: () => string): RequestLifecycle {
	let issued = 0;
	let latest = 0;
	let foreground = 0;
	const pending = new Map<string, { silent: boolean; epoch: number; promise: Promise<boolean> }>();

	return {
		hasForegroundInFlight: () => foreground > 0,

		run<T>({ key, bucket = '', silent = false, fetch, commit }: RequestLifecycleRun<T>) {
			const slot = `${bucket}\u0000${key}`;
			const existing = pending.get(slot);
			if (existing && (silent || !existing.silent)) return existing.promise;

			const epoch = ++issued;
			latest = epoch;
			if (!silent) foreground += 1;

			const promise = (async () => {
				try {
					const value = await fetch();
					if (epoch !== latest || key !== currentKey()) return false;
					commit(value);
					return true;
				} finally {
					if (pending.get(slot)?.epoch === epoch) pending.delete(slot);
					if (!silent) foreground -= 1;
				}
			})();

			pending.set(slot, { silent, epoch, promise });
			return promise;
		}
	};
}

// Create the store
function createHistoryStore() {
	const store = writable<HistoryPageState>(initialState);
	const { subscribe, set, update } = store;
	const lifecycle = createRequestLifecycle(() => historyQueryKey(get(store)));

	return {
		subscribe,

		// Load generations from API. Pass { silent: true } to reconcile in the
		// background (polling / live updates) without showing loading skeletons.
		// Pass { merge: true } to preserve the live (WebSocket-driven) status/progress
		// of in-progress generations instead of overwriting them with the DB row
		// (the DB keeps a running generation as 'pending', which would flicker).
		async loadGenerations(opts?: { silent?: boolean; merge?: boolean }) {
			const silent = opts?.silent === true;
			const merge = opts?.merge === true;
			const snapshot = get(store);
			const request = buildHistoryRequest(snapshot);
			const key = historyQueryKey(snapshot);

			if (!silent) {
				update((state) => ({ ...state, loading: true }));
			}

			try {
				await lifecycle.run({
					key,
					bucket: merge ? 'merge' : 'replace',
					silent,
					fetch: () => api.getGenerationHistory(request),
					commit: (response) => {
						if (!response.success || !response.data) return;
						const data = response.data;
						update((state) => {
							if (!merge) {
								return { ...state, generations: data.generations, totalCount: data.total };
							}
							// Merge: keep live status/progress for generations that are
							// in-progress both locally and on the server, so a background
							// refetch never downgrades 'running' back to 'pending'.
							const localById = new Map(state.generations.map((g) => [g.id, g]));
							const terminal = (s: string) =>
								s === 'completed' || s === 'failed' || s === 'cancelled';
							const generations = data.generations.map((incoming) => {
								const local = localById.get(incoming.id);
								if (!local) return incoming;
								if (!terminal(incoming.status) && !terminal(local.status)) {
									return { ...incoming, status: local.status, progress: local.progress };
								}
								return incoming;
							});
							return { ...state, generations, totalCount: data.total };
						});
					}
				});
			} catch (error) {
				logger.error('Failed to load generation history:', error);
			} finally {
				// Only the last foreground request in flight owns the loading flag, so a
				// superseded response cannot end the newer one's loading state.
				if (!silent && !lifecycle.hasForegroundInFlight()) {
					update((state) => ({ ...state, loading: false }));
				}
			}
		},

		// Set current page
		setPage(page: number) {
			update((state) => ({ ...state, currentPage: page }));
		},

		// Set items per page
		setItemsPerPage(itemsPerPage: number) {
			if (!isItemsPerPageOption(itemsPerPage)) return;
			if (browser) {
				try {
					localStorage.setItem(HISTORY_ITEMS_PER_PAGE_STORAGE_KEY, String(itemsPerPage));
				} catch {
					// Keep the in-memory selection when localStorage is unavailable.
				}
			}
			update((state) => ({ ...state, itemsPerPage, currentPage: 1 }));
		},

		// Restore the viewer's display preference after hydration, before the
		// history request starts. Keeping the initial state deterministic avoids
		// an SSR/client hydration mismatch.
		restoreItemsPerPage() {
			const itemsPerPage = loadItemsPerPage();
			update((state) => ({ ...state, itemsPerPage }));
		},

		// Set filter
		setFilter(key: keyof GenerationHistoryFilters, value: any) {
			update((state) => ({
				...state,
				filters: { ...state.filters, [key]: value },
				currentPage: 1 // Reset to first page when changing filters
			}));
		},

		// Set date preset
		setDatePreset(preset: DatePreset) {
			const range = getDateRangeForPreset(preset);
			update((state) => ({
				...state,
				filters: {
					...state.filters,
					datePreset: preset,
					dateFrom: range.createdFrom,
					dateTo: range.createdTo
				},
				currentPage: 1
			}));
		},

		// Set sort field + direction (resets to first page)
		setSort(sortBy: SortBy, sortDir: SortDir) {
			update((state) => ({
				...state,
				filters: { ...state.filters, sortBy, sortDir },
				currentPage: 1
			}));
		},

		// Load facet counts (modes/presets/models) for filter controls
		async loadFacets() {
			try {
				const response = await api.getHistoryFacets();
				if (response.success && response.data) {
					const facets = response.data;
					update((state) => ({ ...state, facets }));
				}
			} catch (error) {
				logger.error('Failed to load history facets:', error);
			}
		},

		// Set a generation's rating (optimistic; reload on failure)
		async setRating(generationId: string, rating: number) {
			let previous: number | undefined;
			update((state) => ({
				...state,
				generations: state.generations.map((gen) => {
					if (gen.id !== generationId) return gen;
					previous = gen.rating;
					return { ...gen, rating };
				}),
				selectedGeneration:
					state.selectedGeneration?.id === generationId
						? { ...state.selectedGeneration, rating }
						: state.selectedGeneration
			}));

			try {
				const response = await api.setGenerationRating(generationId, rating);
				if (!response.success) throw new Error('Rating update failed');
			} catch (error) {
				logger.error('Failed to set rating:', getErrorMessage(error));
				await this.loadGenerations();
			}
		},

		// Toggle a generation's favorite flag (optimistic; reload on failure)
		async toggleFavorite(generationId: string) {
			let nextValue = false;
			update((state) => ({
				...state,
				generations: state.generations.map((gen) => {
					if (gen.id !== generationId) return gen;
					nextValue = !gen.is_favorite;
					return { ...gen, is_favorite: nextValue };
				}),
				selectedGeneration:
					state.selectedGeneration?.id === generationId
						? { ...state.selectedGeneration, is_favorite: !state.selectedGeneration.is_favorite }
						: state.selectedGeneration
			}));

			try {
				const response = await api.setGenerationFavorite(generationId, nextValue);
				if (!response.success) throw new Error('Favorite update failed');
			} catch (error) {
				logger.error('Failed to toggle favorite:', getErrorMessage(error));
				await this.loadGenerations();
			}
		},

		// Apply a live status/progress update (from the generation WebSocket) to a
		// generation already present in the list. No-op if it isn't loaded.
		applyLiveStatus(generationId: string, status: string, progress?: number) {
			const s = status as GenerationHistoryItem['status'];
			update((state) => {
				let changed = false;
				const generations = state.generations.map((gen) => {
					if (gen.id !== generationId) return gen;
					changed = true;
					return { ...gen, status: s, progress: progress ?? gen.progress };
				});
				if (!changed) return state;
				return {
					...state,
					generations,
					selectedGeneration:
						state.selectedGeneration?.id === generationId
							? {
									...state.selectedGeneration,
									status: s,
									progress: progress ?? state.selectedGeneration.progress
								}
							: state.selectedGeneration
				};
			});
		},

		// Add tag to filter
		addTagFilter(tagId: string) {
			update((state) => ({
				...state,
				filters: {
					...state.filters,
					selectedTagIds: [...state.filters.selectedTagIds, tagId]
				},
				currentPage: 1
			}));
		},

		// Filter by an auto-tagger system tag (single active facet; null clears)
		setSystemTagFilter(tag: string | null) {
			update((state) => ({
				...state,
				filters: { ...state.filters, systemTag: tag || undefined },
				currentPage: 1
			}));
		},

		// Remove tag from filter
		removeTagFilter(tagId: string) {
			update((state) => ({
				...state,
				filters: {
					...state.filters,
					selectedTagIds: state.filters.selectedTagIds.filter((id) => id !== tagId)
				},
				currentPage: 1
			}));
		},

		// Clear all filters
		clearFilters() {
			update((state) => ({
				...state,
				filters: initialFilters,
				currentPage: 1
			}));
		},

		// Set selected generation
		setSelectedGeneration(generation: GenerationHistoryItem | null, fileIndex: number = 0) {
			update((state) => ({
				...state,
				selectedGeneration: generation,
				selectedFileIndex: fileIndex
			}));
		},

		// Delete generation
		async deleteGeneration(generationId: string) {
			try {
				const response = await api.deleteGenerationHistory(generationId);
				if (response.success) {
					// Reload generations after deletion
					await this.loadGenerations();
					// Clear selected generation if it was the one deleted
					update((state) => ({
						...state,
						selectedGeneration:
							state.selectedGeneration?.id === generationId ? null : state.selectedGeneration
					}));
				}
			} catch (error) {
				logger.error('Failed to delete generation:', error);
				throw error;
			}
		},

		// Toggle selection mode
		toggleSelectionMode() {
			update((state) => ({
				...state,
				selectionMode: !state.selectionMode,
				selectedGenerationIds: [] // Clear selections when toggling mode
			}));
		},

		// Toggle generation selection
		toggleGenerationSelection(generationId: string) {
			update((state) => {
				const isSelected = state.selectedGenerationIds.includes(generationId);
				const selectedGenerationIds = isSelected
					? state.selectedGenerationIds.filter((id) => id !== generationId)
					: [...state.selectedGenerationIds, generationId];
				return {
					...state,
					selectedGenerationIds,
					selectionMode: selectedGenerationIds.length > 0
				};
			});
		},

		// Toggle selection from an always-visible card checkbox (no explicit
		// "selection mode" toggle needed): selecting anything turns selection mode
		// on so the floating actions panel appears; deselecting the last item
		// turns it back off.
		toggleSelect(generationId: string) {
			update((state) => {
				const isSelected = state.selectedGenerationIds.includes(generationId);
				const selectedGenerationIds = isSelected
					? state.selectedGenerationIds.filter((id) => id !== generationId)
					: [...state.selectedGenerationIds, generationId];
				return {
					...state,
					selectedGenerationIds,
					selectionMode: selectedGenerationIds.length > 0
				};
			});
		},

		// Select all generations on current page
		selectAll() {
			update((state) => {
				const selectedGenerationIds = state.generations.map((gen) => gen.id);
				return {
					...state,
					selectedGenerationIds,
					selectionMode: selectedGenerationIds.length > 0
				};
			});
		},

		// Clear all selections
		clearSelection() {
			update((state) => ({
				...state,
				selectedGenerationIds: [],
				selectionMode: false
			}));
		},

		// Bulk delete selected generations
		async bulkDeleteGenerations() {
			try {
				const state = get(store);

				if (state.selectedGenerationIds.length === 0) {
					throw new Error('No generations selected');
				}

				const response = await api.bulkDeleteGenerations(state.selectedGenerationIds);
				if (response.success) {
					// Reload generations after deletion
					await this.loadGenerations();
					// Clear selections and exit selection mode
					update((s) => ({
						...s,
						selectedGenerationIds: [],
						selectionMode: false
					}));
					return response;
				}
			} catch (error) {
				logger.error('Failed to bulk delete generations:', error);
				throw error;
			}
		},

		// Load available generation tags
		async loadTags() {
			try {
				const response = await api.getTags('GENERATION');
				if (response.success && response.data) {
					const tags = response.data.tags;
					update((state) => ({ ...state, availableTags: tags }));
				}
			} catch (error) {
				logger.error('Failed to load tags:', error);
			}
		},

		// Create a new generation tag
		async createTag(name: string) {
			const response = await api.createTag(name, 'GENERATION');
			if (response.success) {
				await this.loadTags();
			}
			return response;
		},

		// Bulk delete generations matching all given tags
		async bulkDeleteByTags(tagIds: string[]) {
			const response = await api.bulkDeleteByTags(tagIds);
			if (response.success) {
				await this.loadGenerations();
				await this.loadTags();
			}
			return response;
		},

		// Reset store to initial state
		reset() {
			set(initialState);
		}
	};
}

export const historyStore = createHistoryStore();

// Derived stores
export const totalPages = derived(historyStore, ($history) =>
	Math.ceil($history.totalCount / $history.itemsPerPage)
);

// Search + filtering is now server-side (see loadGenerations). This derived
// store is a passthrough of the server-provided generations list, kept so
// existing consumers (grid, selection toolbar) continue to work unchanged.
export const filteredGenerations = derived(historyStore, ($history) => $history.generations);
