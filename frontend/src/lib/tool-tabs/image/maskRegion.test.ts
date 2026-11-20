import { describe, expect, it } from 'vitest';
import { alphaToMask } from './mask';
import { LASSO_MIN_STEP, closedShape, extendOutline, regionBounds, stampShape } from './maskRegion';

const TINT: [number, number, number] = [10, 20, 30];

function blank(width: number, height: number): Uint8ClampedArray {
	return new Uint8ClampedArray(width * height * 4);
}

function alphaAt(pixels: Uint8ClampedArray, width: number, x: number, y: number): number {
	return pixels[(y * width + x) * 4 + 3];
}

function lit(pixels: Uint8ClampedArray): number {
	let count = 0;
	for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 0) count += 1;
	return count;
}

const square = (x0: number, y0: number, x1: number, y1: number) => [
	{ x: x0, y: y0 },
	{ x: x1, y: y0 },
	{ x: x1, y: y1 },
	{ x: x0, y: y1 }
];

describe('lasso outline capture', () => {
	it('keeps a point only once it is a step away from the last one', () => {
		const points = [{ x: 0, y: 0 }];
		expect(extendOutline(points, { x: 0.5, y: 0.5 }, LASSO_MIN_STEP)).toBe(false);
		expect(extendOutline(points, { x: 3, y: 0 }, LASSO_MIN_STEP)).toBe(true);
		expect(points).toHaveLength(2);
	});

	it('closes only a shape of three or more points, so a click without a drag makes nothing', () => {
		expect(closedShape([{ x: 1, y: 1 }])).toBeNull();
		expect(closedShape([{ x: 1, y: 1 }, { x: 4, y: 4 }])).toBeNull();
		expect(closedShape(square(0, 0, 4, 4))).toHaveLength(4);
	});
});

describe('lasso shape into the mask', () => {
	it('fills a closed shape with opaque tint and leaves the rest clear', () => {
		const pixels = blank(10, 10);
		const changed = stampShape(pixels, 10, 10, square(2, 2, 6, 6), false, TINT);
		expect(changed).toBe(16);
		expect(Array.from(pixels.slice((2 * 10 + 2) * 4, (2 * 10 + 2) * 4 + 4))).toEqual([10, 20, 30, 255]);
		expect(alphaAt(pixels, 10, 5, 5)).toBe(255);
		expect(alphaAt(pixels, 10, 6, 6)).toBe(0);
		expect(alphaAt(pixels, 10, 1, 1)).toBe(0);
	});

	it('uses the even-odd fill rule, so the overlap of a self-intersecting outline stays unmarked', () => {
		const star = [0, 1, 2, 3, 4].map((index) => {
			const angle = ((index * 144 - 90) * Math.PI) / 180;
			return { x: 50 + 40 * Math.cos(angle), y: 50 + 40 * Math.sin(angle) };
		});
		const pixels = blank(100, 100);
		stampShape(pixels, 100, 100, star, false, TINT);
		expect(alphaAt(pixels, 100, 50, 50)).toBe(0);
		expect(alphaAt(pixels, 100, 50, 18)).toBe(255);
		expect(lit(pixels)).toBeGreaterThan(0);
	});

	it('marks nothing for a shape too small to cover a pixel centre', () => {
		const pixels = blank(10, 10);
		expect(stampShape(pixels, 10, 10, [{ x: 3.1, y: 3.1 }, { x: 3.3, y: 3.1 }, { x: 3.2, y: 3.3 }], false, TINT)).toBe(0);
		expect(lit(pixels)).toBe(0);
	});

	it('marks a three by three shape pixel for pixel', () => {
		const pixels = blank(10, 10);
		expect(stampShape(pixels, 10, 10, square(4, 4, 7, 7), false, TINT)).toBe(9);
	});

	it('clips a shape that runs outside the picture', () => {
		const pixels = blank(10, 10);
		const changed = stampShape(pixels, 10, 10, square(-5, -5, 3, 3), false, TINT);
		expect(changed).toBe(9);
		expect(alphaAt(pixels, 10, 0, 0)).toBe(255);
		expect(alphaAt(pixels, 10, 2, 2)).toBe(255);
		expect(alphaAt(pixels, 10, 3, 3)).toBe(0);
		const far = blank(10, 10);
		expect(stampShape(far, 10, 10, square(20, 20, 30, 30), false, TINT)).toBe(0);
	});

	it('adds to a brush stroke and subtracts from it, whatever order they come in', () => {
		const pixels = blank(12, 12);
		for (let x = 0; x < 12; x += 1) for (const y of [5, 6]) pixels.set([10, 20, 30, 255], (y * 12 + x) * 4);
		stampShape(pixels, 12, 12, square(8, 1, 11, 4), false, TINT);
		expect(alphaAt(pixels, 12, 9, 2)).toBe(255);
		expect(alphaAt(pixels, 12, 0, 5)).toBe(255);
		stampShape(pixels, 12, 12, square(0, 4, 4, 7), true, TINT);
		expect(alphaAt(pixels, 12, 0, 5)).toBe(0);
		expect(alphaAt(pixels, 12, 3, 6)).toBe(0);
		expect(alphaAt(pixels, 12, 5, 5)).toBe(255);
		expect(alphaAt(pixels, 12, 9, 2)).toBe(255);
		stampShape(pixels, 12, 12, square(0, 0, 12, 12), false, TINT);
		expect(lit(pixels)).toBe(144);
	});

	it('subtracting a shape from an empty mask changes nothing visible', () => {
		const pixels = blank(8, 8);
		stampShape(pixels, 8, 8, square(1, 1, 5, 5), true, TINT);
		expect(lit(pixels)).toBe(0);
	});
});

