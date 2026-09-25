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
	modelsSortParams,
	type ModelsFilters
} from './modelsFilters';

const TAGS = [
	{ id: 't1', name: 'anime' },
	{ id: 't2', name: 'photoreal' }
];

describe('modelsFiltersFromSearchParams / modelsFiltersToSearchParams', () => {
	it('round-trips a non-default filter set', () => {
		const filters: ModelsFilters = { q: 'flux', tags: ['t1', 't2'], sortBy: 'filename_asc' };
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
		const filters: ModelsFilters = { q: '', tags: ['t1'], sortBy: 'indexed_at_desc' };
		expect(modelsFilterChips(filters, TAGS)).toEqual([{ key: 'tag:t1', label: 'anime' }]);
	});

	it('falls back to the raw tag id when the tag is not found', () => {
		const filters: ModelsFilters = { q: '', tags: ['unknown'], sortBy: 'indexed_at_desc' };
		expect(modelsFilterChips(filters, TAGS)).toEqual([{ key: 'tag:unknown', label: 'unknown' }]);
	});

	it('emits no chips and zero active count for the default filters', () => {
		expect(modelsFilterChips(DEFAULT_MODELS_FILTERS, TAGS)).toEqual([]);
		expect(modelsFilterActiveCount(DEFAULT_MODELS_FILTERS)).toBe(0);
	});

	it('counts tags as one active filter group and excludes q and sortBy', () => {
		const filters: ModelsFilters = { q: 'keep me', tags: ['t1', 't2'], sortBy: 'filename_asc' };
		expect(modelsFilterActiveCount(filters)).toBe(1);
	});

	it('clears a single tag chip and leaves the rest untouched', () => {
		const filters: ModelsFilters = { q: '', tags: ['t1', 't2'], sortBy: 'indexed_at_desc' };
		expect(clearModelsFilterChip(filters, 'tag:t1')).toEqual({ ...filters, tags: ['t2'] });
		expect(clearModelsFilterChip(filters, 'nothing')).toBe(filters);
	});

	it('clears tags but keeps query and sort when clearing all', () => {
		const filters: ModelsFilters = { q: 'keep me', tags: ['t1'], sortBy: 'filename_asc' };
		expect(clearAllModelsFilters(filters)).toEqual({ q: 'keep me', tags: [], sortBy: 'filename_asc' });
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
