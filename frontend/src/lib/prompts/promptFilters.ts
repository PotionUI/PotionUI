import type { PromptListParams } from '$lib/services/api/prompts';

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

const SORT_VALUES: PromptSortBy[] = ['last_used_at', 'created_at', 'name', 'usage_count'];
const USED_VALUES: PromptUsedFilter[] = ['any', 'used', 'never'];
const SOURCE_VALUES: PromptSourceFilter[] = ['any', 'mine', 'civitai'];
const LAST_USED_VALUES: Exclude<PromptLastUsedFilter, ''>[] = ['24h', '7d', '30d'];

function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
	return value && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

export function promptFiltersFromSearchParams(params: URLSearchParams): PromptFilters {
	const usageHint = params.get('usage_hint');
	return {
		q: params.get('q') ?? DEFAULT_PROMPT_FILTERS.q,
		modelId: params.get('model_id') ?? DEFAULT_PROMPT_FILTERS.modelId,
		baseModel: params.get('base_model') ?? DEFAULT_PROMPT_FILTERS.baseModel,
		usageHint:
			usageHint === 'positive' || usageHint === 'negative' ? usageHint : DEFAULT_PROMPT_FILTERS.usageHint,
		source: oneOf(params.get('source'), SOURCE_VALUES, DEFAULT_PROMPT_FILTERS.source),
		used: oneOf(params.get('used'), USED_VALUES, DEFAULT_PROMPT_FILTERS.used),
		lastUsed: oneOf(params.get('last_used'), LAST_USED_VALUES, DEFAULT_PROMPT_FILTERS.lastUsed),
		tags: (params.get('tags') ?? '')
			.split(',')
			.map((tag) => tag.trim())
			.filter(Boolean),
		hasVariables: params.get('has_variables') === '1',
		showNsfw: params.get('nsfw') === '1',
		sortBy: oneOf(params.get('sort_by'), SORT_VALUES, DEFAULT_PROMPT_FILTERS.sortBy),
		sortOrder: params.get('sort_order') === 'asc' ? 'asc' : DEFAULT_PROMPT_FILTERS.sortOrder
	};
}

export function promptFiltersToSearchParams(filters: PromptFilters): URLSearchParams {
	const params = new URLSearchParams();
	if (filters.q) params.set('q', filters.q);
	if (filters.modelId) params.set('model_id', filters.modelId);
	if (filters.baseModel) params.set('base_model', filters.baseModel);
	if (filters.usageHint) params.set('usage_hint', filters.usageHint);
	if (filters.source !== 'any') params.set('source', filters.source);
	if (filters.used !== 'any') params.set('used', filters.used);
	if (filters.lastUsed) params.set('last_used', filters.lastUsed);
	if (filters.tags.length) params.set('tags', filters.tags.join(','));
	if (filters.hasVariables) params.set('has_variables', '1');
	if (filters.showNsfw) params.set('nsfw', '1');
	if (filters.sortBy !== DEFAULT_PROMPT_FILTERS.sortBy) params.set('sort_by', filters.sortBy);
	if (filters.sortOrder !== DEFAULT_PROMPT_FILTERS.sortOrder) params.set('sort_order', filters.sortOrder);
	return params;
}

export function promptFilterActiveCount(filters: PromptFilters): number {
	let count = 0;
	if (filters.modelId) count++;
	if (filters.baseModel) count++;
	if (filters.usageHint) count++;
	if (filters.source !== 'any') count++;
	if (filters.used !== 'any') count++;
	if (filters.lastUsed) count++;
	if (filters.tags.length) count++;
	if (filters.hasVariables) count++;
	if (filters.showNsfw) count++;
	return count;
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

export interface PromptFilterChip {
	key: string;
	label: string;
}

export function promptFilterChips(filters: PromptFilters, modelLabel?: string): PromptFilterChip[] {
	const chips: PromptFilterChip[] = [];
	if (filters.modelId) chips.push({ key: 'model', label: `model = ${modelLabel || filters.modelId}` });
	if (filters.baseModel) chips.push({ key: 'baseModel', label: `base model = ${filters.baseModel}` });
	if (filters.usageHint) chips.push({ key: 'usageHint', label: filters.usageHint });
	if (filters.source !== 'any') chips.push({ key: 'source', label: filters.source });
	if (filters.used !== 'any')
		chips.push({ key: 'used', label: filters.used === 'used' ? 'used ≥ 1' : 'never used' });
	if (filters.lastUsed) chips.push({ key: 'lastUsed', label: `used within ${filters.lastUsed}` });
	for (const tag of filters.tags) chips.push({ key: `tag:${tag}`, label: `#${tag}` });
	if (filters.hasVariables) chips.push({ key: 'hasVariables', label: 'has variables' });
	if (filters.showNsfw) chips.push({ key: 'showNsfw', label: 'nsfw shown' });
	return chips;
}

export function clearPromptFilterChip(filters: PromptFilters, key: string): PromptFilters {
	if (key === 'model') return { ...filters, modelId: '' };
	if (key === 'baseModel') return { ...filters, baseModel: '' };
	if (key === 'usageHint') return { ...filters, usageHint: '' };
	if (key === 'source') return { ...filters, source: 'any' };
	if (key === 'used') return { ...filters, used: 'any' };
	if (key === 'lastUsed') return { ...filters, lastUsed: '' };
	if (key === 'hasVariables') return { ...filters, hasVariables: false };
	if (key === 'showNsfw') return { ...filters, showNsfw: false };
	if (key.startsWith('tag:')) {
		const tag = key.slice('tag:'.length);
		return { ...filters, tags: filters.tags.filter((t) => t !== tag) };
	}
	return filters;
}

export function clearAllPromptFilters(filters: PromptFilters): PromptFilters {
	return { ...DEFAULT_PROMPT_FILTERS, q: filters.q, sortBy: filters.sortBy, sortOrder: filters.sortOrder };
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
