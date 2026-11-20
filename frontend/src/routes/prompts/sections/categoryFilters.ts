import type { SegmentCategory } from '$lib/types/segments';
import { parseServerDate } from '$lib/utils/relativeTime';
import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';

export type CategoryHasFilter = '' | 'segments' | 'empty';
export type CategorySortBy = 'name' | 'count' | 'created' | 'updated';

export interface CategoryFilters {
	q: string;
	has: CategoryHasFilter;
	sortBy: CategorySortBy;
	sortDir?: 'asc' | 'desc';
}

export const DEFAULT_CATEGORY_FILTERS: CategoryFilters = {
	q: '',
	has: '',
	sortBy: 'name',
	sortDir: 'asc'
};

export const CATEGORY_SORT_OPTIONS: ReadonlyArray<{ value: CategorySortBy; label: string }> = [
	{ value: 'name', label: 'Name' },
	{ value: 'count', label: 'Segment count' },
	{ value: 'created', label: 'Created' },
	{ value: 'updated', label: 'Updated' }
];

export const CATEGORY_SORT_DEFAULT_DIR = { name: 'asc', count: 'desc', created: 'desc', updated: 'desc' } as const;

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

export const categoryFilters = createFilterCodec<CategoryFilters>({
	defaults: DEFAULT_CATEGORY_FILTERS,
	fields: FIELDS,
	sortValues: ['name', 'count', 'created', 'updated'],
	sortDefaultDir: CATEGORY_SORT_DEFAULT_DIR
});

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
	const sign = (filters.sortDir ?? CATEGORY_SORT_DEFAULT_DIR[filters.sortBy]) === 'desc' ? -1 : 1;
	if (filters.sortBy === 'count')
		return matched.sort((a, b) => sign * ((counts.get(a.id) ?? 0) - (counts.get(b.id) ?? 0)) || byName(a, b));
	if (filters.sortBy === 'created' || filters.sortBy === 'updated') {
		const field = filters.sortBy === 'created' ? 'created_at' : 'updated_at';
		const stamp = (category: SegmentCategory) => parseServerDate(category[field] ?? category.created_at)?.getTime() ?? 0;
		return matched.sort((a, b) => sign * (stamp(a) - stamp(b)) || byName(a, b));
	}
	return matched.sort((a, b) => sign * byName(a, b));
}
