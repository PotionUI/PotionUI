import { describe, expect, it } from 'vitest';
import { blendByMask, clearByMask, extractByMask, sampleMaskForLayer } from './layerMask';

describe('sampleMaskForLayer', () => {
	it('reads the document mask through the layer offset', () => {
		const mask = new Uint8ClampedArray(16);
		mask[1 * 4 + 1] = 255;
		mask[2 * 4 + 2] = 255;
		const sampled = sampleMaskForLayer(mask, 4, 4, 2, 2, 1, 1);
		expect(Array.from(sampled)).toEqual([255, 0, 0, 255]);
	});

	it('treats everything outside the document as unselected', () => {
		const mask = new Uint8ClampedArray(4).fill(255);
		const sampled = sampleMaskForLayer(mask, 2, 2, 3, 3, -1, -1);
		expect(Array.from(sampled)).toEqual([0, 0, 0, 0, 255, 255, 0, 255, 255]);
	});
});

describe('clearByMask', () => {
	it('zeroes alpha under full coverage and scales it under partial coverage', () => {
		const data = new Uint8ClampedArray([1, 2, 3, 200, 1, 2, 3, 200, 1, 2, 3, 200]);
		clearByMask(data, new Uint8ClampedArray([255, 128, 0]));
		expect(data[3]).toBe(0);
		expect(data[7]).toBe(100);
		expect(data[11]).toBe(200);
		expect(data[0]).toBe(1);
	});
});

describe('extractByMask', () => {
	it('keeps selected pixels and drops the rest without touching the source', () => {
		const data = new Uint8ClampedArray([9, 9, 9, 255, 8, 8, 8, 255]);
		const piece = extractByMask(data, new Uint8ClampedArray([255, 0]));
		expect(Array.from(piece)).toEqual([9, 9, 9, 255, 8, 8, 8, 0]);
		expect(data[7]).toBe(255);
	});
});

describe('blendByMask', () => {
	it('takes filtered pixels where fully covered and blends partial coverage', () => {
		const original = new Uint8ClampedArray([0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255]);
		const filtered = new Uint8ClampedArray([
			200, 200, 200, 255, 200, 200, 200, 255, 200, 200, 200, 255
		]);
		const out = blendByMask(original, filtered, new Uint8ClampedArray([255, 0, 128]));
		expect(out[0]).toBe(200);
		expect(out[4]).toBe(0);
		expect(out[8]).toBeGreaterThan(90);
		expect(out[8]).toBeLessThan(110);
	});
});
