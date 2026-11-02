import { describe, expect, it } from 'vitest';
import { crc32, cropPlan, zipStore } from './crops';
import type { SliceOutput } from './model';

const bytes = (text: string) => new TextEncoder().encode(text);

describe('zip writer', () => {
	it('computes the standard CRC-32 check value', () => {
		expect(crc32(bytes('123456789'))).toBe(0xcbf43926);
	});

	it('stores two files with UTF-8 names, offsets and a matching directory', () => {
		const files = [
			{ name: 'mira-01.png', data: bytes('first') },
			{ name: 'mïra-ß-02.png', data: bytes('second file') }
		];
		const zip = zipStore(files);
		const view = new DataView(zip.buffer, zip.byteOffset, zip.byteLength);
		const decoder = new TextDecoder();

		const firstName = bytes(files[0].name);
		const secondName = bytes(files[1].name);
		expect(view.getUint32(0, true)).toBe(0x04034b50);
		expect(view.getUint16(6, true) & 0x0800).toBe(0x0800);
		expect(view.getUint16(8, true)).toBe(0);
		expect(view.getUint32(14, true)).toBe(crc32(files[0].data));
		expect(view.getUint32(18, true)).toBe(5);
		expect(decoder.decode(zip.subarray(30 + firstName.length, 30 + firstName.length + 5))).toBe('first');

		const second = 30 + firstName.length + 5;
		expect(view.getUint32(second, true)).toBe(0x04034b50);
		expect(view.getUint16(second + 6, true) & 0x0800).toBe(0x0800);
		expect(decoder.decode(zip.subarray(second + 30, second + 30 + secondName.length))).toBe('mïra-ß-02.png');
		expect(decoder.decode(zip.subarray(second + 30 + secondName.length, second + 30 + secondName.length + 11))).toBe('second file');

		const end = zip.length - 22;
		expect(view.getUint32(end, true)).toBe(0x06054b50);
		expect(view.getUint16(end + 8, true)).toBe(2);
		expect(view.getUint16(end + 10, true)).toBe(2);
		const directory = view.getUint32(end + 16, true);
		expect(directory).toBe(second + 30 + secondName.length + 11);
		expect(view.getUint32(directory, true)).toBe(0x02014b50);
		expect(view.getUint16(directory + 8, true) & 0x0800).toBe(0x0800);
		expect(view.getUint32(directory + 42, true)).toBe(0);
		const next = directory + 46 + firstName.length;
		expect(view.getUint32(next, true)).toBe(0x02014b50);
		expect(view.getUint32(next + 42, true)).toBe(second);
		expect(decoder.decode(zip.subarray(next + 46, next + 46 + secondName.length))).toBe('mïra-ß-02.png');
		expect(view.getUint32(end + 12, true)).toBe(end - directory);
	});
});

describe('crop geometry', () => {
	const cell = { id: 'c', x0: 0.25, y0: 0.5, x1: 0.75, y1: 0.75 };
	const logical = { width: 800, height: 400 };
	const output = (patch: Partial<SliceOutput> = {}): SliceOutput => ({ trim: false, tolerance: 12, padding: 0, aspect: 'keep', ...patch });

	it('maps the cell to source pixels and fills the canvas at full size', () => {
		const plan = cropPlan(null, cell, output(), logical, { width: 400, height: 200 }, 4096);
		expect(plan).toEqual({ width: 400, height: 100, src: { x: 100, y: 100, width: 200, height: 50 }, dst: { x: 0, y: 0, width: 400, height: 100 } });
	});

	it('centres the picture inside the padding', () => {
		const plan = cropPlan(null, cell, output({ padding: 20 }), logical, logical, 4096);
		expect(plan.width).toBe(440);
		expect(plan.height).toBe(140);
		expect(plan.dst).toEqual({ x: 20, y: 20, width: 400, height: 100 });
	});

	it('pads to a square or portrait aspect without cropping', () => {
		const square = cropPlan(null, cell, output({ aspect: 'square' }), logical, logical, 4096);
		expect([square.width, square.height]).toEqual([400, 400]);
		expect(square.dst).toEqual({ x: 0, y: 150, width: 400, height: 100 });
		const portrait = cropPlan(null, cell, output({ aspect: 'portrait' }), logical, logical, 4096);
		expect([portrait.width, portrait.height]).toEqual([400, 600]);
		expect(portrait.dst.y).toBe(250);
	});

	it('clamps the long side to maxSide and scales everything with it', () => {
		const plan = cropPlan(null, cell, output({ padding: 20 }), logical, logical, 220);
		expect(plan.width).toBe(220);
		expect(plan.height).toBe(70);
		expect(plan.dst).toEqual({ x: 10, y: 10, width: 200, height: 50 });
	});

	it('clamps a tall output by its height', () => {
		const plan = cropPlan(null, cell, output({ aspect: 'portrait' }), logical, logical, 300);
		expect([plan.width, plan.height]).toEqual([200, 300]);
		expect(plan.dst).toEqual({ x: 0, y: 125, width: 200, height: 50 });
	});
});
