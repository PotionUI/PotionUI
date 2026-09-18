import { describe, expect, it } from 'vitest';
import { partitionNavItems } from './sidebarOverflow';

describe('partitionNavItems', () => {
	it('keeps everything visible when it fits exactly', () => {
		const items = ['a', 'b', 'c'];

		const result = partitionNavItems(items, 132, { rowHeight: 44, reservedHeight: 0 });

		expect(result.visible).toEqual(['a', 'b', 'c']);
		expect(result.overflow).toEqual([]);
	});

	it('reserves a row for the More trigger and overflows the tail when one item too many', () => {
		const items = ['a', 'b', 'c', 'd'];

		const result = partitionNavItems(items, 132, { rowHeight: 44, reservedHeight: 0 });

		expect(result.visible).toEqual(['a', 'b']);
		expect(result.overflow).toEqual(['c', 'd']);
	});

	it('overflows everything when there is no available height', () => {
		const items = ['a', 'b', 'c'];

		const result = partitionNavItems(items, 0, { rowHeight: 44, reservedHeight: 0 });

		expect(result.visible).toEqual([]);
		expect(result.overflow).toEqual(['a', 'b', 'c']);
	});

	it('subtracts reservedHeight before fitting rows', () => {
		const items = ['a', 'b', 'c'];

		const result = partitionNavItems(items, 132, { rowHeight: 44, reservedHeight: 44 });

		expect(result.visible).toEqual(['a']);
		expect(result.overflow).toEqual(['b', 'c']);
	});

	it('returns an empty result for an empty item list', () => {
		const result = partitionNavItems([], 132, { rowHeight: 44, reservedHeight: 0 });

		expect(result.visible).toEqual([]);
		expect(result.overflow).toEqual([]);
	});
});
