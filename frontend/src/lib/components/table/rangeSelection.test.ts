import { describe, expect, it } from 'vitest';
import { applyRange, moveActiveIndex, rangeBetween } from './selection';
import { scrollTopToReveal, shouldVirtualize, virtualWindow } from './virtualWindow';

const IDS = ['a', 'b', 'c', 'd', 'e'];

describe('rangeBetween', () => {
	it('returns the inclusive range in visible order in either direction', () => {
		expect(rangeBetween(IDS, 'b', 'd')).toEqual(['b', 'c', 'd']);
		expect(rangeBetween(IDS, 'd', 'b')).toEqual(['b', 'c', 'd']);
	});

	it('falls back to the target alone without a usable anchor', () => {
		expect(rangeBetween(IDS, null, 'c')).toEqual(['c']);
		expect(rangeBetween(IDS, 'gone', 'c')).toEqual(['c']);
		expect(rangeBetween(IDS, 'a', 'gone')).toEqual([]);
	});
});

describe('applyRange', () => {
	it('selects or deselects a range and leaves other ids alone', () => {
		expect([...applyRange(new Set(['z']), ['a', 'b'], true)].sort()).toEqual(['a', 'b', 'z']);
		expect([...applyRange(new Set(['a', 'b', 'z']), ['a', 'b'], false)]).toEqual(['z']);
	});

	it('skips locked ids', () => {
		expect([...applyRange(new Set(), ['a', 'b', 'c'], true, new Set(['b']))].sort()).toEqual(['a', 'c']);
	});
});

describe('moveActiveIndex', () => {
	it('moves within bounds', () => {
		expect(moveActiveIndex(0, 'ArrowDown', 3)).toBe(1);
		expect(moveActiveIndex(2, 'ArrowDown', 3)).toBe(2);
		expect(moveActiveIndex(0, 'ArrowUp', 3)).toBe(0);
		expect(moveActiveIndex(1, 'Home', 3)).toBe(0);
		expect(moveActiveIndex(1, 'End', 3)).toBe(2);
	});

	it('starts at an end when nothing is active and handles empty lists', () => {
		expect(moveActiveIndex(-1, 'ArrowDown', 3)).toBe(0);
		expect(moveActiveIndex(-1, 'ArrowUp', 3)).toBe(2);
		expect(moveActiveIndex(-1, 'ArrowDown', 0)).toBe(-1);
	});
});

describe('virtualWindow', () => {
	it('only virtualises above the threshold', () => {
		expect(shouldVirtualize(150, true)).toBe(false);
		expect(shouldVirtualize(151, true)).toBe(true);
		expect(shouldVirtualize(500, false)).toBe(false);
	});

	it('windows around the scroll position with padding for the rest', () => {
		const win = virtualWindow({ count: 1000, rowHeight: 50, scrollTop: 5000, viewport: 500, overscan: 5 });
		expect(win.start).toBe(95);
		expect(win.end).toBe(115);
		expect(win.padTop).toBe(95 * 50);
		expect(win.padBottom).toBe(885 * 50);
	});

	it('clamps at both ends', () => {
		expect(virtualWindow({ count: 1000, rowHeight: 50, scrollTop: 0, viewport: 500, overscan: 5 }).start).toBe(0);
		expect(virtualWindow({ count: 20, rowHeight: 50, scrollTop: 99999, viewport: 500, overscan: 5 }).end).toBe(20);
	});

	it('computes the scroll position that reveals a row', () => {
		expect(scrollTopToReveal({ index: 0, rowHeight: 50, scrollTop: 300, viewport: 500 })).toBe(0);
		expect(scrollTopToReveal({ index: 20, rowHeight: 50, scrollTop: 0, viewport: 500 })).toBe(550);
		expect(scrollTopToReveal({ index: 3, rowHeight: 50, scrollTop: 0, viewport: 500 })).toBe(0);
	});
});
