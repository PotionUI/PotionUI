import { describe, expect, it } from 'vitest';
import {
	applyMarquee,
	idsInMarquee,
	rangeSelection,
	rectFromPoints,
	rectsIntersect,
	toggleSelection,
	type SelectionRect
} from './selection';

describe('rectFromPoints', () => {
	it('normalizes corners given in any order', () => {
		expect(rectFromPoints(50, 40, 10, 20)).toEqual({ left: 10, top: 20, right: 50, bottom: 40 });
		expect(rectFromPoints(10, 20, 50, 40)).toEqual({ left: 10, top: 20, right: 50, bottom: 40 });
	});
});

describe('rectsIntersect', () => {
	const a: SelectionRect = { left: 0, top: 0, right: 10, bottom: 10 };

	it('is true for overlapping rects', () => {
		expect(rectsIntersect(a, { left: 5, top: 5, right: 15, bottom: 15 })).toBe(true);
	});

	it('is false for disjoint rects', () => {
		expect(rectsIntersect(a, { left: 20, top: 20, right: 30, bottom: 30 })).toBe(false);
	});

	it('is false for rects that only touch at an edge', () => {
		expect(rectsIntersect(a, { left: 10, top: 0, right: 20, bottom: 10 })).toBe(false);
	});
});

describe('idsInMarquee', () => {
	const rects = new Map<string, SelectionRect>([
		['a', { left: 0, top: 0, right: 10, bottom: 10 }],
		['b', { left: 20, top: 0, right: 30, bottom: 10 }],
		['c', { left: 40, top: 0, right: 50, bottom: 10 }]
	]);

	it('returns only ids whose rect intersects, in order', () => {
		const marquee = rectFromPoints(5, 0, 25, 10);
		expect(idsInMarquee(['a', 'b', 'c'], rects, marquee)).toEqual(['a', 'b']);
	});

	it('skips ids with no measured rect', () => {
		const marquee = rectFromPoints(0, 0, 100, 10);
		expect(idsInMarquee(['a', 'ghost', 'c'], rects, marquee)).toEqual(['a', 'c']);
	});
});

describe('toggleSelection', () => {
	it('adds an unselected id', () => {
		expect(toggleSelection(['a'], 'b')).toEqual(['a', 'b']);
	});

	it('removes an already-selected id', () => {
		expect(toggleSelection(['a', 'b'], 'a')).toEqual(['b']);
	});
});

describe('rangeSelection', () => {
	const order = ['a', 'b', 'c', 'd', 'e'];

	it('selects the range between anchor and target, forward', () => {
		expect(rangeSelection(order, 'b', 'd', [])).toEqual(['b', 'c', 'd']);
	});

	it('selects the range between anchor and target, backward', () => {
		expect(rangeSelection(order, 'd', 'b', [])).toEqual(['b', 'c', 'd']);
	});

	it('unions the range onto the current selection rather than replacing it', () => {
		expect(rangeSelection(order, 'c', 'd', ['a'])).toEqual(['a', 'c', 'd']);
	});

	it('does not duplicate ids already in both the range and the current selection', () => {
		expect(rangeSelection(order, 'b', 'c', ['b', 'c'])).toEqual(['b', 'c']);
	});

	it('falls back to a plain toggle when there is no anchor', () => {
		expect(rangeSelection(order, null, 'c', ['a'])).toEqual(['a', 'c']);
	});

	it('falls back to a plain toggle when the anchor fell out of the loaded order', () => {
		expect(rangeSelection(order, 'ghost', 'c', ['a'])).toEqual(['a', 'c']);
	});
});

describe('applyMarquee', () => {
	it('replace mode makes the marquee ids the whole selection', () => {
		expect(applyMarquee(['x', 'y'], ['a', 'b'], 'replace')).toEqual(['a', 'b']);
	});

	it('add mode unions the marquee ids onto the pre-drag selection', () => {
		expect(applyMarquee(['x'], ['a', 'b'], 'add')).toEqual(['x', 'a', 'b']);
	});

	it('add mode does not duplicate an id already selected before the drag', () => {
		expect(applyMarquee(['a'], ['a', 'b'], 'add')).toEqual(['a', 'b']);
	});
});
