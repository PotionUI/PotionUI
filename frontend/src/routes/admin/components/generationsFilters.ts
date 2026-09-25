import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';
import { FAILURE_ALERT_CATEGORIES } from './settings/failureAlerts';

export type GenerationStatusFilter = '' | 'completed' | 'running' | 'pending' | 'failed' | 'cancelled';
export type GenerationSortBy = 'created_desc' | 'created_asc';

export interface GenerationsFilters {
	q: string;
	status: GenerationStatusFilter;
	category: string;
	userId: string;
	createdFrom: string;
	createdTo: string;
	sortBy: GenerationSortBy;
}

export const DEFAULT_GENERATIONS_FILTERS: GenerationsFilters = {
	q: '',
	status: '',
	category: '',
	userId: '',
	createdFrom: '',
	createdTo: '',
	sortBy: 'created_desc'
};

export const GENERATION_STATUS_OPTIONS: ReadonlyArray<{ value: GenerationStatusFilter; label: string }> = [
	{ value: '', label: 'All' },
	{ value: 'completed', label: 'Completed' },
	{ value: 'running', label: 'Running' },
	{ value: 'pending', label: 'Pending' },
	{ value: 'failed', label: 'Failed' },
	{ value: 'cancelled', label: 'Cancelled' }
];

export const GENERATION_SORT_OPTIONS: readonly SortOption<GenerationSortBy>[] = [
	{ value: 'created_desc', label: 'Newest first' },
	{ value: 'created_asc', label: 'Oldest first' }
];

export const GENERATION_CATEGORY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
	{ value: '', label: 'All' },
	...FAILURE_ALERT_CATEGORIES
];

const STATUS_CHIP_LABELS: Record<Exclude<GenerationStatusFilter, ''>, string> = {
	completed: 'Completed',
	running: 'Running',
	pending: 'Pending',
	failed: 'Failed',
	cancelled: 'Cancelled'
};

const CATEGORY_CHIP_LABELS: Record<string, string> = Object.fromEntries(
	FAILURE_ALERT_CATEGORIES.map((category) => [category.value, category.label])
);

const FIELDS: readonly FilterFieldDescriptor<GenerationsFilters>[] = [
	{
		kind: 'enum',
		key: 'status',
		param: 'status',
		label: 'Status',
		values: ['completed', 'running', 'pending', 'failed', 'cancelled'],
		default: '',
		chipLabel: (value) => STATUS_CHIP_LABELS[value as Exclude<GenerationStatusFilter, ''>] ?? value
	},
	{
		kind: 'enum',
		key: 'category',
		param: 'category',
		label: 'Category',
		values: FAILURE_ALERT_CATEGORIES.map((category) => category.value),
		default: '',
		chipLabel: (value) => CATEGORY_CHIP_LABELS[value as string] ?? value
	},
	{ kind: 'text', key: 'userId', param: 'user', label: 'User', default: '' },
	{
		kind: 'text',
		key: 'createdFrom',
		param: 'from',
		label: 'Created from',
		default: '',
		chipLabel: (value) => `From ${value}`
	},
	{
		kind: 'text',
		key: 'createdTo',
		param: 'to',
		label: 'Created to',
		default: '',
		chipLabel: (value) => `To ${value}`
	}
];

const codec = createFilterCodec<GenerationsFilters>({
	defaults: DEFAULT_GENERATIONS_FILTERS,
	fields: FIELDS,
	sortValues: ['created_desc', 'created_asc']
});

export function generationsFiltersFromSearchParams(params: URLSearchParams): GenerationsFilters {
	return codec.fromSearchParams(params);
}

export function generationsFiltersToSearchParams(filters: GenerationsFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}

export function generationsFilterActiveCount(filters: GenerationsFilters): number {
	return codec.activeCount(filters);
}

export function generationsFilterChips(filters: GenerationsFilters, userLabel: (userId: string) => string): FilterChip[] {
	return codec.chips(filters, { userId: ((value: string) => userLabel(value)) as (value: never) => string | null });
}

export function clearGenerationsFilterChip(filters: GenerationsFilters, key: string): GenerationsFilters {
	return codec.clearChip(filters, key);
}

export function clearAllGenerationsFilters(filters: GenerationsFilters): GenerationsFilters {
	return codec.clearAll(filters);
}

export function generationsSortParams(sortBy: GenerationSortBy): { sortBy: 'created_at'; sortDir: 'asc' | 'desc' } {
	return { sortBy: 'created_at', sortDir: sortBy === 'created_asc' ? 'asc' : 'desc' };
}
