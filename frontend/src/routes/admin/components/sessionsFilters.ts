import { createFilterCodec } from '$lib/components/library/filterCodec';
import type { SortOption } from '$lib/components/library/librarySection';

export type SessionsSortBy = 'recent';

export interface SessionsFilters {
	q: string;
	sortBy: SessionsSortBy;
}

export const DEFAULT_SESSIONS_FILTERS: SessionsFilters = {
	q: '',
	sortBy: 'recent'
};

export const SESSIONS_SORT_OPTIONS: readonly SortOption<SessionsSortBy>[] = [
	{ value: 'recent', label: 'Most recent' }
];

const codec = createFilterCodec<SessionsFilters>({
	defaults: DEFAULT_SESSIONS_FILTERS,
	fields: [],
	sortValues: ['recent']
});

export function sessionsFiltersFromSearchParams(params: URLSearchParams): SessionsFilters {
	return codec.fromSearchParams(params);
}

export function sessionsFiltersToSearchParams(filters: SessionsFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}
