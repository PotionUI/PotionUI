import { describe, it, expect } from 'vitest';
import { canGoNext, canGoPrev, clampPage, pageCount } from './pager';

describe('pageCount', () => {
	it('rounds up and never goes below one page', () => {
		expect(pageCount(101, 25)).toBe(5);
		expect(pageCount(0, 25)).toBe(1);
		expect(pageCount(25, 25)).toBe(1);
	});

	it('falls back to a single page for a non-positive page size', () => {
		expect(pageCount(100, 0)).toBe(1);
		expect(pageCount(100, -5)).toBe(1);
	});
});

describe('clampPage', () => {
	it('clamps below 1 and above the page count', () => {
		expect(clampPage(0, 5)).toBe(1);
		expect(clampPage(-3, 5)).toBe(1);
		expect(clampPage(9, 5)).toBe(5);
	});

	it('passes through an in-range page', () => {
		expect(clampPage(3, 5)).toBe(3);
	});
});

describe('canGoPrev / canGoNext', () => {
	it('canGoPrev is false only on page 1', () => {
		expect(canGoPrev(1)).toBe(false);
		expect(canGoPrev(2)).toBe(true);
	});

	it('canGoNext is false only on the last page', () => {
		expect(canGoNext(5, 5)).toBe(false);
		expect(canGoNext(4, 5)).toBe(true);
	});
});
