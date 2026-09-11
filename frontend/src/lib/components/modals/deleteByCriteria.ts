// Pure state -> request shaping for the "Delete by criteria" modal
// (DeleteByCriteriaModal.svelte), kept out of the component so the "does
// anything actually narrow the delete" and "what does the API body look
// like" logic can be tested without mounting Svelte.
//
// Shared between History (tags, age, status, without-media, keep-favorites)
// and Library (tags, age, media type) - each page passes its own
// `DeleteByCriteriaCriteriaConfig` to say which criteria it renders and
// sends. A criterion the config leaves off never appears in `hasCriteria`
// or the built request body, however the state field is set.

export type AgeMode = 'none' | 'older_than_days' | 'date_range';

/** Which criteria a given "Delete by criteria" modal instance renders and
 * sends. `keepFavorites` is a delete modifier, not a narrowing criterion -
 * it never counts towards `hasCriteria` even when enabled. */
export interface DeleteByCriteriaCriteriaConfig {
	tags?: boolean;
	age?: boolean;
	status?: boolean;
	withoutMedia?: boolean;
	keepFavorites?: boolean;
	mediaType?: boolean;
}

export interface DeleteByCriteriaState {
	tagIds: string[];
	ageMode: AgeMode;
	olderThanDays: number | null | undefined;
	createdFrom: string | null | undefined;
	createdTo: string | null | undefined;
	failedOrCancelledOnly: boolean;
	withoutMedia: boolean;
	keepFavorites: boolean;
	mediaType: string | null | undefined;
}

export interface BulkDeleteByCriteriaRequestBody {
	tag_ids?: string[];
	older_than_days?: number;
	created_from?: string;
	created_to?: string;
	without_media?: boolean;
	statuses?: string[];
	keep_favorites?: boolean;
	media_type?: string;
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
		keepFavorites: true,
		mediaType: null
	};
}

function ageHasCriteria(state: DeleteByCriteriaState): boolean {
	return (
		(state.ageMode === 'older_than_days' && state.olderThanDays != null && state.olderThanDays > 0) ||
		(state.ageMode === 'date_range' && (!!state.createdFrom || !!state.createdTo))
	);
}

/** Whether any criterion the config enables actually narrows the candidate
 * set - mirrors the backend's `is_empty()`
 * (`GenerationDeleteCriteria`/`LibraryDeleteCriteria`). */
export function hasCriteria(state: DeleteByCriteriaState, config: DeleteByCriteriaCriteriaConfig): boolean {
	return (
		(!!config.tags && state.tagIds.length > 0) ||
		(!!config.age && ageHasCriteria(state)) ||
		(!!config.status && state.failedOrCancelledOnly) ||
		(!!config.withoutMedia && state.withoutMedia) ||
		(!!config.mediaType && !!state.mediaType)
	);
}

export function buildDeleteByCriteriaRequest(
	state: DeleteByCriteriaState,
	config: DeleteByCriteriaCriteriaConfig
): BulkDeleteByCriteriaRequestBody {
	const body: BulkDeleteByCriteriaRequestBody = {};

	// Delete modifiers: sent whenever the config enables them, regardless of
	// whether the state differs from its default.
	if (config.withoutMedia) body.without_media = state.withoutMedia;
	if (config.keepFavorites) body.keep_favorites = state.keepFavorites;

	if (config.tags && state.tagIds.length > 0) {
		body.tag_ids = state.tagIds;
	}
	if (config.age) {
		if (state.ageMode === 'older_than_days' && state.olderThanDays != null && state.olderThanDays > 0) {
			body.older_than_days = state.olderThanDays;
		}
		if (state.ageMode === 'date_range') {
			if (state.createdFrom) body.created_from = state.createdFrom;
			if (state.createdTo) body.created_to = state.createdTo;
		}
	}
	if (config.status && state.failedOrCancelledOnly) {
		body.statuses = FAILED_OR_CANCELLED_STATUSES;
	}
	if (config.mediaType && state.mediaType) {
		body.media_type = state.mediaType;
	}

	return body;
}
