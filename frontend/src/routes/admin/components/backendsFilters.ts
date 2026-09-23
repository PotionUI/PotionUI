import { createFilterCodec } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';
import type { Backend } from '$lib/services/admin-api';

export type BackendSortBy = 'name';

export interface BackendsFilters {
	q: string;
	sortBy: BackendSortBy;
}

export const DEFAULT_BACKENDS_FILTERS: BackendsFilters = {
	q: '',
	sortBy: 'name'
};

export const BACKENDS_SORT_OPTIONS: readonly SortOption<BackendSortBy>[] = [
	{ value: 'name', label: 'Name A–Z' }
];

const codec = createFilterCodec<BackendsFilters>({
	defaults: DEFAULT_BACKENDS_FILTERS,
	fields: [],
	sortValues: ['name']
});

export function backendsFilterChips(filters: BackendsFilters): FilterChip[] {
	return codec.chips(filters);
}

export function clearBackendsFilterChip(filters: BackendsFilters, key: string): BackendsFilters {
	return codec.clearChip(filters, key);
}

export function clearAllBackendsFilters(filters: BackendsFilters): BackendsFilters {
	return codec.clearAll(filters);
}

export function backendsFilterActiveCount(filters: BackendsFilters): number {
	return codec.activeCount(filters);
}

export function applyBackendsFilters(
	backends: readonly Backend[],
	filters: BackendsFilters,
	engineLabel: (engine: string) => string
): Backend[] {
	const query = filters.q.trim().toLowerCase();
	const rows = query
		? backends.filter(
				(backend) =>
					backend.name?.toLowerCase().includes(query) ||
					engineLabel(backend.engine).toLowerCase().includes(query)
			)
		: backends.slice();
	if (filters.sortBy === 'name') {
		return rows.sort((a, b) => a.name.localeCompare(b.name));
	}
	return rows;
}
