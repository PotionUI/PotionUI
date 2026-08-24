import { describe, it, expect } from 'vitest';
import {
	DEFAULT_LIBRARY_FILTERS,
	buildLibraryQuery,
	clampLibraryPage,
	hasActiveLibraryFilters,
	totalLibraryPages,
	type LibraryFilters
} from './libraryQuery';

function filters(overrides: Partial<LibraryFilters> = {}): LibraryFilters {
	return { ...DEFAULT_LIBRARY_FILTERS, ...overrides };
}

describe('buildLibraryQuery', () => {
	it('sends only paging when nothing is filtered', () => {
		expect(buildLibraryQuery(filters(), 1, 24)).toEqual({ limit: 24, offset: 0 });
	});

	it('derives offset from the 1-based page', () => {
		expect(buildLibraryQuery(filters(), 3, 24).offset).toBe(48);
	});

	it('treats a page below 1 as the first page', () => {
		expect(buildLibraryQuery(filters(), 0, 24).offset).toBe(0);
	});

	it('joins tag ids into the comma-separated form the route parses', () => {
		expect(buildLibraryQuery(filters({ selectedTagIds: ['a', 'b'] }), 1, 24).tag_ids).toBe('a,b');
	});

	it('drops empty tag ids instead of sending a blank segment', () => {
		expect(buildLibraryQuery(filters({ selectedTagIds: ['a', ''] }), 1, 24).tag_ids).toBe('a');
	});

	it('omits tag_ids entirely when no tag is selected', () => {
		expect(buildLibraryQuery(filters({ selectedTagIds: [] }), 1, 24)).not.toHaveProperty('tag_ids');
	});

	it('omits an all-media-types filter', () => {
		expect(buildLibraryQuery(filters({ mediaType: 'all' }), 1, 24)).not.toHaveProperty('media_type');
		expect(buildLibraryQuery(filters({ mediaType: 'video' }), 1, 24).media_type).toBe('video');
	});

	it('trims search and omits a whitespace-only query', () => {
		expect(buildLibraryQuery(filters({ search: '  cat  ' }), 1, 24).search).toBe('cat');
		expect(buildLibraryQuery(filters({ search: '   ' }), 1, 24)).not.toHaveProperty('search');
	});

	it('passes the active collection through', () => {
		expect(buildLibraryQuery(filters({ collectionId: 'col-1' }), 1, 24).collection_id).toBe('col-1');
	});
});

describe('hasActiveLibraryFilters', () => {
	it('is false for the default filters', () => {
		expect(hasActiveLibraryFilters(filters())).toBe(false);
	});

	it('ignores a whitespace-only search', () => {
		expect(hasActiveLibraryFilters(filters({ search: '  ' }))).toBe(false);
	});

	it.each([
		['media type', { mediaType: 'image' as const }],
		['tags', { selectedTagIds: ['t'] }],
		['collection', { collectionId: 'c' }],
		['search', { search: 'x' }]
	])('is true when %s is set', (_label, overrides) => {
		expect(hasActiveLibraryFilters(filters(overrides))).toBe(true);
	});
});

describe('totalLibraryPages', () => {
	it('rounds a partial page up', () => {
		expect(totalLibraryPages(25, 24)).toBe(2);
	});

	it('is zero for an empty library', () => {
		expect(totalLibraryPages(0, 24)).toBe(0);
	});
});

describe('clampLibraryPage', () => {
	it('pulls a page past the end back to the last populated page', () => {
		expect(clampLibraryPage(3, 25, 24)).toBe(2);
	});

	it('leaves a page inside the range alone', () => {
		expect(clampLibraryPage(2, 100, 24)).toBe(2);
	});

	it('falls back to page 1 when everything was deleted', () => {
		expect(clampLibraryPage(4, 0, 24)).toBe(1);
	});
});
