import type { SavedSegment, SegmentCategory } from '$lib/types/segments';
import { parseServerDate } from '$lib/utils/relativeTime';
import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip } from '$lib/components/library/librarySection';

export type SegmentEnabledFilter = '' | 'on' | 'off';
export type SegmentSortBy = 'name' | 'created';

export interface SegmentFilters {
	q: string;
	category: string;
	enabled: SegmentEnabledFilter;
	tags: string[];
	sortBy: SegmentSortBy;
}

export const DEFAULT_SEGMENT_FILTERS: SegmentFilters = {
	q: '',
	category: '',
	enabled: '',
	tags: [],
	sortBy: 'name'
};

export const SEGMENT_SORT_OPTIONS: ReadonlyArray<{ value: SegmentSortBy; label: string }> = [
	{ value: 'name', label: 'Name' },
	{ value: 'created', label: 'Created' }
];

const FIELDS: readonly FilterFieldDescriptor<SegmentFilters>[] = [
	{ kind: 'text', key: 'category', param: 'category', label: 'Category' },
	{
		kind: 'enum',
		key: 'enabled',
		param: 'enabled',
		label: 'Enabled',
		values: ['on', 'off'],
		default: '',
		chipLabel: (value) => (value === 'on' ? 'enabled' : 'disabled')
	},
	{ kind: 'tags', key: 'tags', param: 'tags', label: 'Tags' }
];

const codec = createFilterCodec<SegmentFilters>({
	defaults: DEFAULT_SEGMENT_FILTERS,
	fields: FIELDS,
	sortValues: ['name', 'created']
});

export function segmentFiltersFromSearchParams(params: URLSearchParams): SegmentFilters {
	return codec.fromSearchParams(params);
}

export function segmentFiltersToSearchParams(filters: SegmentFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}

export function segmentFilterActiveCount(filters: SegmentFilters): number {
	return codec.activeCount(filters);
}

export function segmentFilterChips(filters: SegmentFilters, categories: readonly SegmentCategory[]): FilterChip[] {
	const categoryName = categories.find((category) => category.id === filters.category)?.name ?? filters.category;
	return codec.chips(filters, { category: () => `category = ${categoryName}` });
}

export function clearSegmentFilterChip(filters: SegmentFilters, key: string): SegmentFilters {
	return codec.clearChip(filters, key);
}

export function clearAllSegmentFilters(filters: SegmentFilters): SegmentFilters {
	return codec.clearAll(filters);
}

function segmentTimestamp(segment: SavedSegment): number {
	return parseServerDate(segment.updated_at ?? segment.created_at)?.getTime() ?? 0;
}

export function applySegmentFilters(
	segments: readonly SavedSegment[],
	categories: readonly SegmentCategory[],
	filters: SegmentFilters
): SavedSegment[] {
	const categoryNames = new Map(categories.map((category) => [category.id, category.name.toLowerCase()]));
	const query = filters.q.trim().toLowerCase();
	const wantedTags = filters.tags.map((tag) => tag.toLowerCase());
	const matched = segments.filter((segment) => {
		if (filters.category && segment.category_id !== filters.category) return false;
		if (filters.enabled === 'on' && !segment.enabled) return false;
		if (filters.enabled === 'off' && segment.enabled) return false;
		const segmentTags = (segment.tags ?? []).map((tag) => tag.toLowerCase());
		if (wantedTags.length && !wantedTags.some((tag) => segmentTags.includes(tag))) return false;
		if (!query) return true;
		const haystack = [
			segment.name,
			segment.content,
			segment.description ?? '',
			...(segment.tags ?? []),
			categoryNames.get(segment.category_id) ?? ''
		]
			.join(' ')
			.toLowerCase();
		return haystack.includes(query);
	});
	if (filters.sortBy === 'created') return matched.sort((a, b) => segmentTimestamp(b) - segmentTimestamp(a));
	return matched.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
}

export function segmentTagVocabulary(segments: readonly SavedSegment[]): Array<{ tag: string; count: number }> {
	const counts = new Map<string, number>();
	for (const segment of segments) {
		for (const tag of segment.tags ?? []) counts.set(tag, (counts.get(tag) ?? 0) + 1);
	}
	return Array.from(counts, ([tag, count]) => ({ tag, count })).sort(
		(a, b) => b.count - a.count || a.tag.localeCompare(b.tag)
	);
}

export function segmentCountsByCategory(segments: readonly SavedSegment[]): Map<string, number> {
	const counts = new Map<string, number>();
	for (const segment of segments) counts.set(segment.category_id, (counts.get(segment.category_id) ?? 0) + 1);
	return counts;
}
