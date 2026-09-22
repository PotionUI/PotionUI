import { describe, it, expect } from 'vitest';
import {
	DEFAULT_PROMPT_FILTERS,
	promptFiltersFromSearchParams,
	promptFiltersToSearchParams,
	promptFilterActiveCount,
	promptFilterChips,
	clearPromptFilterChip,
	clearAllPromptFilters,
	promptFiltersToListParams,
	type PromptFilters
} from './promptFilters';

describe('promptFiltersFromSearchParams / promptFiltersToSearchParams', () => {
	it('round-trips a fully-set filter state through the URL', () => {
		const filters: PromptFilters = {
			q: 'dance',
			modelId: 'model-1',
			baseModel: 'sdxl',
			usageHint: 'negative',
			source: 'civitai',
			used: 'used',
			lastUsed: '7d',
			tags: ['dance', 'tiktok'],
			hasVariables: true,
			showNsfw: true,
			sortBy: 'usage_count',
			sortOrder: 'asc'
		};

		const params = promptFiltersToSearchParams(filters);
		const restored = promptFiltersFromSearchParams(params);

		expect(restored).toEqual(filters);
	});

	it('returns the defaults for an empty query string', () => {
		expect(promptFiltersFromSearchParams(new URLSearchParams(''))).toEqual(DEFAULT_PROMPT_FILTERS);
	});

	it('writes no keys for the default filter state', () => {
		expect(promptFiltersToSearchParams(DEFAULT_PROMPT_FILTERS).toString()).toBe('');
	});

	it('falls back to defaults for out-of-range enum values instead of throwing', () => {
		const params = new URLSearchParams('used=bogus&source=bogus&sort_by=bogus&usage_hint=bogus&last_used=bogus');
		expect(promptFiltersFromSearchParams(params)).toEqual(DEFAULT_PROMPT_FILTERS);
	});

	it('parses a comma-joined tags list, trimming and dropping blanks', () => {
		const params = new URLSearchParams('tags=dance, tiktok,,portrait');
		expect(promptFiltersFromSearchParams(params).tags).toEqual(['dance', 'tiktok', 'portrait']);
	});
});

describe('promptFilterActiveCount', () => {
	it('is zero for the default filters', () => {
		expect(promptFilterActiveCount(DEFAULT_PROMPT_FILTERS)).toBe(0);
	});

	it('counts each active field once, tags as a single field', () => {
		const filters: PromptFilters = {
			...DEFAULT_PROMPT_FILTERS,
			modelId: 'm1',
			used: 'used',
			tags: ['a', 'b', 'c']
		};
		expect(promptFilterActiveCount(filters)).toBe(3);
	});

	it('does not count q or sort', () => {
		const filters: PromptFilters = { ...DEFAULT_PROMPT_FILTERS, q: 'dance', sortBy: 'name', sortOrder: 'asc' };
		expect(promptFilterActiveCount(filters)).toBe(0);
	});
});

describe('promptFilterChips / clearPromptFilterChip', () => {
	it('builds one chip per active field, resolving the model label', () => {
		const filters: PromptFilters = { ...DEFAULT_PROMPT_FILTERS, modelId: 'model-1', hasVariables: true };
		const chips = promptFilterChips(filters, 'MiniMax-H3');
		expect(chips).toEqual([
			{ key: 'model', label: 'model = MiniMax-H3' },
			{ key: 'hasVariables', label: 'has variables' }
		]);
	});

	it('falls back to the raw id when no model label is given', () => {
		const filters: PromptFilters = { ...DEFAULT_PROMPT_FILTERS, modelId: 'model-1' };
		expect(promptFilterChips(filters)).toEqual([{ key: 'model', label: 'model = model-1' }]);
	});

	it('emits one chip per tag and clears only that tag', () => {
		const filters: PromptFilters = { ...DEFAULT_PROMPT_FILTERS, tags: ['dance', 'portrait'] };
		const chips = promptFilterChips(filters);
		expect(chips).toEqual([
			{ key: 'tag:dance', label: '#dance' },
			{ key: 'tag:portrait', label: '#portrait' }
		]);
		expect(clearPromptFilterChip(filters, 'tag:dance').tags).toEqual(['portrait']);
	});

	it('clears exactly the field named by the chip key', () => {
		const filters: PromptFilters = { ...DEFAULT_PROMPT_FILTERS, used: 'never', showNsfw: true };
		expect(clearPromptFilterChip(filters, 'used')).toEqual({ ...filters, used: 'any' });
		expect(clearPromptFilterChip(filters, 'showNsfw')).toEqual({ ...filters, showNsfw: false });
	});

	it('is a no-op for an unknown key', () => {
		expect(clearPromptFilterChip(DEFAULT_PROMPT_FILTERS, 'nonsense')).toEqual(DEFAULT_PROMPT_FILTERS);
	});
});

describe('clearAllPromptFilters', () => {
	it('resets every filter field but keeps the search query and sort', () => {
		const filters: PromptFilters = {
			...DEFAULT_PROMPT_FILTERS,
			q: 'dance',
			modelId: 'm1',
			tags: ['a'],
			sortBy: 'name',
			sortOrder: 'asc'
		};
		expect(clearAllPromptFilters(filters)).toEqual({
			...DEFAULT_PROMPT_FILTERS,
			q: 'dance',
			sortBy: 'name',
			sortOrder: 'asc'
		});
	});
});

describe('promptFiltersToListParams', () => {
	it('maps every field to its GET /api/prompts param', () => {
		const filters: PromptFilters = {
			q: 'dance',
			modelId: 'model-1',
			baseModel: 'sdxl',
			usageHint: 'positive',
			source: 'civitai',
			used: 'used',
			lastUsed: '',
			tags: ['dance', 'tiktok'],
			hasVariables: true,
			showNsfw: false,
			sortBy: 'usage_count',
			sortOrder: 'asc'
		};

		const params = promptFiltersToListParams(filters, { collectionId: 'col-1', limit: 48, offset: 0 });

		expect(params).toMatchObject({
			limit: 48,
			offset: 0,
			collection_id: 'col-1',
			model_id: 'model-1',
			base_model: 'sdxl',
			usage_hint: 'positive',
			source_provider: 'civitai',
			used: 'used',
			tags: 'dance,tiktok',
			has_variables: true,
			nsfw: 'exclude',
			sort_by: 'usage_count',
			sort_order: 'asc'
		});
		expect(params.used_after).toBeUndefined();
	});

	it('maps "mine" to the manual source_provider', () => {
		const filters: PromptFilters = { ...DEFAULT_PROMPT_FILTERS, source: 'mine' };
		expect(promptFiltersToListParams(filters).source_provider).toBe('manual');
	});

	it('turns showNsfw on into nsfw=include', () => {
		const filters: PromptFilters = { ...DEFAULT_PROMPT_FILTERS, showNsfw: true };
		expect(promptFiltersToListParams(filters).nsfw).toBe('include');
	});

	it('computes used_after from lastUsed relative to the given `now`', () => {
		const filters: PromptFilters = { ...DEFAULT_PROMPT_FILTERS, lastUsed: '7d' };
		const now = new Date('2026-09-22T00:00:00.000Z');
		const params = promptFiltersToListParams(filters, { now });
		expect(params.used_after).toBe('2026-09-15T00:00:00.000Z');
	});
});
