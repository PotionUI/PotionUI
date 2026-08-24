/**
 * Filter state for the Inspirations page and its translation into the query
 * `GET /api/inspirations` expects. Pure so the page and its store build the
 * same request from the same rules - mirrors libraryQuery.ts.
 */

export interface InspirationsFilters {
	search: string;
	saved: boolean;
	collectionId?: string;
	authorId?: string;
}

export interface InspirationsQuery {
	query?: string;
	saved?: true;
	collection_id?: string;
	author_id?: string;
	limit: number;
	offset: number;
}

export const DEFAULT_INSPIRATIONS_FILTERS: InspirationsFilters = {
	search: '',
	saved: false,
	collectionId: undefined,
	authorId: undefined
};

export function buildInspirationsQuery(
	filters: InspirationsFilters,
	page: number,
	itemsPerPage: number
): InspirationsQuery {
	const safePage = Math.max(1, Math.floor(page) || 1);
	const query: InspirationsQuery = {
		limit: itemsPerPage,
		offset: (safePage - 1) * itemsPerPage
	};

	const search = filters.search.trim();
	if (search) query.query = search;
	if (filters.saved) query.saved = true;
	if (filters.collectionId) query.collection_id = filters.collectionId;
	if (filters.authorId) query.author_id = filters.authorId;

	return query;
}

export function hasActiveInspirationsFilters(filters: InspirationsFilters): boolean {
	return (
		filters.search.trim() !== '' ||
		filters.saved ||
		!!filters.collectionId ||
		!!filters.authorId
	);
}

export function totalInspirationsPages(totalCount: number, itemsPerPage: number): number {
	if (itemsPerPage <= 0) return 0;
	return Math.ceil(Math.max(0, totalCount) / itemsPerPage);
}

export function clampInspirationsPage(
	page: number,
	totalCount: number,
	itemsPerPage: number
): number {
	const pages = totalInspirationsPages(totalCount, itemsPerPage);
	if (pages <= 0) return 1;
	return Math.min(Math.max(1, page), pages);
}
