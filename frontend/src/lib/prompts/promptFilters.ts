import type { PromptListParams } from '$lib/services/api/prompts';
import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip } from '$lib/components/library/librarySection';

export type PromptSourceFilter = 'any' | 'mine' | 'civitai';
export type PromptUsedFilter = 'any' | 'used' | 'never';
export type PromptLastUsedFilter = '' | '24h' | '7d' | '30d';
export type PromptSortBy = 'last_used_at' | 'created_at' | 'name' | 'usage_count';

export interface PromptFilters {
	q: string;
	modelId: string;
	baseModel: string;
	usageHint: '' | 'positive' | 'negative';
	source: PromptSourceFilter;
	used: PromptUsedFilter;
	lastUsed: PromptLastUsedFilter;
	tags: string[];
	hasVariables: boolean;
	showNsfw: boolean;
	sortBy: PromptSortBy;
	sortOrder: 'asc' | 'desc';
}

export const DEFAULT_PROMPT_FILTERS: PromptFilters = {
	q: '',
	modelId: '',
	baseModel: '',
	usageHint: '',
	source: 'any',
	used: 'any',
	lastUsed: '',
	tags: [],
	hasVariables: false,
	showNsfw: false,
	sortBy: 'last_used_at',
	sortOrder: 'desc'
};

const FIELDS: readonly FilterFieldDescriptor<PromptFilters>[] = [
	{
		kind: 'text',
		key: 'modelId',
		param: 'model_id',
		label: 'Model',
		default: '',
		chipKey: 'model',
		chipLabel: (value) => `model = ${value}`
	},
	{ kind: 'text', key: 'baseModel', param: 'base_model', label: 'Base model', default: '' },
	{
		kind: 'enum',
		key: 'usageHint',
		param: 'usage_hint',
		label: 'Usage hint',
		values: ['positive', 'negative'],
		default: ''
	},
	{
		kind: 'enum',
		key: 'source',
		param: 'source',
		label: 'Source',
		values: ['any', 'mine', 'civitai'],
		default: 'any'
	},
	{
		kind: 'enum',
		key: 'used',
		param: 'used',
		label: 'Used in generations',
		values: ['any', 'used', 'never'],
		default: 'any',
		chipLabel: (value) => (value === 'used' ? 'used ≥ 1' : 'never used')
	},
	{
		kind: 'enum',
		key: 'lastUsed',
		param: 'last_used',
		label: 'Last used',
		values: ['24h', '7d', '30d'],
		default: '',
		chipLabel: (value) => `used within ${value}`
	},
	{ kind: 'tags', key: 'tags', param: 'tags', label: 'Tags' },
	{ kind: 'boolean', key: 'hasVariables', param: 'has_variables', label: 'Has variables', chipLabel: 'has variables' },
	{ kind: 'boolean', key: 'showNsfw', param: 'nsfw', label: 'Show NSFW', chipLabel: 'nsfw shown' },
	{
		kind: 'enum',
		key: 'sortOrder',
		param: 'sort_order',
		label: 'Sort order',
		values: ['asc', 'desc'],
		default: 'desc',
		filterable: false
	}
];

const codec = createFilterCodec<PromptFilters>({
	defaults: DEFAULT_PROMPT_FILTERS,
	fields: FIELDS,
	sortValues: ['last_used_at', 'created_at', 'name', 'usage_count']
});

export function promptFiltersFromSearchParams(params: URLSearchParams): PromptFilters {
	return codec.fromSearchParams(params);
}

export function promptFiltersToSearchParams(filters: PromptFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}

export function promptFilterActiveCount(filters: PromptFilters): number {
	return codec.activeCount(filters);
}

export type PromptFilterChip = FilterChip;

export function promptFilterChips(filters: PromptFilters, modelLabel?: string): PromptFilterChip[] {
	return codec.chips(filters, modelLabel ? { modelId: () => `model = ${modelLabel}` } : undefined);
}

export function clearPromptFilterChip(filters: PromptFilters, key: string): PromptFilters {
	return codec.clearChip(filters, key);
}

export function clearAllPromptFilters(filters: PromptFilters): PromptFilters {
	return codec.clearAll(filters);
}

function lastUsedAfterIso(lastUsed: PromptLastUsedFilter, now: Date): string | undefined {
	if (!lastUsed) return undefined;
	const hoursByValue: Record<Exclude<PromptLastUsedFilter, ''>, number> = {
		'24h': 24,
		'7d': 24 * 7,
		'30d': 24 * 30
	};
	return new Date(now.getTime() - hoursByValue[lastUsed] * 60 * 60 * 1000).toISOString();
}

export function promptFiltersToListParams(
	filters: PromptFilters,
	extra: { collectionId?: string; limit?: number; offset?: number; now?: Date } = {}
): PromptListParams {
	return {
		limit: extra.limit,
		offset: extra.offset,
		collection_id: extra.collectionId,
		model_id: filters.modelId || undefined,
		base_model: filters.baseModel || undefined,
		usage_hint: filters.usageHint || undefined,
		source_provider: filters.source === 'mine' ? 'manual' : filters.source === 'civitai' ? 'civitai' : undefined,
		used: filters.used !== 'any' ? filters.used : undefined,
		used_after: lastUsedAfterIso(filters.lastUsed, extra.now ?? new Date()),
		tags: filters.tags.length ? filters.tags.join(',') : undefined,
		has_variables: filters.hasVariables || undefined,
		nsfw: filters.showNsfw ? 'include' : 'exclude',
		sort_by: filters.sortBy,
		sort_order: filters.sortOrder
	};
}
