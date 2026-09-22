import type { SavedSegment, SegmentCategory } from '$lib/types/segments';
import { parseServerDate } from '$lib/utils/relativeTime';
import { oneOf, type FilterChip } from '../library/librarySection';

export type SegmentTypeFilter = '' | 'content' | 'break';
export type SegmentEnabledFilter = '' | 'on' | 'off';
export type SegmentSortBy = 'name' | 'created';

export interface SegmentFilters {
	q: string;
	category: string;
	type: SegmentTypeFilter;
	enabled: SegmentEnabledFilter;
	tags: string[];
	sortBy: SegmentSortBy;
}

export const DEFAULT_SEGMENT_FILTERS: SegmentFilters = {
	q: '',
	category: '',
	type: '',
	enabled: '',
	tags: [],
	sortBy: 'name'
};

export const SEGMENT_SORT_OPTIONS: ReadonlyArray<{ value: SegmentSortBy; label: string }> = [
	{ value: 'name', label: 'Name' },
	{ value: 'created', label: 'Created' }
];

const TYPE_VALUES: Exclude<SegmentTypeFilter, ''>[] = ['content', 'break'];
const ENABLED_VALUES: Exclude<SegmentEnabledFilter, ''>[] = ['on', 'off'];
const SORT_VALUES: SegmentSortBy[] = ['name', 'created'];

function splitTags(value: string | null): string[] {
	return (value ?? '')
		.split(',')
		.map((tag) => tag.trim())
		.filter(Boolean);
}

export function segmentFiltersFromSearchParams(params: URLSearchParams): SegmentFilters {
	return {
		q: params.get('q') ?? DEFAULT_SEGMENT_FILTERS.q,
		category: params.get('category') ?? DEFAULT_SEGMENT_FILTERS.category,
		type: oneOf(params.get('type'), TYPE_VALUES, DEFAULT_SEGMENT_FILTERS.type),
		enabled: oneOf(params.get('enabled'), ENABLED_VALUES, DEFAULT_SEGMENT_FILTERS.enabled),
		tags: splitTags(params.get('tags')),
		sortBy: oneOf(params.get('sort_by'), SORT_VALUES, DEFAULT_SEGMENT_FILTERS.sortBy)
	};
}

export function segmentFiltersToSearchParams(filters: SegmentFilters): URLSearchParams {
	const params = new URLSearchParams();
	if (filters.q) params.set('q', filters.q);
	if (filters.category) params.set('category', filters.category);
	if (filters.type) params.set('type', filters.type);
	if (filters.enabled) params.set('enabled', filters.enabled);
	if (filters.tags.length) params.set('tags', filters.tags.join(','));
	if (filters.sortBy !== DEFAULT_SEGMENT_FILTERS.sortBy) params.set('sort_by', filters.sortBy);
	return params;
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
		if (filters.type && segment.type !== filters.type) return false;
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

export function segmentFilterActiveCount(filters: SegmentFilters): number {
	let count = 0;
	if (filters.category) count++;
	if (filters.type) count++;
	if (filters.enabled) count++;
	if (filters.tags.length) count++;
	return count;
}

export function segmentFilterChips(filters: SegmentFilters, categories: readonly SegmentCategory[]): FilterChip[] {
	const chips: FilterChip[] = [];
	if (filters.category) {
		const name = categories.find((category) => category.id === filters.category)?.name ?? filters.category;
		chips.push({ key: 'category', label: `category = ${name}` });
	}
	if (filters.type) chips.push({ key: 'type', label: filters.type });
	if (filters.enabled) chips.push({ key: 'enabled', label: filters.enabled === 'on' ? 'enabled' : 'disabled' });
	for (const tag of filters.tags) chips.push({ key: `tag:${tag}`, label: `#${tag}` });
	return chips;
}

export function clearSegmentFilterChip(filters: SegmentFilters, key: string): SegmentFilters {
	if (key === 'category') return { ...filters, category: '' };
	if (key === 'type') return { ...filters, type: '' };
	if (key === 'enabled') return { ...filters, enabled: '' };
	if (key.startsWith('tag:')) {
		const tag = key.slice('tag:'.length);
		return { ...filters, tags: filters.tags.filter((entry) => entry !== tag) };
	}
	return filters;
}

export function clearAllSegmentFilters(filters: SegmentFilters): SegmentFilters {
	return { ...DEFAULT_SEGMENT_FILTERS, q: filters.q, sortBy: filters.sortBy };
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
