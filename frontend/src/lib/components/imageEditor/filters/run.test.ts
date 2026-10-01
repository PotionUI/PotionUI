import { describe, expect, it } from 'vitest';
import {
	BUILTIN_FILTERS,
	defaultFilterValues,
	grayscaleFilter,
	invertFilter,
	toneFilter
} from './builtin';
import { activeSteps, runFilters } from './run';
import type { PaintFilter, PixelBuffer } from '../types';

function buffer(...pixels: number[][]): PixelBuffer {
	return { width: pixels.length, height: 1, data: new Uint8ClampedArray(pixels.flat()) };
}

describe('builtin filters', () => {
	it('start inactive with neutral values', () => {
		for (const filter of BUILTIN_FILTERS) {
			expect(filter.active(defaultFilterValues(filter))).toBe(false);
		}
	});

	it('activate once a value moves or a toggle flips', () => {
		expect(toneFilter.active({ ...defaultFilterValues(toneFilter), hue: 10 })).toBe(true);
		expect(invertFilter.active({ on: true })).toBe(true);
	});

	it('do not mutate the buffer they were given', () => {
		const input = buffer([10, 20, 30, 255]);
		const copy = input.data.slice();
		invertFilter.apply(input, { on: true });
		expect(Array.from(input.data)).toEqual(Array.from(copy));
	});
});

describe('runFilters', () => {
	it('chains active filters in order', async () => {
		const result = await runFilters(buffer([255, 0, 0, 255]), [
			{ filter: invertFilter, values: { on: true } },
			{ filter: grayscaleFilter, values: { on: true } }
		]);
		expect(result.data[0]).toBe(result.data[1]);
		expect(result.data[1]).toBe(result.data[2]);
		expect(result.data[0]).toBeGreaterThan(100);
	});

	it('skips inactive filters and returns an untouched copy', async () => {
		const base = buffer([1, 2, 3, 255]);
		const result = await runFilters(base, [{ filter: invertFilter, values: { on: false } }]);
		expect(Array.from(result.data)).toEqual([1, 2, 3, 255]);
		expect(result.data).not.toBe(base.data);
	});

	it('keeps pixels outside the mask even for filters that ignore it', async () => {
		const result = await runFilters(
			buffer([0, 0, 0, 255], [0, 0, 0, 255]),
			[{ filter: invertFilter, values: { on: true } }],
			new Uint8ClampedArray([255, 0])
		);
		expect(Array.from(result.data)).toEqual([255, 255, 255, 255, 0, 0, 0, 255]);
	});

	it('awaits asynchronous filters from plugins', async () => {
		const brighten: PaintFilter = {
			id: 'brighten',
			label: 'Brighten',
			params: [],
			toggle: true,
			active: () => true,
			async apply(image) {
				const data = new Uint8ClampedArray(image.data);
				data[0] = 200;
				return { ...image, data };
			}
		};
		const result = await runFilters(buffer([0, 0, 0, 255]), [{ filter: brighten, values: {} }]);
		expect(result.data[0]).toBe(200);
	});

	it('lists only the steps that will change anything', () => {
		const steps = [
			{ filter: invertFilter, values: { on: false } },
			{ filter: grayscaleFilter, values: { on: true } }
		];
		expect(activeSteps(steps).map((s) => s.filter.id)).toEqual(['grayscale']);
	});
});
