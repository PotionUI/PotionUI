import { writable, derived } from 'svelte/store';
import { logger, getErrorMessage } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import type { InspirationDto } from '$lib/services/api/inspirations';
import {
	DEFAULT_INSPIRATIONS_FILTERS,
	buildInspirationsQuery,
	totalInspirationsPages,
	type InspirationsFilters
} from '$lib/inspirations/inspirationsQuery';

export const INSPIRATIONS_ITEMS_PER_PAGE = 24;

export interface InspirationsPageState {
	items: InspirationDto[];
	totalCount: number;
	loading: boolean;
	currentPage: number;
	filters: InspirationsFilters;
	selectedId: string | null;
}

const initialState: InspirationsPageState = {
	items: [],
	totalCount: 0,
	loading: false,
	currentPage: 1,
	filters: { ...DEFAULT_INSPIRATIONS_FILTERS },
	selectedId: null
};

function createInspirationsStore() {
	const { subscribe, set, update } = writable<InspirationsPageState>(initialState);

	function snapshot(): InspirationsPageState {
		let state!: InspirationsPageState;
		subscribe((s) => (state = s))();
		return state;
	}

	async function load(opts?: { silent?: boolean }) {
		if (!opts?.silent) update((state) => ({ ...state, loading: true }));

		const { filters, currentPage } = snapshot();
		try {
			const response = await api.listInspirations(
				buildInspirationsQuery(filters, currentPage, INSPIRATIONS_ITEMS_PER_PAGE)
			);
			if (response.success && response.data) {
				const data = response.data;
				update((state) => ({ ...state, items: data.items, totalCount: data.total, loading: false }));
			} else {
				update((state) => ({ ...state, loading: false }));
			}
		} catch (error) {
			logger.error('Failed to load inspirations:', getErrorMessage(error));
			update((state) => ({ ...state, loading: false }));
		}
	}

	return {
		subscribe,

		load,

		setPage(page: number) {
			update((state) => ({ ...state, currentPage: page }));
		},

		setFilter<K extends keyof InspirationsFilters>(key: K, value: InspirationsFilters[K]) {
			update((state) => ({
				...state,
				filters: { ...state.filters, [key]: value },
				currentPage: 1
			}));
		},

		clearFilters() {
			update((state) => ({ ...state, filters: { ...DEFAULT_INSPIRATIONS_FILTERS }, currentPage: 1 }));
		},

		setSelectedId(id: string | null) {
			update((state) => ({ ...state, selectedId: id }));
		},

		/** Patches one row in place - used after a save/unsave toggle updates its counts. */
		patchItem(id: string, patch: Partial<InspirationDto>) {
			update((state) => ({
				...state,
				items: state.items.map((item) => (item.id === id ? { ...item, ...patch } : item))
			}));
		},

		async remove(id: string) {
			const response = await api.deleteInspiration(id);
			if (response.success) {
				update((state) => ({
					...state,
					items: state.items.filter((item) => item.id !== id),
					totalCount: Math.max(0, state.totalCount - 1),
					selectedId: state.selectedId === id ? null : state.selectedId
				}));
			}
			return response;
		},

		reset() {
			set({ ...initialState, filters: { ...DEFAULT_INSPIRATIONS_FILTERS } });
		}
	};
}

export const inspirationsStore = createInspirationsStore();

export const inspirationsTotalPages = derived(inspirationsStore, ($state) =>
	totalInspirationsPages($state.totalCount, INSPIRATIONS_ITEMS_PER_PAGE)
);
