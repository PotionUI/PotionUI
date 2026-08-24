import { describe, it, expect } from 'vitest';
import { layoutJustifiedRows, clampAspect } from './justifiedLayout';

describe('clampAspect', () => {
	it('passes normal aspects through', () => {
		expect(clampAspect(1.5)).toBe(1.5);
	});

	it('clamps extreme panoramas and strips', () => {
		expect(clampAspect(10)).toBe(2.6);
		expect(clampAspect(0.1)).toBe(0.45);
	});

	it('falls back to square for invalid input', () => {
		expect(clampAspect(0)).toBe(1);
		expect(clampAspect(NaN)).toBe(1);
		expect(clampAspect(-2)).toBe(1);
	});
});

describe('layoutJustifiedRows', () => {
	const items = (aspects: number[]) => aspects.map((aspect, i) => ({ item: i, aspect }));

	it('returns empty for no items or zero width', () => {
		expect(layoutJustifiedRows([], 1000, 220, 12)).toEqual([]);
		expect(layoutJustifiedRows(items([1, 1]), 0, 220, 12)).toEqual([]);
	});

	it('packs every justified row exactly to the container width', () => {
		const rows = layoutJustifiedRows(items([1.78, 1, 0.75, 1.33, 1, 0.56, 1.5, 1]), 1200, 220, 12);
		expect(rows.length).toBeGreaterThan(1);
		// All rows except possibly the last must fill the width exactly.
		for (const row of rows.slice(0, -1)) {
			const width = row.reduce((sum, box) => sum + box.width, 0) + 12 * (row.length - 1);
			expect(width).toBeCloseTo(1200, 3);
		}
	});

	it('keeps a uniform height within each row and preserves aspect ratios', () => {
		const rows = layoutJustifiedRows(items([1.78, 0.75, 1]), 900, 220, 12);
		for (const row of rows) {
			const heights = new Set(row.map((box) => Math.round(box.height * 1000)));
			expect(heights.size).toBe(1);
			for (const box of row) {
				expect(box.width / box.height).toBeGreaterThan(0.4);
				expect(box.width / box.height).toBeLessThan(2.7);
			}
		}
	});

	it('does not stretch a sparse last row beyond the target height', () => {
		const rows = layoutJustifiedRows(items([1]), 1200, 220, 12);
		expect(rows).toHaveLength(1);
		expect(rows[0][0].height).toBeLessThanOrEqual(220);
	});

	it('keeps item order across rows', () => {
		const rows = layoutJustifiedRows(items([1, 1, 1, 1, 1, 1, 1]), 600, 220, 12);
		const flat = rows.flat().map((box) => box.item);
		expect(flat).toEqual([0, 1, 2, 3, 4, 5, 6]);
	});
});
