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
	| 'file_size_asc'
	| 'uses_desc'
	| 'last_used_desc';

export type ModelsQueryMode = 'substring' | 'regex';
export type ModelsUsage = 'any' | 'used' | 'never';

export interface ModelsFilters {
	q: string;
	qMode: ModelsQueryMode;
	tags: string[];
	indexedFrom: string;
	indexedTo: string;
	used: ModelsUsage;
	minUses: string;
	lastUsedFrom: string;
	lastUsedTo: string;
	sortBy: ModelsSortBy;
}

export interface ModelTagRef {
	id: string;
	name: string;
}

export const DEFAULT_MODELS_FILTERS: ModelsFilters = {
	q: '',
	qMode: 'substring',
	tags: [],
	indexedFrom: '',
	indexedTo: '',
	used: 'any',
	minUses: '',
	lastUsedFrom: '',
	lastUsedTo: '',
	sortBy: 'indexed_at_desc'
};

export const MODELS_USAGE_VALUES: readonly ModelsUsage[] = ['any', 'used', 'never'];

const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function dayOrEmpty(value: string): string {
	return DAY_PATTERN.test(value) ? value : '';
}

function positiveIntOrEmpty(value: string): string {
	const parsed = Number.parseInt(value, 10);
	return Number.isFinite(parsed) && parsed >= 1 && String(parsed) === value.trim() ? String(parsed) : '';
}

export const MODELS_SORT_VALUES: readonly ModelsSortBy[] = [
	'indexed_at_desc',
	'indexed_at_asc',
	'modified_at_desc',
	'modified_at_asc',
	'filename_asc',
	'filename_desc',
	'file_size_desc',
	'file_size_asc',
	'uses_desc',
	'last_used_desc'
];

export const MODELS_SORT_OPTIONS: readonly SortOption<ModelsSortBy>[] = [
	{ value: 'indexed_at_desc', label: 'Newest indexed' },
	{ value: 'indexed_at_asc', label: 'Oldest indexed' },
	{ value: 'modified_at_desc', label: 'Recently modified' },
	{ value: 'modified_at_asc', label: 'Least recently modified' },
	{ value: 'filename_asc', label: 'Filename A–Z' },
	{ value: 'filename_desc', label: 'Filename Z–A' },
	{ value: 'file_size_desc', label: 'Largest first' },
	{ value: 'file_size_asc', label: 'Smallest first' },
	{ value: 'uses_desc', label: 'Most used' },
	{ value: 'last_used_desc', label: 'Recently used' }
];

const FIELDS: readonly FilterFieldDescriptor<ModelsFilters>[] = [
	{ kind: 'enum', key: 'qMode', param: 'q_mode', label: 'Search mode', values: ['substring', 'regex'], default: 'substring', filterable: false },
	{ kind: 'tags', key: 'tags', param: 'tags', label: 'Tags' },
	{ kind: 'text', key: 'indexedFrom', param: 'indexed_from', label: 'Indexed from', chipLabel: (value) => `Indexed from ${value}` },
	{ kind: 'text', key: 'indexedTo', param: 'indexed_to', label: 'Indexed to', chipLabel: (value) => `Indexed to ${value}` },
	{
		kind: 'enum',
		key: 'used',
		param: 'used',
		label: 'Usage',
		values: MODELS_USAGE_VALUES,
		default: 'any',
		chipLabel: (value) => (value === 'never' ? 'Never used' : 'Used')
	},
	{ kind: 'text', key: 'minUses', param: 'min_uses', label: 'Minimum uses', chipLabel: (value) => `At least ${value} uses` },
	{ kind: 'text', key: 'lastUsedFrom', param: 'last_used_from', label: 'Last used from', chipLabel: (value) => `Last used from ${value}` },
	{ kind: 'text', key: 'lastUsedTo', param: 'last_used_to', label: 'Last used to', chipLabel: (value) => `Last used to ${value}` }
];

const codec = createFilterCodec<ModelsFilters>({
	defaults: DEFAULT_MODELS_FILTERS,
	fields: FIELDS,
	sortValues: MODELS_SORT_VALUES
});

export function modelsFiltersFromSearchParams(params: URLSearchParams): ModelsFilters {
	const filters = codec.fromSearchParams(params);
	const used = filters.used;
	return {
		...filters,
		indexedFrom: dayOrEmpty(filters.indexedFrom),
		indexedTo: dayOrEmpty(filters.indexedTo),
		minUses: used === 'never' ? '' : positiveIntOrEmpty(filters.minUses),
		lastUsedFrom: used === 'never' ? '' : dayOrEmpty(filters.lastUsedFrom),
		lastUsedTo: used === 'never' ? '' : dayOrEmpty(filters.lastUsedTo)
	};
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
	return !!filters.q.trim() || codec.activeCount(filters) > 0;
}

export function withModelsUsage(filters: ModelsFilters, used: ModelsUsage): ModelsFilters {
	if (used === 'used') return { ...filters, used };
	return { ...filters, used, minUses: '', ...(used === 'never' ? { lastUsedFrom: '', lastUsedTo: '' } : {}) };
}

export type ModelsSortField = 'indexed_at' | 'modified_at' | 'filename' | 'file_size' | 'uses' | 'last_used';

export function modelsSortParams(sortBy: ModelsSortBy): {
	sort_by: ModelsSortField;
	sort_order: 'asc' | 'desc';
} {
	const separator = sortBy.lastIndexOf('_');
	return {
		sort_by: sortBy.slice(0, separator) as ModelsSortField,
		sort_order: sortBy.slice(separator + 1) as 'asc' | 'desc'
	};
}

export interface ModelsQueryParams {
	search?: string;
	q_mode?: 'regex';
	indexed_from?: string;
	indexed_to?: string;
	used?: 'used' | 'never';
	min_uses?: number;
	last_used_from?: string;
	last_used_to?: string;
}

export function modelsQueryParams(filters: ModelsFilters): ModelsQueryParams {
	const params: ModelsQueryParams = {};
	if (filters.q) {
		params.search = filters.q;
		if (filters.qMode === 'regex') params.q_mode = 'regex';
	}
	if (filters.indexedFrom) params.indexed_from = filters.indexedFrom;
	if (filters.indexedTo) params.indexed_to = filters.indexedTo;
	if (filters.used !== 'any') params.used = filters.used;
	if (filters.used !== 'never') {
		if (filters.minUses) params.min_uses = Number(filters.minUses);
		if (filters.lastUsedFrom) params.last_used_from = filters.lastUsedFrom;
		if (filters.lastUsedTo) params.last_used_to = filters.lastUsedTo;
	}
	return params;
}
