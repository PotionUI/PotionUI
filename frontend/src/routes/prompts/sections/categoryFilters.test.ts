import { describe, it, expect } from 'vitest';
import type { SegmentCategory } from '$lib/types/segments';
import {
	DEFAULT_CATEGORY_FILTERS,
	applyCategoryFilters,
	categoryFilterActiveCount,
	categoryFilterChips,
	categoryFiltersFromSearchParams,
	categoryFiltersToSearchParams,
	clearAllCategoryFilters,
	clearCategoryFilterChip,
	type CategoryFilters
} from './categoryFilters';

function category(id: string, name: string, description = ''): SegmentCategory {
	return { id, name, description, color: '#3B82F6' };
}

const categories = [
	category('cam', 'Camera', 'Lens, angle and movement'),
	category('light', 'Lighting', 'Key and fill'),
	category('style', 'Style'),
	category('empty', 'Empty bin')
];
const counts = new Map<string, number>([
	['cam', 12],
	['light', 9],
	['style', 17]
]);

describe('categoryFilters URL round-trip', () => {
	it('round-trips every non-default value', () => {
		const filters: CategoryFilters = { q: 'cam', has: 'empty', sortBy: 'count' };
		const params = categoryFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('cam');
		expect(params.get('has')).toBe('empty');
		expect(params.get('sort_by')).toBe('count');
		expect(categoryFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('omits defaults from the URL', () => {
		expect(categoryFiltersToSearchParams(DEFAULT_CATEGORY_FILTERS).toString()).toBe('');
	});

	it('falls back to defaults for unknown values', () => {
		const params = new URLSearchParams('has=bogus&sort_by=nope');
		expect(categoryFiltersFromSearchParams(params)).toEqual(DEFAULT_CATEGORY_FILTERS);
	});
});

describe('applyCategoryFilters', () => {
	it('searches name and description case-insensitively', () => {
		const result = applyCategoryFilters(categories, counts, { ...DEFAULT_CATEGORY_FILTERS, q: 'LENS' });
		expect(result.map((entry) => entry.id)).toEqual(['cam']);
	});

	it('keeps only categories with segments', () => {
		const result = applyCategoryFilters(categories, counts, { ...DEFAULT_CATEGORY_FILTERS, has: 'segments' });
		expect(result.map((entry) => entry.id)).toEqual(['cam', 'light', 'style']);
	});

	it('keeps only empty categories', () => {
		const result = applyCategoryFilters(categories, counts, { ...DEFAULT_CATEGORY_FILTERS, has: 'empty' });
		expect(result.map((entry) => entry.id)).toEqual(['empty']);
	});

	it('sorts by name by default', () => {
		const result = applyCategoryFilters(categories, counts, DEFAULT_CATEGORY_FILTERS);
		expect(result.map((entry) => entry.name)).toEqual(['Camera', 'Empty bin', 'Lighting', 'Style']);
	});

	it('sorts by segment count descending, then name', () => {
		const tied = [...categories, category('zed', 'Alpha tie')];
		const tiedCounts = new Map(counts);
		tiedCounts.set('zed', 12);
		const result = applyCategoryFilters(tied, tiedCounts, { ...DEFAULT_CATEGORY_FILTERS, sortBy: 'count' });
		expect(result.map((entry) => entry.id)).toEqual(['style', 'zed', 'cam', 'light', 'empty']);
	});
});

describe('category filter chips', () => {
	it('emits a chip for the has filter and clears it by key', () => {
		const filters: CategoryFilters = { ...DEFAULT_CATEGORY_FILTERS, has: 'segments' };
		expect(categoryFilterChips(filters)).toEqual([{ key: 'has', label: 'with segments' }]);
		expect(categoryFilterActiveCount(filters)).toBe(1);
		expect(clearCategoryFilterChip(filters, 'has')).toEqual(DEFAULT_CATEGORY_FILTERS);
		expect(clearCategoryFilterChip(filters, 'unknown')).toBe(filters);
	});

	it('clear all keeps the query and the sort', () => {
		const filters: CategoryFilters = { q: 'cam', has: 'empty', sortBy: 'count' };
		expect(clearAllCategoryFilters(filters)).toEqual({ q: 'cam', has: '', sortBy: 'count' });
		expect(categoryFilterChips(DEFAULT_CATEGORY_FILTERS)).toEqual([]);
		expect(categoryFilterActiveCount(DEFAULT_CATEGORY_FILTERS)).toBe(0);
	});
});
