// Pure state -> request shaping for the "Delete by criteria" modal, kept out
// of the component so the "does anything actually narrow the delete" and
// "what does the API body look like" logic can be tested without mounting
// Svelte.

export type AgeMode = 'none' | 'older_than_days' | 'date_range';

export interface DeleteByCriteriaState {
	tagIds: string[];
	ageMode: AgeMode;
	olderThanDays: number | null | undefined;
	createdFrom: string | null | undefined;
	createdTo: string | null | undefined;
	failedOrCancelledOnly: boolean;
	withoutMedia: boolean;
	keepFavorites: boolean;
}

export interface BulkDeleteByCriteriaRequestBody {
	tag_ids?: string[];
	older_than_days?: number;
	created_from?: string;
	created_to?: string;
	without_media: boolean;
	statuses?: string[];
	keep_favorites: boolean;
}

export const FAILED_OR_CANCELLED_STATUSES = ['failed', 'cancelled'];

export function initialDeleteByCriteriaState(): DeleteByCriteriaState {
	return {
		tagIds: [],
		ageMode: 'none',
		olderThanDays: null,
		createdFrom: null,
		createdTo: null,
		failedOrCancelledOnly: false,
		withoutMedia: false,
		keepFavorites: true
	};
}

/** Whether any criterion actually narrows the candidate set - mirrors the
 * backend's `GenerationDeleteCriteria.is_empty()`. */
export function hasCriteria(state: DeleteByCriteriaState): boolean {
	return (
		state.tagIds.length > 0 ||
		(state.ageMode === 'older_than_days' && state.olderThanDays != null && state.olderThanDays > 0) ||
		(state.ageMode === 'date_range' && (!!state.createdFrom || !!state.createdTo)) ||
		state.failedOrCancelledOnly ||
		state.withoutMedia
	);
}

export function buildDeleteByCriteriaRequest(state: DeleteByCriteriaState): BulkDeleteByCriteriaRequestBody {
	const body: BulkDeleteByCriteriaRequestBody = {
		without_media: state.withoutMedia,
		keep_favorites: state.keepFavorites
	};

	if (state.tagIds.length > 0) {
		body.tag_ids = state.tagIds;
	}
	if (state.ageMode === 'older_than_days' && state.olderThanDays != null && state.olderThanDays > 0) {
		body.older_than_days = state.olderThanDays;
	}
	if (state.ageMode === 'date_range') {
		if (state.createdFrom) body.created_from = state.createdFrom;
		if (state.createdTo) body.created_to = state.createdTo;
	}
	if (state.failedOrCancelledOnly) {
		body.statuses = FAILED_OR_CANCELLED_STATUSES;
	}

	return body;
}