describe('marked area bounds', () => {
	it('is the box around everything above the stored cutoff', () => {
		const pixels = blank(10, 8);
		stampShape(pixels, 10, 8, square(2, 1, 6, 4), false, TINT);
		expect(regionBounds(pixels, 10, 8)).toEqual({ x: 2, y: 1, width: 4, height: 3 });
	});

	it('ignores the faint edge of a brush stroke and is null for an empty mask', () => {
		const pixels = blank(4, 4);
		pixels[3] = 100;
		expect(regionBounds(pixels, 4, 4)).toBeNull();
		pixels[3] = 128;
		expect(regionBounds(pixels, 4, 4)).toEqual({ x: 0, y: 0, width: 1, height: 1 });
	});
});

describe('a lasso mask exports like a brush mask', () => {
	it('leaves hard alpha values, so the stored cutoff makes pure white on opaque black', () => {
		const pixels = blank(10, 10);
		stampShape(pixels, 10, 10, square(2, 2, 6, 6), false, TINT);
		const out = new Uint8ClampedArray(pixels.length);
		const count = alphaToMask(pixels, out, 127);
		expect(count).toBe(16);
		const values = new Set<number>();
		for (let i = 0; i < out.length; i += 4) {
			values.add(out[i]);
			expect(out[i + 3]).toBe(255);
			expect(out[i + 1]).toBe(out[i]);
		}
		expect([...values].sort((a, b) => a - b)).toEqual([0, 255]);
		expect(out[(3 * 10 + 3) * 4]).toBe(255);
		expect(out[0]).toBe(0);
	});

	it('gives the same pixels at the default cutoff and the stored cutoff', () => {
		const pixels = blank(10, 10);
		stampShape(pixels, 10, 10, square(1, 1, 9, 9), false, TINT);
		const loose = new Uint8ClampedArray(pixels.length);
		const strict = new Uint8ClampedArray(pixels.length);
		alphaToMask(pixels, loose);
		alphaToMask(pixels, strict, 127);
		expect(Array.from(loose)).toEqual(Array.from(strict));
	});

	it('resolves the soft edge a brush leaves next to a lasso the same way on both sides of the cutoff', () => {
		const pixels = blank(6, 1);
		pixels[3] = 126;
		pixels[7] = 128;
		const out = new Uint8ClampedArray(pixels.length);
		alphaToMask(pixels, out, 127);
		expect([out[0], out[4]]).toEqual([0, 255]);
	});
});
