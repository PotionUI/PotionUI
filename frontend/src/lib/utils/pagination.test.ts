import { describe, it, expect } from 'vitest';
import { pageWindow, PAGE_WINDOW_SLOTS } from './pagination';

describe('pageWindow', () => {
	it('returns nothing for zero or negative totals', () => {
		expect(pageWindow(1, 0)).toEqual([]);
		expect(pageWindow(1, -3)).toEqual([]);
	});

	it('lists every page without ellipsis when they fit', () => {
		expect(pageWindow(1, 1)).toEqual([1]);
		expect(pageWindow(2, 4)).toEqual([1, 2, 3, 4]);
		expect(pageWindow(4, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
	});

	it('anchors to the head near the start', () => {
		expect(pageWindow(1, 20)).toEqual([1, 2, 3, 4, 5, 'ellipsis', 20]);
		expect(pageWindow(4, 20)).toEqual([1, 2, 3, 4, 5, 'ellipsis', 20]);
	});

	it('centres on the current page in the middle', () => {
		expect(pageWindow(5, 20)).toEqual([1, 'ellipsis', 4, 5, 6, 'ellipsis', 20]);
		expect(pageWindow(10, 20)).toEqual([1, 'ellipsis', 9, 10, 11, 'ellipsis', 20]);
	});

	it('anchors to the tail near the end', () => {
		expect(pageWindow(17, 20)).toEqual([1, 'ellipsis', 16, 17, 18, 19, 20]);
		expect(pageWindow(20, 20)).toEqual([1, 'ellipsis', 16, 17, 18, 19, 20]);
	});

	it('keeps a constant slot count while walking a long range', () => {
		for (let page = 1; page <= 20; page++) {
			expect(pageWindow(page, 20)).toHaveLength(PAGE_WINDOW_SLOTS);
		}
	});

	it('always includes the first and last page, and the current page', () => {
		for (let page = 1; page <= 20; page++) {
			const slots = pageWindow(page, 20);
			expect(slots[0]).toBe(1);
			expect(slots[slots.length - 1]).toBe(20);
			expect(slots).toContain(page);
		}
	});

	it('never puts two ellipses side by side or hides a single page behind one', () => {
		for (let total = 8; total <= 30; total++) {
			for (let page = 1; page <= total; page++) {
				const slots = pageWindow(page, total);
				for (let i = 1; i < slots.length; i++) {
					const prev = slots[i - 1];
					const next = slots[i];
					if (next === 'ellipsis') {
						expect(prev).not.toBe('ellipsis');
					} else if (typeof prev === 'number') {
						expect(next).toBe(prev + 1);
					}
				}
			}
		}
	});

	it('clamps an out-of-range current page', () => {
		expect(pageWindow(0, 20)).toEqual(pageWindow(1, 20));
		expect(pageWindow(99, 20)).toEqual(pageWindow(20, 20));
	});

	it('widens on request and refuses to go below the stable minimum', () => {
		expect(pageWindow(10, 30, 9)).toEqual([1, 'ellipsis', 8, 9, 10, 11, 12, 'ellipsis', 30]);
		expect(pageWindow(10, 30, 3)).toEqual(pageWindow(10, 30));
	});
});
