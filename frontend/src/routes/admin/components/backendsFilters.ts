import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';
import type { Backend } from '$lib/services/admin-api';

export type BackendSortBy = 'name';

export interface BackendsFilters {
	q: string;
	engine: string;
	sortBy: BackendSortBy;
}

export const DEFAULT_BACKENDS_FILTERS: BackendsFilters = {
	q: '',
	engine: '',
	sortBy: 'name'
};

export const BACKENDS_SORT_OPTIONS: readonly SortOption<BackendSortBy>[] = [
	{ value: 'name', label: 'Name A–Z' }
];

const FIELDS: readonly FilterFieldDescriptor<BackendsFilters>[] = [
	{
		kind: 'text',
		key: 'engine',
		param: 'engine',
		label: 'Engine',
		default: '',
		chipLabel: (value) => `engine: ${value}`
	}
];

const codec = createFilterCodec<BackendsFilters>({
	defaults: DEFAULT_BACKENDS_FILTERS,
	fields: FIELDS,
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
	const rows = backends.filter((backend) => {
		if (filters.engine && backend.engine !== filters.engine) return false;
		if (!query) return true;
		return (
			backend.name?.toLowerCase().includes(query) || engineLabel(backend.engine).toLowerCase().includes(query)
		);
	});
	if (filters.sortBy === 'name') {
		return rows.sort((a, b) => a.name.localeCompare(b.name));
	}
	return rows;
}

export function backendEngines(backends: readonly Backend[]): string[] {
	return Array.from(new Set(backends.map((backend) => backend.engine))).sort();
}

export function backendEngineCounts(backends: readonly Backend[]): Record<string, number> {
	const counts: Record<string, number> = {};
	for (const backend of backends) {
		counts[backend.engine] = (counts[backend.engine] ?? 0) + 1;
	}
	return counts;
}
