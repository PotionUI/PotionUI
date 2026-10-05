import { describe, it, expect } from 'vitest';
import { collectionQueryParams, collectionQueryStrings, collectionListParams } from './selection';
import { buildLibraryQuery, DEFAULT_LIBRARY_FILTERS } from '$lib/library/libraryQuery';
import {
	buildInspirationsQuery,
	DEFAULT_INSPIRATIONS_FILTERS
} from '$lib/inspirations/inspirationsQuery';

describe('collectionQueryParams', () => {
	it('sends nothing for the All view', () => {
		expect(collectionQueryParams({})).toEqual({});
	});

	it('includes descendants by default', () => {
		expect(collectionQueryParams({ collectionId: 'c1' })).toEqual({ collection_id: 'c1' });
	});

	it('turns descendants off when direct only is set', () => {
		expect(collectionQueryParams({ collectionId: 'c1', directOnly: true })).toEqual({
			collection_id: 'c1',
			include_descendants: false
		});
	});

	it('sends only unsorted for the Unsorted view, even with a stale collection', () => {
		expect(collectionQueryParams({ unsorted: true, collectionId: 'c1', directOnly: true })).toEqual({
			unsorted: true
		});
	});

	it('does not send direct only without a collection', () => {
		expect(collectionQueryParams({ directOnly: true })).toEqual({});
	});

	it('stringifies for URL builders', () => {
		expect(collectionQueryStrings({ collectionId: 'c1', directOnly: true })).toEqual({
			collection_id: 'c1',
			include_descendants: 'false'
		});
	});

	it('shapes the collection list request', () => {
		expect(collectionListParams(false)).toEqual({});
		expect(collectionListParams(true)).toEqual({ include_descendants: false });
	});
});

describe('page queries use the shared params', () => {
	it('library', () => {
		const query = buildLibraryQuery(
			{ ...DEFAULT_LIBRARY_FILTERS, collectionId: 'c1', directOnly: true, favoritesOnly: true },
			1,
			24
		);
		expect(query.collection_id).toBe('c1');
		expect(query.include_descendants).toBe(false);
		expect(query.favorites_only).toBe(true);
		const unsorted = buildLibraryQuery({ ...DEFAULT_LIBRARY_FILTERS, unsorted: true }, 1, 24);
		expect(unsorted.unsorted).toBe(true);
		expect(unsorted.collection_id).toBeUndefined();
	});

	it('inspirations', () => {
		const query = buildInspirationsQuery(
			{ ...DEFAULT_INSPIRATIONS_FILTERS, collectionId: 'c1', directOnly: true },
			1,
			24
		);
		expect(query.include_descendants).toBe(false);
		expect(
			buildInspirationsQuery({ ...DEFAULT_INSPIRATIONS_FILTERS, unsorted: true }, 1, 24).unsorted
		).toBe(true);
	});
});
