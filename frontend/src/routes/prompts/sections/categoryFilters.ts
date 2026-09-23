import type { SegmentCategory } from '$lib/types/segments';
import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip } from '$lib/components/library/librarySection';

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

const FIELDS: readonly FilterFieldDescriptor<CategoryFilters>[] = [
	{
		kind: 'enum',
		key: 'has',
		param: 'has',
		label: 'Segments',
		values: ['segments', 'empty'],
		default: '',
		chipLabel: (value) => (value === 'segments' ? 'with segments' : 'empty')
	}
];

const codec = createFilterCodec<CategoryFilters>({
	defaults: DEFAULT_CATEGORY_FILTERS,
	fields: FIELDS,
	sortValues: ['name', 'count']
});

export function categoryFiltersFromSearchParams(params: URLSearchParams): CategoryFilters {
	return codec.fromSearchParams(params);
}

export function categoryFiltersToSearchParams(filters: CategoryFilters): URLSearchParams {
	return codec.toSearchParams(filters);
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
	return codec.activeCount(filters);
}

export function categoryFilterChips(filters: CategoryFilters): FilterChip[] {
	return codec.chips(filters);
}

export function clearCategoryFilterChip(filters: CategoryFilters, key: string): CategoryFilters {
	return codec.clearChip(filters, key);
}

export function clearAllCategoryFilters(filters: CategoryFilters): CategoryFilters {
	return codec.clearAll(filters);
}
