import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';

export type ModelsSortBy =
	| 'indexed_at_desc'
	| 'indexed_at_asc'
	| 'modified_at_desc'
	| 'modified_at_asc'
	| 'filename_asc'
	| 'filename_desc'
	| 'file_size_desc'
	| 'file_size_asc';

export interface ModelsFilters {
	q: string;
	type: string;
	tags: string[];
	sortBy: ModelsSortBy;
}

export interface ModelTagRef {
	id: string;
	name: string;
}

export const DEFAULT_MODELS_FILTERS: ModelsFilters = {
	q: '',
	type: 'all',
	tags: [],
	sortBy: 'indexed_at_desc'
};

export const MODELS_SORT_VALUES: readonly ModelsSortBy[] = [
	'indexed_at_desc',
	'indexed_at_asc',
	'modified_at_desc',
	'modified_at_asc',
	'filename_asc',
	'filename_desc',
	'file_size_desc',
	'file_size_asc'
];

export const MODELS_SORT_OPTIONS: readonly SortOption<ModelsSortBy>[] = [
	{ value: 'indexed_at_desc', label: 'Newest indexed' },
	{ value: 'indexed_at_asc', label: 'Oldest indexed' },
	{ value: 'modified_at_desc', label: 'Recently modified' },
	{ value: 'modified_at_asc', label: 'Least recently modified' },
	{ value: 'filename_asc', label: 'Filename A–Z' },
	{ value: 'filename_desc', label: 'Filename Z–A' },
	{ value: 'file_size_desc', label: 'Largest first' },
	{ value: 'file_size_asc', label: 'Smallest first' }
];

const FIELDS: readonly FilterFieldDescriptor<ModelsFilters>[] = [
	{
		kind: 'text',
		key: 'type',
		param: 'type',
		label: 'Type',
		default: 'all',
		chipLabel: (value) => value.toUpperCase()
	},
	{ kind: 'tags', key: 'tags', param: 'tags', label: 'Tags' }
];

const codec = createFilterCodec<ModelsFilters>({
	defaults: DEFAULT_MODELS_FILTERS,
	fields: FIELDS,
	sortValues: MODELS_SORT_VALUES
});

export function modelsFiltersFromSearchParams(params: URLSearchParams): ModelsFilters {
	return codec.fromSearchParams(params);
}

export function modelsFiltersToSearchParams(filters: ModelsFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}

export function modelsFilterChips(filters: ModelsFilters, tags: readonly ModelTagRef[]): FilterChip[] {
	return codec.chips(filters, { tags: (tagId: string) => tags.find((tag) => tag.id === tagId)?.name ?? tagId });
}

export function clearModelsFilterChip(filters: ModelsFilters, key: string): ModelsFilters {
	return codec.clearChip(filters, key);
}

export function clearAllModelsFilters(filters: ModelsFilters): ModelsFilters {
	return codec.clearAll(filters);
}

export function modelsFilterActiveCount(filters: ModelsFilters): number {
	return codec.activeCount(filters);
}

export function modelsHasActiveFilters(filters: ModelsFilters): boolean {
	return !!filters.q.trim() || filters.type !== DEFAULT_MODELS_FILTERS.type || filters.tags.length > 0;
}

export function modelsSortParams(sortBy: ModelsSortBy): {
	sort_by: 'indexed_at' | 'modified_at' | 'filename' | 'file_size';
	sort_order: 'asc' | 'desc';
} {
	const separator = sortBy.lastIndexOf('_');
	return {
		sort_by: sortBy.slice(0, separator) as 'indexed_at' | 'modified_at' | 'filename' | 'file_size',
		sort_order: sortBy.slice(separator + 1) as 'asc' | 'desc'
	};
}
