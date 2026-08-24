import { writable } from 'svelte/store';
import { logger, getErrorMessage } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import type { ModelCollection } from '$lib/types/models';

interface ModelLibraryState {
	collections: ModelCollection[];
	loading: boolean;
}

const initialState: ModelLibraryState = {
	collections: [],
	loading: false
};

function createModelLibraryStore() {
	const { subscribe, update, set } = writable<ModelLibraryState>(initialState);
	let loaded = false;
	let inFlight: Promise<void> | null = null;
	let forcedAfterFlight: Promise<void> | null = null;

	// Named refresh so the mutations below never depend on `this` binding.
	async function fetchCollections() {
		update((state) => ({ ...state, loading: true }));
		try {
			const response = await api.listModelCollections();
			if (response.success && response.data) {
				const data = response.data;
				update((state) => ({
					...state,
					collections: data.collections,
					loading: false
				}));
				loaded = true;
			} else {
				update((state) => ({ ...state, loading: false }));
			}
		} catch (error) {
			logger.error('Failed to load model collections:', getErrorMessage(error));
			update((state) => ({ ...state, loading: false }));
		}
	}

	function refresh(force = false): Promise<void> {
		if (!force && loaded) return Promise.resolve();
		if (inFlight) {
			if (!force) return inFlight;
			if (!forcedAfterFlight) {
				forcedAfterFlight = inFlight
					.then(() => refresh(true))
					.finally(() => (forcedAfterFlight = null));
			}
			return forcedAfterFlight;
		}

		inFlight = fetchCollections().finally(() => {
			inFlight = null;
		});
		return inFlight;
	}

	return {
		subscribe,

		// Load all model collections for the current user
		load: refresh,

		// Create a new collection (optionally nested under parentId).
		// The created row is inserted optimistically so it appears immediately,
		// then a refresh reconciles ordering/counts.
		async create(name: string, parentId?: string | null) {
			const response = await api.createModelCollection(name, parentId);
			if (response.success) {
				const created = response.data?.collection;
				if (created) {
					update((state) =>
						state.collections.some((c) => c.id === created.id)
							? state
							: { ...state, collections: [...state.collections, created] }
					);
				}
				await refresh(true);
			}
			return response;
		},

		// Reparent a collection (parentId === null => move to root), then refresh.
		// On failure (e.g. a cycle-forming move) the API message is logged and the
		// response is returned so callers can surface it to the user.
		async move(id: string, parentId: string | null) {
			try {
				const response = await api.moveModelCollection(id, parentId);
				if (response.success) {
					await refresh(true);
				} else {
					logger.error(
						'Move model collection failed:',
						response.error ?? response.message ?? 'unknown'
					);
				}
				return response;
			} catch (error) {
				logger.error('Move model collection failed:', getErrorMessage(error));
				throw error;
			}
		},

		// Reparent several collections at once, then refresh. Per-id failures
		// (a cycle, an ownership mismatch) don't block the rest of the batch -
		// the caller inspects the returned moved/failed/errors to report them.
		async bulkMove(ids: string[], parentId: string | null) {
			const response = await api.bulkMoveModelCollections(ids, parentId);
			if (response.success) {
				await refresh(true);
			}
			return {
				success: response.success,
				error: response.error,
				message: response.message,
				moved: response.data?.moved ?? 0,
				failed: response.data?.failed ?? ids.length,
				errors: response.data?.errors ?? []
			};
		},

		// Rename a collection, then refresh the list
		async rename(id: string, name: string) {
			const response = await api.renameModelCollection(id, name);
			if (response.success) {
				await refresh(true);
			}
			return response;
		},

		// Delete a collection, then refresh the list
		async remove(id: string) {
			const response = await api.deleteModelCollection(id);
			if (response.success) {
				await refresh(true);
			}
			return response;
		},

		// Add models to a collection (updates the item_count on reload)
		async addMembers(id: string, modelIds: string[]) {
			const response = await api.addToModelCollection(id, modelIds);
			if (response.success) {
				await refresh(true);
			}
			return response;
		},

		// Remove models from a collection (updates the item_count on reload)
		async removeMembers(id: string, modelIds: string[]) {
			const response = await api.removeFromModelCollection(id, modelIds);
			if (response.success) {
				await refresh(true);
			}
			return response;
		},

		reset() {
			set(initialState);
			loaded = false;
			inFlight = null;
			forcedAfterFlight = null;
		}
	};
}

export const modelLibraryStore = createModelLibraryStore();
