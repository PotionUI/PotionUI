import { describe, it, expect } from 'vitest';
import {
	buildInspirationsQuery,
	hasActiveInspirationsFilters,
	totalInspirationsPages,
	clampInspirationsPage,
	DEFAULT_INSPIRATIONS_FILTERS
} from './inspirationsQuery';

describe('buildInspirationsQuery', () => {
	it('omits inactive filters and computes offset from page', () => {
		const query = buildInspirationsQuery(DEFAULT_INSPIRATIONS_FILTERS, 3, 24);
		expect(query).toEqual({ limit: 24, offset: 48 });
	});

	it('includes a trimmed search term as query', () => {
		const query = buildInspirationsQuery(
			{ ...DEFAULT_INSPIRATIONS_FILTERS, search: '  sunset  ' },
			1,
			24
		);
		expect(query.query).toBe('sunset');
	});

	it('includes saved only when true', () => {
		const query = buildInspirationsQuery({ ...DEFAULT_INSPIRATIONS_FILTERS, saved: true }, 1, 24);
		expect(query.saved).toBe(true);
	});

	it('includes collection and author filters when set', () => {
		const query = buildInspirationsQuery(
			{ ...DEFAULT_INSPIRATIONS_FILTERS, collectionId: 'col-1', authorId: 'user-1' },
			1,
			24
		);
		expect(query.collection_id).toBe('col-1');
		expect(query.author_id).toBe('user-1');
	});
});

describe('hasActiveInspirationsFilters', () => {
	it('is false for the default filters', () => {
		expect(hasActiveInspirationsFilters(DEFAULT_INSPIRATIONS_FILTERS)).toBe(false);
	});

	it('is true when saved is toggled on', () => {
		expect(hasActiveInspirationsFilters({ ...DEFAULT_INSPIRATIONS_FILTERS, saved: true })).toBe(
			true
		);
	});
});

describe('totalInspirationsPages / clampInspirationsPage', () => {
	it('computes page count from total and page size', () => {
		expect(totalInspirationsPages(50, 24)).toBe(3);
		expect(totalInspirationsPages(0, 24)).toBe(0);
	});

	it('clamps a stale page back onto the last populated page', () => {
		expect(clampInspirationsPage(5, 30, 24)).toBe(2);
		expect(clampInspirationsPage(1, 0, 24)).toBe(1);
	});
});
