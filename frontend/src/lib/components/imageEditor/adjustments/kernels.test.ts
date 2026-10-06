import { describe, expect, it } from 'vitest';
import { NEUTRAL_TONE, applyGrayscale, applyInvert, applyTone, isNeutralTone } from './kernels';

function pixels(...values: number[][]): Uint8ClampedArray {
	return new Uint8ClampedArray(values.flat());
}

describe('applyTone', () => {
	it('is the identity at neutral settings', () => {
		const data = pixels([10, 120, 250, 255], [200, 30, 90, 128]);
		const copy = data.slice();
		applyTone(data, NEUTRAL_TONE);
		for (let i = 0; i < data.length; i++)
			expect(Math.abs(data[i] - copy[i])).toBeLessThanOrEqual(1);
	});

	it('scales brightness and clamps at white', () => {
		const data = pixels([100, 150, 200, 255]);
		applyTone(data, { ...NEUTRAL_TONE, brightness: 50 });
		expect(data[0]).toBe(150);
		expect(data[1]).toBe(225);
		expect(data[2]).toBe(255);
		expect(data[3]).toBe(255);
	});

	it('pushes values away from mid grey with contrast', () => {
		const data = pixels([100, 128, 200, 255]);
		applyTone(data, { ...NEUTRAL_TONE, contrast: 100 });
		expect(data[0]).toBeLessThan(100);
		expect(data[2]).toBeGreaterThan(200);
	});

	it('removes all colour at minus 100 saturation', () => {
		const data = pixels([255, 0, 0, 255]);
		applyTone(data, { ...NEUTRAL_TONE, saturation: -100 });
		expect(Math.abs(data[0] - data[1])).toBeLessThanOrEqual(1);
		expect(Math.abs(data[1] - data[2])).toBeLessThanOrEqual(1);
	});

	it('rotates hue by 120 degrees from red toward green', () => {
		const data = pixels([255, 0, 0, 255]);
		applyTone(data, { ...NEUTRAL_TONE, hue: 120 });
		expect(data[1]).toBeGreaterThan(data[0]);
		expect(data[1]).toBeGreaterThan(data[2]);
	});

	it('leaves alpha alone', () => {
		const data = pixels([10, 20, 30, 77]);
		applyTone(data, { brightness: 40, contrast: 20, saturation: 30, hue: 90 });
		expect(data[3]).toBe(77);
	});

	it('changes only pixels the mask covers and blends partial coverage', () => {
		const full = pixels([100, 100, 100, 255]);
		applyTone(full, { ...NEUTRAL_TONE, brightness: 100 });

		const masked = pixels([100, 100, 100, 255], [100, 100, 100, 255], [100, 100, 100, 255]);
		applyTone(masked, { ...NEUTRAL_TONE, brightness: 100 }, new Uint8ClampedArray([255, 0, 128]));
		expect(masked[0]).toBe(full[0]);
		expect(masked[4]).toBe(100);
		expect(masked[8]).toBeGreaterThan(100);
		expect(masked[8]).toBeLessThan(full[0]);
	});
});

describe('isNeutralTone', () => {
	it('detects the untouched state', () => {
		expect(isNeutralTone(NEUTRAL_TONE)).toBe(true);
		expect(isNeutralTone({ ...NEUTRAL_TONE, hue: 1 })).toBe(false);
	});
});

describe('applyInvert', () => {
	it('flips colour channels and keeps alpha', () => {
		const data = pixels([0, 100, 255, 90]);
		applyInvert(data);
		expect(Array.from(data)).toEqual([255, 155, 0, 90]);
	});

	it('is its own inverse', () => {
		const data = pixels([12, 34, 56, 255], [200, 210, 220, 255]);
		const copy = data.slice();
		applyInvert(data);
		applyInvert(data);
		expect(Array.from(data)).toEqual(Array.from(copy));
	});

	it('respects the mask', () => {
		const data = pixels([0, 0, 0, 255], [0, 0, 0, 255]);
		applyInvert(data, new Uint8ClampedArray([255, 0]));
		expect(Array.from(data)).toEqual([255, 255, 255, 255, 0, 0, 0, 255]);
	});
});

describe('applyGrayscale', () => {
	it('makes the channels equal using luma weights', () => {
		const data = pixels([255, 0, 0, 255], [0, 255, 0, 255]);
		applyGrayscale(data);
		expect(data[0]).toBe(data[1]);
		expect(data[1]).toBe(data[2]);
		expect(data[4]).toBeGreaterThan(data[0]);
	});

	it('leaves already grey pixels alone', () => {
		const data = pixels([90, 90, 90, 255]);
		applyGrayscale(data);
		expect(Array.from(data)).toEqual([90, 90, 90, 255]);
	});
});
