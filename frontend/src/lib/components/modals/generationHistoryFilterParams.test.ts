import { describe, it, expect } from 'vitest';
import { buildHistoryFilterParams } from './generationHistoryFilterParams';

describe('buildHistoryFilterParams', () => {
	it('returns an empty object when no filters are set', () => {
		expect(buildHistoryFilterParams({ tagIds: [], collectionId: null })).toEqual({});
	});

	it('includes tagIds when tags are selected', () => {
		expect(buildHistoryFilterParams({ tagIds: ['t1', 't2'], collectionId: null })).toEqual({
			tagIds: ['t1', 't2']
		});
	});

	it('includes collectionId when a collection is selected', () => {
		expect(buildHistoryFilterParams({ tagIds: [], collectionId: 'c1' })).toEqual({
			collectionId: 'c1'
		});
	});

	it('combines tagIds and collectionId when both are set', () => {
		expect(buildHistoryFilterParams({ tagIds: ['t1'], collectionId: 'c1' })).toEqual({
			tagIds: ['t1'],
			collectionId: 'c1'
		});
	});

	it('"All collections" (null) omits collectionId entirely, not as an empty string', () => {
		const result = buildHistoryFilterParams({ tagIds: ['t1'], collectionId: null });
		expect(result).not.toHaveProperty('collectionId');
		expect(result).toEqual({ tagIds: ['t1'] });
	});

	it('an empty-string collectionId is also omitted', () => {
		const result = buildHistoryFilterParams({ tagIds: [], collectionId: '' });
		expect(result).not.toHaveProperty('collectionId');
	});
});
