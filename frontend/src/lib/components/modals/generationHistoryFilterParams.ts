export interface GenerationHistoryFilterInput {
	tagIds: string[];
	collectionId: string | null;
}

export interface GenerationHistoryFilterParams {
	tagIds?: string[];
	collectionId?: string;
}

/**
 * `GenerationHistoryModal`'s tag + collection filters combine into one params
 * object (the backend ANDs `tag_ids` and `collection_id` together - see
 * `history_query.py`/`repository.py`). "All collections" is `null`, which
 * must omit `collectionId` entirely rather than sending an empty string.
 */
export function buildHistoryFilterParams({
	tagIds,
	collectionId
}: GenerationHistoryFilterInput): GenerationHistoryFilterParams {
	const params: GenerationHistoryFilterParams = {};
	if (tagIds.length > 0) params.tagIds = tagIds;
	if (collectionId) params.collectionId = collectionId;
	return params;
}
