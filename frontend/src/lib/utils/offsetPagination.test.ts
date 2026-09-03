import { describe, it, expect } from 'vitest';
import { currentPageFromOffset, totalPagesFromCount, offsetForPage } from './offsetPagination';

describe('currentPageFromOffset', () => {
	it('is page 1 at offset 0', () => {
		expect(currentPageFromOffset(0, 20)).toBe(1);
	});

	it('advances one page per limit-sized offset step', () => {
		expect(currentPageFromOffset(20, 20)).toBe(2);
		expect(currentPageFromOffset(40, 20)).toBe(3);
	});

	it('floors a mid-page offset onto the page that contains it', () => {
		expect(currentPageFromOffset(25, 20)).toBe(2);
	});
});

describe('totalPagesFromCount', () => {
	it('rounds up a partial last page', () => {
		expect(totalPagesFromCount(97, 20)).toBe(5); // 4 full pages + 17
	});

	it('is exact for a multiple of the page size', () => {
		expect(totalPagesFromCount(40, 20)).toBe(2);
	});

	it('is at least 1 for an empty list, never 0', () => {
		expect(totalPagesFromCount(0, 20)).toBe(1);
	});
});

describe('offsetForPage', () => {
	it('is 0 for page 1', () => {
		expect(offsetForPage(1, 20)).toBe(0);
	});

	it('is (page-1)*limit for later pages', () => {
		expect(offsetForPage(3, 20)).toBe(40);
	});

	it('clamps a page below 1 to offset 0', () => {
		expect(offsetForPage(0, 20)).toBe(0);
		expect(offsetForPage(-5, 20)).toBe(0);
	});

	it('round-trips with currentPageFromOffset', () => {
		const page = 4;
		const limit = 20;
		expect(currentPageFromOffset(offsetForPage(page, limit), limit)).toBe(page);
	});
});
