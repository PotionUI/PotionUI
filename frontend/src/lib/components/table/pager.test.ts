import { describe, it, expect } from 'vitest';
import { clampPage, pageCount } from './pager';

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
