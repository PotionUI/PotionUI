import { describe, it, expect } from 'vitest';
import {
	DEFAULT_GENERATIONS_FILTERS,
	clearAllGenerationsFilters,
	clearGenerationsFilterChip,
	generationsFilterActiveCount,
	generationsFilterChips,
	generationsFiltersFromSearchParams,
	generationsFiltersToSearchParams,
	generationsSortParams,
	type GenerationsFilters
} from './generationsFilters';

describe('generationsFiltersFromSearchParams / generationsFiltersToSearchParams', () => {
	it('round-trips a fully-set filter state through the URL', () => {
		const filters: GenerationsFilters = {
			q: 'portrait',
			status: 'completed',
			userId: 'user-1',
			createdFrom: '2026-09-01',
			createdTo: '2026-09-20',
			sortBy: 'created_asc'
		};
		const params = generationsFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('portrait');
		expect(params.get('status')).toBe('completed');
		expect(params.get('user')).toBe('user-1');
		expect(params.get('from')).toBe('2026-09-01');
		expect(params.get('to')).toBe('2026-09-20');
		expect(params.get('sort_by')).toBe('created_asc');
		expect(generationsFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('omits defaults from the URL so a clean list has no query string', () => {
		expect(generationsFiltersToSearchParams(DEFAULT_GENERATIONS_FILTERS).toString()).toBe('');
	});

	it('falls back to the defaults for an unknown status or sort value', () => {
		const params = new URLSearchParams({ status: 'archived', sort_by: 'rating' });
		const filters = generationsFiltersFromSearchParams(params);
		expect(filters.status).toBe('');
		expect(filters.sortBy).toBe('created_desc');
	});
});

describe('generations filter chips', () => {
	it('emits one chip per active field with a friendly status label and clears them individually', () => {
		const filters: GenerationsFilters = {
			q: 'x',
			status: 'failed',
			userId: 'user-1',
			createdFrom: '2026-09-01',
			createdTo: '2026-09-20',
			sortBy: 'created_desc'
		};
		const chips = generationsFilterChips(filters, (userId) => `user:${userId}`);
		expect(chips).toEqual([
			{ key: 'status', label: 'Failed' },
			{ key: 'userId', label: 'user:user-1' },
			{ key: 'createdFrom', label: 'From 2026-09-01' },
			{ key: 'createdTo', label: 'To 2026-09-20' }
		]);
		expect(clearGenerationsFilterChip(filters, 'status').status).toBe('');
		expect(clearGenerationsFilterChip(filters, 'userId').userId).toBe('');
		expect(clearGenerationsFilterChip(filters, 'nothing')).toBe(filters);
	});

	it('counts every active field and keeps query and sort when clearing all', () => {
		const filters: GenerationsFilters = {
			q: 'keep me',
			status: 'running',
			userId: 'user-2',
			createdFrom: '2026-09-01',
			createdTo: '',
			sortBy: 'created_asc'
		};
		expect(generationsFilterActiveCount(filters)).toBe(3);
		expect(clearAllGenerationsFilters(filters)).toEqual({
			q: 'keep me',
			status: '',
			userId: '',
			createdFrom: '',
			createdTo: '',
			sortBy: 'created_asc'
		});
		expect(generationsFilterActiveCount(DEFAULT_GENERATIONS_FILTERS)).toBe(0);
	});
});

describe('generationsSortParams', () => {
	it('maps the sort option to the backend column and direction', () => {
		expect(generationsSortParams('created_desc')).toEqual({ sortBy: 'created_at', sortDir: 'desc' });
		expect(generationsSortParams('created_asc')).toEqual({ sortBy: 'created_at', sortDir: 'asc' });
	});
});
