/**
 * Filter state for the Library page and its translation into the query the
 * `/api/library/items` route expects (`src/features/library/routes.py`).
 *
 * Pure so the page, the store and the media-loader's Library picker all build
 * the same request from the same rules.
 */

export type LibraryMediaTypeFilter = 'all' | 'image' | 'video' | 'audio';

export interface LibraryFilters {
	mediaType: LibraryMediaTypeFilter;
	selectedTagIds: string[];
	collectionId?: string;
	search: string;
}

/** Snake-case query params, ready to hand to axios as `params`. */
export interface LibraryQuery {
	media_type?: string;
	tag_ids?: string;
	collection_id?: string;
	search?: string;
	limit: number;
	offset: number;
}

export const DEFAULT_LIBRARY_FILTERS: LibraryFilters = {
	mediaType: 'all',
	selectedTagIds: [],
	collectionId: undefined,
	search: ''
};

/**
 * The route reads several tags as one comma-separated string and treats an
 * absent param as "no filter", so every inactive filter has to be omitted
 * rather than sent empty - `search=''` would otherwise be a substring match
 * against the empty string on the server's side of the contract.
 */
export function buildLibraryQuery(
	filters: LibraryFilters,
	page: number,
	itemsPerPage: number
): LibraryQuery {
	const safePage = Math.max(1, Math.floor(page) || 1);
	const query: LibraryQuery = {
		limit: itemsPerPage,
		offset: (safePage - 1) * itemsPerPage
	};

	if (filters.mediaType !== 'all') query.media_type = filters.mediaType;

	const tagIds = filters.selectedTagIds.filter((id) => !!id);
	if (tagIds.length > 0) query.tag_ids = tagIds.join(',');

	if (filters.collectionId) query.collection_id = filters.collectionId;

	const search = filters.search.trim();
	if (search) query.search = search;

	return query;
}

/** Whether anything is narrowing the view - drives the empty state's wording. */
export function hasActiveLibraryFilters(filters: LibraryFilters): boolean {
	return (
		filters.mediaType !== 'all' ||
		filters.selectedTagIds.length > 0 ||
		!!filters.collectionId ||
		filters.search.trim() !== ''
	);
}

export function totalLibraryPages(totalCount: number, itemsPerPage: number): number {
	if (itemsPerPage <= 0) return 0;
	return Math.ceil(Math.max(0, totalCount) / itemsPerPage);
}

/**
 * Where to land after items were removed. Deleting the last item on the last
 * page leaves the current page past the end of the result set; without this the
 * grid would reload into an empty page that the user never navigated to.
 */
export function clampLibraryPage(page: number, totalCount: number, itemsPerPage: number): number {
	const pages = totalLibraryPages(totalCount, itemsPerPage);
	if (pages <= 0) return 1;
	return Math.min(Math.max(1, page), pages);
}
