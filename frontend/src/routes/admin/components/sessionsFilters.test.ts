import { describe, it, expect } from 'vitest';
import { DEFAULT_SESSIONS_FILTERS, sessionsFiltersFromSearchParams, sessionsFiltersToSearchParams } from './sessionsFilters';

describe('sessionsFiltersFromSearchParams / sessionsFiltersToSearchParams', () => {
	it('round-trips q and sortBy through the URL', () => {
		const params = new URLSearchParams({ q: 'alice', sort_by: 'recent' });
		expect(sessionsFiltersFromSearchParams(params)).toEqual({ q: 'alice', sortBy: 'recent' });
	});

	it('omits defaults from the URL so a clean list has no query string', () => {
		expect(sessionsFiltersToSearchParams(DEFAULT_SESSIONS_FILTERS).toString()).toBe('');
	});

	it('falls back to the default sort for an unknown value', () => {
		const filters = sessionsFiltersFromSearchParams(new URLSearchParams({ sort_by: 'oldest' }));
		expect(filters.sortBy).toBe('recent');
	});

	it('carries a search term into the query string and back', () => {
		const filters = { q: 'bob@example.com', sortBy: 'recent' as const };
		const params = sessionsFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('bob@example.com');
		expect(sessionsFiltersFromSearchParams(params)).toEqual(filters);
	});
});
