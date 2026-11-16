import { describe, expect, it } from 'vitest';
import type { PixelBuffer } from '../types';
import { floodFill } from './floodFill';

type Pixel = [number, number, number, number];

function solid(width: number, height: number, rgba: Pixel): PixelBuffer {
	const data = new Uint8ClampedArray(width * height * 4);
	for (let i = 0; i < width * height; i++) data.set(rgba, i * 4);
	return { width, height, data };
}

function paint(buf: PixelBuffer, x: number, y: number, rgba: Pixel) {
	buf.data.set(rgba, (y * buf.width + x) * 4);
}

function at(buf: PixelBuffer, x: number, y: number) {
	const i = (y * buf.width + x) * 4;
	return Array.from(buf.data.slice(i, i + 4));
}

const white: Pixel = [255, 255, 255, 255];
const black: Pixel = [0, 0, 0, 255];
const red: Pixel = [255, 0, 0, 255];

describe('floodFill', () => {
	it('fills a connected region and reports its bounds', () => {
		const buf = solid(6, 6, white);
		for (let y = 0; y < 6; y++) paint(buf, 3, y, black);

		const rect = floodFill(buf, 0, 0, red, 0);
		expect(rect).toEqual({ x: 0, y: 0, width: 3, height: 6 });
		expect(at(buf, 2, 5)).toEqual(red);
		expect(at(buf, 3, 0)).toEqual(black);
		expect(at(buf, 5, 5)).toEqual(white);
	});

	it('does not leak diagonally through a one-pixel wall', () => {
		const buf = solid(4, 4, white);
		paint(buf, 1, 0, black);
		paint(buf, 0, 1, black);
		floodFill(buf, 0, 0, red, 0);
		expect(at(buf, 0, 0)).toEqual(red);
		expect(at(buf, 1, 1)).toEqual(white);
	});

	it('widens the region with tolerance', () => {
		const strict = solid(4, 1, white);
		paint(strict, 2, 0, [250, 250, 250, 255]);
		paint(strict, 3, 0, [250, 250, 250, 255]);
		floodFill(strict, 0, 0, red, 0);
		expect(at(strict, 2, 0)).toEqual([250, 250, 250, 255]);

		const loose = solid(4, 1, white);
		paint(loose, 2, 0, [250, 250, 250, 255]);
		paint(loose, 3, 0, [250, 250, 250, 255]);
		floodFill(loose, 0, 0, red, 5);
		expect(at(loose, 3, 0)).toEqual(red);
	});

	it('fills everything at 100 percent tolerance', () => {
		const buf = solid(3, 3, white);
		paint(buf, 1, 1, black);
		floodFill(buf, 0, 0, red, 100);
		expect(at(buf, 1, 1)).toEqual(red);
	});

	it('does nothing when the target already has the fill colour', () => {
		const buf = solid(3, 3, red);
		expect(floodFill(buf, 1, 1, red, 0)).toBeNull();
	});

	it('does nothing when the start point is outside the buffer', () => {
		const buf = solid(3, 3, white);
		expect(floodFill(buf, -1, 0, red, 0)).toBeNull();
		expect(floodFill(buf, 0, 3, red, 0)).toBeNull();
	});

	it('only writes inside the mask but still measures the region from the buffer', () => {
		const buf = solid(4, 1, white);
		const mask = new Uint8ClampedArray([255, 255, 0, 0]);
		const rect = floodFill(buf, 0, 0, red, 0, mask);
		expect(rect).toEqual({ x: 0, y: 0, width: 2, height: 1 });
		expect(at(buf, 1, 0)).toEqual(red);
		expect(at(buf, 2, 0)).toEqual(white);
	});

	it('fills transparent regions bounded by opaque pixels', () => {
		const buf = solid(3, 3, [0, 0, 0, 0]);
		paint(buf, 1, 1, black);
		floodFill(buf, 0, 0, red, 0);
		expect(at(buf, 2, 2)).toEqual(red);
		expect(at(buf, 1, 1)).toEqual(black);
	});

	it('handles large regions without exhausting the stack', () => {
		const buf = solid(512, 512, white);
		const rect = floodFill(buf, 0, 0, red, 0);
		expect(rect).toEqual({ x: 0, y: 0, width: 512, height: 512 });
	});
});
