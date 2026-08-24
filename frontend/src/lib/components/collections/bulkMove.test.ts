import { describe, it, expect } from 'vitest';
import { dropRedundantDescendants, blockedBulkMoveTargets } from './bulkMove';
import type { CollectionLike } from './types';

function collection(id: string, parent_id: string | null, name = id): CollectionLike {
	return { id, parent_id, name, item_count: 0 };
}

describe('dropRedundantDescendants', () => {
	it('drops a selected child whose selected parent is also in the batch', () => {
		const collections = [collection('a', null), collection('b', 'a')];
		expect(dropRedundantDescendants(['a', 'b'], collections)).toEqual(['a']);
	});

	it('drops a selected grandchild whose selected grandparent is in the batch', () => {
		const collections = [
			collection('a', null),
			collection('b', 'a'),
			collection('c', 'b')
		];
		expect(dropRedundantDescendants(['a', 'c'], collections)).toEqual(['a']);
	});

	it('keeps unrelated siblings, order aside', () => {
		const collections = [collection('a', null), collection('b', null)];
		expect(new Set(dropRedundantDescendants(['a', 'b'], collections))).toEqual(new Set(['a', 'b']));
	});

	it('keeps a child selected alongside an unrelated (non-ancestor) folder', () => {
		const collections = [
			collection('a', null),
			collection('b', 'a'),
			collection('c', null)
		];
		expect(new Set(dropRedundantDescendants(['b', 'c'], collections))).toEqual(new Set(['b', 'c']));
	});

	it('de-duplicates repeated ids', () => {
		const collections = [collection('a', null)];
		expect(dropRedundantDescendants(['a', 'a'], collections)).toEqual(['a']);
	});
});

describe('blockedBulkMoveTargets', () => {
	it('blocks a single selected id and its own descendants', () => {
		const collections = [
			collection('a', null),
			collection('b', 'a'),
			collection('c', 'b'),
			collection('sibling', null)
		];
		expect(blockedBulkMoveTargets(['a'], collections)).toEqual(new Set(['a', 'b', 'c']));
	});

	it('unions blocked descendants across every selected id', () => {
		const collections = [
			collection('a', null),
			collection('a-child', 'a'),
			collection('x', null),
			collection('x-child', 'x'),
			collection('unrelated', null)
		];
		expect(blockedBulkMoveTargets(['a', 'x'], collections)).toEqual(
			new Set(['a', 'a-child', 'x', 'x-child'])
		);
	});

	it('does not block an id unrelated to any selected item', () => {
		const collections = [collection('a', null), collection('unrelated', null)];
		expect(blockedBulkMoveTargets(['a'], collections).has('unrelated')).toBe(false);
	});
});
