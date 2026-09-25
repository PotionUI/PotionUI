import { describe, it, expect } from 'vitest';
import {
	DEFAULT_MODELS_FILTERS,
	MODELS_SORT_VALUES,
	clearAllModelsFilters,
	clearModelsFilterChip,
	modelsFilterActiveCount,
	modelsFilterChips,
	modelsFiltersFromSearchParams,
	modelsFiltersToSearchParams,
	modelsHasActiveFilters,
	modelsQueryParams,
	modelsSortParams,
	withModelsUsage,
	type ModelsFilters
} from './modelsFilters';

const TAGS = [
	{ id: 't1', name: 'anime' },
	{ id: 't2', name: 'photoreal' }
];

describe('modelsFiltersFromSearchParams / modelsFiltersToSearchParams', () => {
	it('round-trips a non-default filter set', () => {
		const filters: ModelsFilters = { ...DEFAULT_MODELS_FILTERS, q: 'flux', tags: ['t1', 't2'], sortBy: 'filename_asc' };
		const params = modelsFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('flux');
		expect(params.get('tags')).toBe('t1,t2');
		expect(params.get('sort_by')).toBe('filename_asc');
		expect(modelsFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('serializes the default filters to an empty query string', () => {
		expect(modelsFiltersToSearchParams(DEFAULT_MODELS_FILTERS).toString()).toBe('');
	});

	it('falls back to defaults for missing or unknown params', () => {
		const params = new URLSearchParams('sort_by=bogus');
		expect(modelsFiltersFromSearchParams(params)).toEqual(DEFAULT_MODELS_FILTERS);
	});
});

describe('models filter chips', () => {
	it('emits a tag chip resolved to the tag name', () => {
		const filters: ModelsFilters = { ...DEFAULT_MODELS_FILTERS, q: '', tags: ['t1'], sortBy: 'indexed_at_desc' };
		expect(modelsFilterChips(filters, TAGS)).toEqual([{ key: 'tag:t1', label: 'anime' }]);
	});

	it('falls back to the raw tag id when the tag is not found', () => {
		const filters: ModelsFilters = { ...DEFAULT_MODELS_FILTERS, q: '', tags: ['unknown'], sortBy: 'indexed_at_desc' };
		expect(modelsFilterChips(filters, TAGS)).toEqual([{ key: 'tag:unknown', label: 'unknown' }]);
	});

	it('emits no chips and zero active count for the default filters', () => {
		expect(modelsFilterChips(DEFAULT_MODELS_FILTERS, TAGS)).toEqual([]);
		expect(modelsFilterActiveCount(DEFAULT_MODELS_FILTERS)).toBe(0);
	});

	it('counts tags as one active filter group and excludes q and sortBy', () => {
		const filters: ModelsFilters = { ...DEFAULT_MODELS_FILTERS, q: 'keep me', tags: ['t1', 't2'], sortBy: 'filename_asc' };
		expect(modelsFilterActiveCount(filters)).toBe(1);
	});

	it('clears a single tag chip and leaves the rest untouched', () => {
		const filters: ModelsFilters = { ...DEFAULT_MODELS_FILTERS, q: '', tags: ['t1', 't2'], sortBy: 'indexed_at_desc' };
		expect(clearModelsFilterChip(filters, 'tag:t1')).toEqual({ ...filters, tags: ['t2'] });
		expect(clearModelsFilterChip(filters, 'nothing')).toBe(filters);
	});

	it('clears tags but keeps query and sort when clearing all', () => {
		const filters: ModelsFilters = { ...DEFAULT_MODELS_FILTERS, q: 'keep me', tags: ['t1'], sortBy: 'filename_asc' };
		expect(clearAllModelsFilters(filters)).toEqual({ ...DEFAULT_MODELS_FILTERS, q: 'keep me', sortBy: 'filename_asc' });
	});
});

describe('modelsHasActiveFilters', () => {
	it('is false for the default filters', () => {
		expect(modelsHasActiveFilters(DEFAULT_MODELS_FILTERS)).toBe(false);
	});

	it('is true when q or tags are set', () => {
		expect(modelsHasActiveFilters({ ...DEFAULT_MODELS_FILTERS, q: 'x' })).toBe(true);
		expect(modelsHasActiveFilters({ ...DEFAULT_MODELS_FILTERS, tags: ['t1'] })).toBe(true);
	});
});

describe('modelsSortParams', () => {
	it('splits every combined sort value into the legacy sort_by/sort_order pair', () => {
		expect(modelsSortParams('indexed_at_desc')).toEqual({ sort_by: 'indexed_at', sort_order: 'desc' });
		expect(modelsSortParams('indexed_at_asc')).toEqual({ sort_by: 'indexed_at', sort_order: 'asc' });
		expect(modelsSortParams('modified_at_desc')).toEqual({ sort_by: 'modified_at', sort_order: 'desc' });
		expect(modelsSortParams('modified_at_asc')).toEqual({ sort_by: 'modified_at', sort_order: 'asc' });
		expect(modelsSortParams('filename_asc')).toEqual({ sort_by: 'filename', sort_order: 'asc' });
		expect(modelsSortParams('filename_desc')).toEqual({ sort_by: 'filename', sort_order: 'desc' });
		expect(modelsSortParams('file_size_desc')).toEqual({ sort_by: 'file_size', sort_order: 'desc' });
		expect(modelsSortParams('file_size_asc')).toEqual({ sort_by: 'file_size', sort_order: 'asc' });
	});

	it('covers every declared sort value with no leftovers', () => {
		for (const value of MODELS_SORT_VALUES) {
			const { sort_by, sort_order } = modelsSortParams(value);
			expect(`${sort_by}_${sort_order}`).toBe(value);
		}
	});
});

describe('advanced model search filters', () => {
	const advanced: ModelsFilters = {
		...DEFAULT_MODELS_FILTERS,
		q: '^flux-.*',
		qMode: 'regex',
		indexedFrom: '2026-01-01',
		indexedTo: '2026-01-31',
		used: 'used',
		minUses: '3',
		lastUsedFrom: '2026-02-01',
		lastUsedTo: '2026-02-28',
		sortBy: 'uses_desc'
	};

	it('round-trips every advanced field through the URL', () => {
		const params = modelsFiltersToSearchParams(advanced);
		expect(params.get('q_mode')).toBe('regex');
		expect(params.get('indexed_from')).toBe('2026-01-01');
		expect(params.get('used')).toBe('used');
		expect(params.get('min_uses')).toBe('3');
		expect(params.get('last_used_to')).toBe('2026-02-28');
		expect(params.get('sort_by')).toBe('uses_desc');
		expect(modelsFiltersFromSearchParams(params)).toEqual(advanced);
	});

	it('drops malformed dates and non-positive use counts from the URL', () => {
		const params = new URLSearchParams('indexed_from=yesterday&min_uses=0&used=used&last_used_to=2026-13');
		const filters = modelsFiltersFromSearchParams(params);
		expect(filters.indexedFrom).toBe('');
		expect(filters.minUses).toBe('');
		expect(filters.lastUsedTo).toBe('');
		expect(filters.used).toBe('used');
	});

	it('ignores usage refinements when the URL asks for never used', () => {
		const filters = modelsFiltersFromSearchParams(new URLSearchParams('used=never&min_uses=2&last_used_from=2026-01-01'));
		expect(filters.used).toBe('never');
		expect(filters.minUses).toBe('');
		expect(filters.lastUsedFrom).toBe('');
	});

	it('emits one removable chip per active filter and counts them, never the regex toggle', () => {
		const chips = modelsFilterChips(advanced, TAGS);
		expect(chips).toEqual([
			{ key: 'indexedFrom', label: 'Indexed from 2026-01-01' },
			{ key: 'indexedTo', label: 'Indexed to 2026-01-31' },
			{ key: 'used', label: 'Used' },
			{ key: 'minUses', label: 'At least 3 uses' },
			{ key: 'lastUsedFrom', label: 'Last used from 2026-02-01' },
			{ key: 'lastUsedTo', label: 'Last used to 2026-02-28' }
		]);
		expect(modelsFilterActiveCount(advanced)).toBe(6);
		expect(modelsFilterChips({ ...DEFAULT_MODELS_FILTERS, used: 'never' }, TAGS)).toEqual([{ key: 'used', label: 'Never used' }]);
	});

	it('clears a single advanced chip back to its default', () => {
		expect(clearModelsFilterChip(advanced, 'indexedFrom').indexedFrom).toBe('');
		expect(clearModelsFilterChip(advanced, 'used').used).toBe('any');
		expect(clearModelsFilterChip(advanced, 'minUses')).toEqual({ ...advanced, minUses: '' });
	});

	it('clear all keeps the query, its regex mode and the sort', () => {
		expect(clearAllModelsFilters(advanced)).toEqual({ ...DEFAULT_MODELS_FILTERS, q: '^flux-.*', qMode: 'regex', sortBy: 'uses_desc' });
	});

	it('treats any advanced filter as active', () => {
		expect(modelsHasActiveFilters({ ...DEFAULT_MODELS_FILTERS, used: 'never' })).toBe(true);
		expect(modelsHasActiveFilters({ ...DEFAULT_MODELS_FILTERS, qMode: 'regex' })).toBe(false);
	});

	it('switching usage drops refinements that no longer apply', () => {
		expect(withModelsUsage(advanced, 'never')).toMatchObject({ used: 'never', minUses: '', lastUsedFrom: '', lastUsedTo: '' });
		expect(withModelsUsage(advanced, 'any')).toMatchObject({ used: 'any', minUses: '', lastUsedFrom: '2026-02-01' });
		expect(withModelsUsage({ ...advanced, used: 'any' }, 'used')).toMatchObject({ used: 'used', minUses: '3' });
	});

	it('builds API params, sending q_mode only with a query', () => {
		expect(modelsQueryParams(advanced)).toEqual({
			search: '^flux-.*',
			q_mode: 'regex',
			indexed_from: '2026-01-01',
			indexed_to: '2026-01-31',
			used: 'used',
			min_uses: 3,
			last_used_from: '2026-02-01',
			last_used_to: '2026-02-28'
		});
		expect(modelsQueryParams({ ...DEFAULT_MODELS_FILTERS, qMode: 'regex' })).toEqual({});
		expect(modelsQueryParams({ ...DEFAULT_MODELS_FILTERS, used: 'never', minUses: '2' })).toEqual({ used: 'never' });
	});

	it('maps the usage sorts onto the API sort fields', () => {
		expect(modelsSortParams('uses_desc')).toEqual({ sort_by: 'uses', sort_order: 'desc' });
		expect(modelsSortParams('last_used_desc')).toEqual({ sort_by: 'last_used', sort_order: 'desc' });
	});
});
