import { writable } from 'svelte/store';
import { logger, getErrorMessage } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import type { Collection, CollectionScope } from '$lib/types/history';

interface CollectionsState {
	collections: Collection[];
	loading: boolean;
}

const initialState: CollectionsState = {
	collections: [],
	loading: false
};

// A collection tree is scoped (History vs Library, backend migration 137),
// so there is one store instance per scope rather than one shared store -
// see historyCollectionsStore / libraryCollectionsStore below. The scope is
// captured once here and threaded into every API call, so callers never pass
// it themselves and can't cross the streams.
function createCollectionsStore(scope: CollectionScope) {
	const { subscribe, update, set } = writable<CollectionsState>(initialState);

	// Overlapping refresh()es race (create/rename/move/... all trigger one, and
	// a plain load() can be in flight too), so every call is stamped with a
	// monotonic sequence number. A response is applied only if it is still
	// the most recently issued request when it settles - a response cannot be
	// superseded only by one that has already applied; a newer request that
	// is merely still in flight also permanently retires every older one, so
	// an optimistic mutation isn't wiped by a stale list response that
	// happens to resolve before the mutation's own refresh does.
	// reset() bumps the lifetime token, retiring every in-flight refresh at
	// once so a pending response can never repopulate a reset store.
	let requestSeq = 0;
	let lifetime = 0;

	// Named refresh so the mutations below never depend on `this` binding.
	async function refresh() {
		const seq = ++requestSeq;
		const requestLifetime = lifetime;
		update((state) => ({ ...state, loading: true }));
		try {
			const response = await api.listCollections(scope);
			if (requestLifetime !== lifetime || seq !== requestSeq) return;
			if (response.success && response.data) {
				const data = response.data;
				update((state) => ({
					...state,
					collections: data.collections,
					loading: false
				}));
			} else {
				update((state) => ({ ...state, loading: false }));
			}
		} catch (error) {
			logger.error('Failed to load collections:', getErrorMessage(error));
			if (requestLifetime !== lifetime || seq !== requestSeq) return;
			update((state) => ({ ...state, loading: false }));
		}
	}

	return {
		subscribe,

		// Load all collections for the current user, within this store's scope
		load: refresh,

		// Create a new collection (optionally nested under parentId).
		// The created row is inserted optimistically so it appears immediately,
		// then a refresh reconciles ordering/counts.
		async create(name: string, parentId?: string | null) {
			const response = await api.createCollection(name, scope, parentId);
			if (response.success) {
				const created = response.data?.collection;
				if (created) {
					update((state) =>
						state.collections.some((c) => c.id === created.id)
							? state
							: { ...state, collections: [...state.collections, created] }
					);
				}
				await refresh();
			}
			return response;
		},

		// Reparent a collection (parentId === null => move to root), then refresh.
		// On failure (e.g. a cycle-forming move) the API message is logged and the
		// response is returned so callers can surface it to the user.
		async move(id: string, parentId: string | null) {
			try {
				const response = await api.moveCollection(id, parentId, scope);
				if (response.success) {
					await refresh();
				} else {
					logger.error('Move collection failed:', response.error ?? response.message ?? 'unknown');
				}
				return response;
			} catch (error) {
				logger.error('Move collection failed:', getErrorMessage(error));
				throw error;
			}
		},

		// Reparent several collections at once, then refresh. Per-id failures
		// (a cycle, an ownership mismatch) don't block the rest of the batch -
		// the caller inspects the returned moved/failed/errors to report them.
		async bulkMove(ids: string[], parentId: string | null) {
			const response = await api.bulkMoveCollections(ids, parentId, scope);
			if (response.success) {
				await refresh();
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
			const response = await api.renameCollection(id, name, scope);
			if (response.success) {
				await refresh();
			}
			return response;
		},

		// Delete a collection, then refresh the list
		async remove(id: string) {
			const response = await api.deleteCollection(id, scope);
			if (response.success) {
				await refresh();
			}
			return response;
		},

		// Add generations to a collection (updates the item_count on reload)
		async addMembers(id: string, generationIds: string[]) {
			const response = await api.addToCollection(id, generationIds, scope);
			if (response.success) {
				await refresh();
			}
			return response;
		},

		reset() {
			lifetime++;
			set(initialState);
		}
	};
}

export const historyCollectionsStore = createCollectionsStore('history');
export const libraryCollectionsStore = createCollectionsStore('library');
export const promptsCollectionsStore = createCollectionsStore('prompts');
