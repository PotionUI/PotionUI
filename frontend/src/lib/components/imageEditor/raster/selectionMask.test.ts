import { describe, expect, it } from 'vitest';
import {
	combineMasks,
	invertMask,
	maskBounds,
	maskCoverage,
	rasterizePolygon,
	rectMask
} from './selectionMask';

function row(mask: Uint8ClampedArray, width: number, y: number) {
	return Array.from(mask.slice(y * width, (y + 1) * width));
}

describe('rasterizePolygon', () => {
	it('fills an axis-aligned square exactly', () => {
		const mask = rasterizePolygon(
			[
				{ x: 1, y: 1 },
				{ x: 4, y: 1 },
				{ x: 4, y: 3 },
				{ x: 1, y: 3 }
			],
			6,
			5
		);
		expect(row(mask, 6, 0)).toEqual([0, 0, 0, 0, 0, 0]);
		expect(row(mask, 6, 1)).toEqual([0, 255, 255, 255, 0, 0]);
		expect(row(mask, 6, 2)).toEqual([0, 255, 255, 255, 0, 0]);
		expect(row(mask, 6, 3)).toEqual([0, 0, 0, 0, 0, 0]);
	});

	it('fills a triangle narrower toward its apex', () => {
		const mask = rasterizePolygon(
			[
				{ x: 0, y: 0 },
				{ x: 8, y: 0 },
				{ x: 0, y: 8 }
			],
			8,
			8
		);
		const widths = Array.from({ length: 8 }, (_, y) => row(mask, 8, y).filter((v) => v).length);
		for (let y = 1; y < 8; y++) expect(widths[y]).toBeLessThanOrEqual(widths[y - 1]);
		expect(widths[0]).toBeGreaterThan(5);
		expect(widths[7]).toBeLessThanOrEqual(1);
	});

	it('leaves a hole where an even-odd path overlaps itself', () => {
		const path = [
			{ x: 0, y: 0 },
			{ x: 10, y: 0 },
			{ x: 10, y: 10 },
			{ x: 0, y: 10 },
			{ x: 0, y: 0 },
			{ x: 3, y: 3 },
			{ x: 7, y: 3 },
			{ x: 7, y: 7 },
			{ x: 3, y: 7 },
			{ x: 3, y: 3 }
		];
		const mask = rasterizePolygon(path, 10, 10);
		expect(mask[5 * 10 + 5]).toBe(0);
		expect(mask[1 * 10 + 1]).toBe(255);
	});

	it('returns an empty mask for fewer than three points', () => {
		const mask = rasterizePolygon(
			[
				{ x: 0, y: 0 },
				{ x: 5, y: 5 }
			],
			6,
			6
		);
		expect(maskCoverage(mask)).toBe(0);
	});

	it('clips to the mask size', () => {
		const mask = rasterizePolygon(
			[
				{ x: -5, y: -5 },
				{ x: 50, y: -5 },
				{ x: 50, y: 50 },
				{ x: -5, y: 50 }
			],
			4,
			4
		);
		expect(maskCoverage(mask)).toBe(16);
	});
});

describe('mask helpers', () => {
	it('builds a rect mask clipped to the canvas', () => {
		const mask = rectMask({ x: -2, y: 1, width: 5, height: 2 }, 4, 4);
		expect(row(mask, 4, 1)).toEqual([255, 255, 255, 0]);
		expect(row(mask, 4, 2)).toEqual([255, 255, 255, 0]);
		expect(row(mask, 4, 0)).toEqual([0, 0, 0, 0]);
	});

	it('finds the bounds of the selected area', () => {
		const mask = rectMask({ x: 2, y: 3, width: 4, height: 2 }, 10, 10);
		expect(maskBounds(mask, 10, 10)).toEqual({
			x: 2,
			y: 3,
			width: 4,
			height: 2
		});
		expect(maskBounds(new Uint8ClampedArray(100), 10, 10)).toBeNull();
	});

	it('inverts a mask', () => {
		const mask = rectMask({ x: 0, y: 0, width: 2, height: 4 }, 4, 4);
		const inverted = invertMask(mask);
		expect(maskCoverage(inverted)).toBe(8);
		expect(inverted[0]).toBe(0);
		expect(inverted[3]).toBe(255);
	});

	it('combines masks by union, subtract and intersect', () => {
		const a = rectMask({ x: 0, y: 0, width: 3, height: 1 }, 4, 1);
		const b = rectMask({ x: 2, y: 0, width: 2, height: 1 }, 4, 1);
		expect(Array.from(combineMasks(a, b, 'union'))).toEqual([255, 255, 255, 255]);
		expect(Array.from(combineMasks(a, b, 'subtract'))).toEqual([255, 255, 0, 0]);
		expect(Array.from(combineMasks(a, b, 'intersect'))).toEqual([0, 0, 255, 0]);
	});
});
