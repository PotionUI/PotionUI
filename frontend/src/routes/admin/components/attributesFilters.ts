import type { AttributeDefinition } from '$lib/types/models';
import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';

export type AttributesSortBy = 'key' | 'label';

export interface AttributesFilters {
	q: string;
	sortBy: AttributesSortBy;
}

export const DEFAULT_ATTRIBUTES_FILTERS: AttributesFilters = {
	q: '',
	sortBy: 'key'
};

export const ATTRIBUTES_SORT_OPTIONS: readonly SortOption<AttributesSortBy>[] = [
	{ value: 'key', label: 'Key A–Z' },
	{ value: 'label', label: 'Label A–Z' }
];

const FIELDS: readonly FilterFieldDescriptor<AttributesFilters>[] = [];

const codec = createFilterCodec<AttributesFilters>({
	defaults: DEFAULT_ATTRIBUTES_FILTERS,
	fields: FIELDS,
	sortValues: ['key', 'label']
});

export function attributesFiltersFromSearchParams(params: URLSearchParams): AttributesFilters {
	return codec.fromSearchParams(params);
}

export function attributesFiltersToSearchParams(filters: AttributesFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}

export function attributesFilterChips(filters: AttributesFilters): FilterChip[] {
	return codec.chips(filters);
}

export function clearAttributesFilterChip(filters: AttributesFilters, key: string): AttributesFilters {
	return codec.clearChip(filters, key);
}

export function clearAllAttributesFilters(filters: AttributesFilters): AttributesFilters {
	return codec.clearAll(filters);
}

export function attributesFilterActiveCount(filters: AttributesFilters): number {
	return codec.activeCount(filters);
}

export function applyAttributesFilters(
	definitions: readonly AttributeDefinition[],
	filters: AttributesFilters
): AttributeDefinition[] {
	const query = filters.q.trim().toLowerCase();
	const rows = query
		? definitions.filter((d) => d.key.toLowerCase().includes(query) || d.label.toLowerCase().includes(query))
		: [...definitions];
	if (filters.sortBy === 'label') return rows.sort((a, b) => a.label.localeCompare(b.label));
	return rows.sort((a, b) => a.key.localeCompare(b.key));
}
