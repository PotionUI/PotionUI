import { writable } from 'svelte/store';
import { logger, getErrorMessage } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import type { InspirationCollection } from '$lib/services/api/inspirations';

interface InspirationsCollectionsState {
	collections: InspirationCollection[];
	loading: boolean;
}

const initialState: InspirationsCollectionsState = {
	collections: [],
	loading: false
};

/**
 * Caller-scoped collections for Inspirations - a separate tree from the
 * history/library one (own endpoints, own rows). No bulk-move: the
 * contract exposes single-item reparenting via `parent_id` only.
 */
function createInspirationsCollectionsStore() {
	const { subscribe, update, set } = writable<InspirationsCollectionsState>(initialState);

	async function refresh() {
		update((state) => ({ ...state, loading: true }));
		try {
			const response = await api.listInspirationCollections();
			if (response.success && response.data) {
				update((state) => ({ ...state, collections: response.data!.items, loading: false }));
			} else {
				update((state) => ({ ...state, loading: false }));
			}
		} catch (error) {
			logger.error('Failed to load inspiration collections:', getErrorMessage(error));
			update((state) => ({ ...state, loading: false }));
		}
	}

	return {
		subscribe,

		load: refresh,

		async create(name: string, parentId?: string | null) {
			const response = await api.createInspirationCollection(name, parentId);
			if (response.success) await refresh();
			return response;
		},

		async rename(id: string, name: string) {
			const response = await api.updateInspirationCollection(id, { name });
			if (response.success) await refresh();
			return response;
		},

		// Reparent a collection (parentId === null => move to root), then refresh.
		async move(id: string, parentId: string | null) {
			const response = await api.updateInspirationCollection(id, { parent_id: parentId });
			if (response.success) await refresh();
			return response;
		},

		async remove(id: string) {
			const response = await api.deleteInspirationCollection(id);
			if (response.success) await refresh();
			return response;
		},

		async addItem(collectionId: string, inspirationId: string) {
			const response = await api.addInspirationToCollection(collectionId, inspirationId);
			if (response.success) await refresh();
			return response;
		},

		async removeItem(collectionId: string, inspirationId: string) {
			const response = await api.removeInspirationFromCollection(collectionId, inspirationId);
			if (response.success) await refresh();
			return response;
		},

		reset() {
			set(initialState);
		}
	};
}

export const inspirationsCollectionsStore = createInspirationsCollectionsStore();
