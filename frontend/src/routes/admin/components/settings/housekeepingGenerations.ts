// Pure form-state helpers for the "Generations" delete-by-criteria block on
// HousekeepingPanel: whether the current selections narrow the candidate set
// at all, and the query params those selections translate to.

import type { GenerationDeleteParams } from '$lib/services/admin-api';

export interface GenerationDeleteFormState {
	olderThanDays: number | null;
	withoutMedia: boolean;
	onlyFailedOrCancelled: boolean;
	keepFavorites: boolean;
}

/** Whether the form narrows the candidate set at all - `keepFavorites` alone
 * never does, since deleting under no other criterion would sweep every
 * non-favorite, non-in-flight generation for every user. */
export function hasGenerationDeleteCriteria(state: GenerationDeleteFormState): boolean {
	return state.olderThanDays !== null || state.withoutMedia || state.onlyFailedOrCancelled;
}

export function buildGenerationDeleteParams(state: GenerationDeleteFormState): GenerationDeleteParams {
	const params: GenerationDeleteParams = {
		without_media: state.withoutMedia,
		keep_favorites: state.keepFavorites
	};
	if (state.olderThanDays !== null) params.older_than_days = state.olderThanDays;
	if (state.onlyFailedOrCancelled) params.statuses = 'failed,cancelled';
	return params;
}
