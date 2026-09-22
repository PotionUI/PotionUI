import type { SegmentCategory } from '$lib/types/segments';
import { oneOf, type FilterChip } from '../library/librarySection';

export type CategoryHasFilter = '' | 'segments' | 'empty';
export type CategorySortBy = 'name' | 'count';

export interface CategoryFilters {
	q: string;
	has: CategoryHasFilter;
	sortBy: CategorySortBy;
}

export const DEFAULT_CATEGORY_FILTERS: CategoryFilters = {
	q: '',
	has: '',
	sortBy: 'name'
};

export const CATEGORY_SORT_OPTIONS: ReadonlyArray<{ value: CategorySortBy; label: string }> = [
	{ value: 'name', label: 'Name' },
	{ value: 'count', label: 'Segment count' }
];

const HAS_VALUES: Exclude<CategoryHasFilter, ''>[] = ['segments', 'empty'];
const SORT_VALUES: CategorySortBy[] = ['name', 'count'];

export function categoryFiltersFromSearchParams(params: URLSearchParams): CategoryFilters {
	return {
		q: params.get('q') ?? DEFAULT_CATEGORY_FILTERS.q,
		has: oneOf(params.get('has'), HAS_VALUES, DEFAULT_CATEGORY_FILTERS.has),
		sortBy: oneOf(params.get('sort_by'), SORT_VALUES, DEFAULT_CATEGORY_FILTERS.sortBy)
	};
}

export function categoryFiltersToSearchParams(filters: CategoryFilters): URLSearchParams {
	const params = new URLSearchParams();
	if (filters.q) params.set('q', filters.q);
	if (filters.has) params.set('has', filters.has);
	if (filters.sortBy !== DEFAULT_CATEGORY_FILTERS.sortBy) params.set('sort_by', filters.sortBy);
	return params;
}

function byName(a: SegmentCategory, b: SegmentCategory): number {
	return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
}

export function applyCategoryFilters(
	categories: readonly SegmentCategory[],
	counts: ReadonlyMap<string, number>,
	filters: CategoryFilters
): SegmentCategory[] {
	const query = filters.q.trim().toLowerCase();
	const matched = categories.filter((category) => {
		const count = counts.get(category.id) ?? 0;
		if (filters.has === 'segments' && count === 0) return false;
		if (filters.has === 'empty' && count > 0) return false;
		if (!query) return true;
		return [category.name, category.description ?? ''].join(' ').toLowerCase().includes(query);
	});
	if (filters.sortBy === 'count')
		return matched.sort((a, b) => (counts.get(b.id) ?? 0) - (counts.get(a.id) ?? 0) || byName(a, b));
	return matched.sort(byName);
}

export function categoryFilterActiveCount(filters: CategoryFilters): number {
	return filters.has ? 1 : 0;
}

export function categoryFilterChips(filters: CategoryFilters): FilterChip[] {
	const chips: FilterChip[] = [];
	if (filters.has) chips.push({ key: 'has', label: filters.has === 'segments' ? 'with segments' : 'empty' });
	return chips;
}

export function clearCategoryFilterChip(filters: CategoryFilters, key: string): CategoryFilters {
	if (key === 'has') return { ...filters, has: '' };
	return filters;
}

export function clearAllCategoryFilters(filters: CategoryFilters): CategoryFilters {
	return { ...DEFAULT_CATEGORY_FILTERS, q: filters.q, sortBy: filters.sortBy };
}
