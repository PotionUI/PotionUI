import { describe, expect, it } from 'vitest';
import { createPatch, patchBytes, writePatch } from './patch';
import type { PixelBuffer } from './types';

function buffer(width: number, height: number, fill = 0): PixelBuffer {
	return {
		width,
		height,
		data: new Uint8ClampedArray(width * height * 4).fill(fill)
	};
}

function setPixel(buf: PixelBuffer, x: number, y: number, value: number) {
	const i = (y * buf.width + x) * 4;
	buf.data[i] = value;
	buf.data[i + 1] = value;
	buf.data[i + 2] = value;
	buf.data[i + 3] = 255;
}

function slice(buf: PixelBuffer, x: number, y: number, w: number, h: number) {
	const out = new Uint8ClampedArray(w * h * 4);
	for (let row = 0; row < h; row++) {
		const from = ((y + row) * buf.width + x) * 4;
		out.set(buf.data.subarray(from, from + w * 4), row * w * 4);
	}
	return out;
}

describe('createPatch', () => {
	it('returns null when nothing changed', () => {
		const data = new Uint8ClampedArray(4 * 4 * 4);
		expect(createPatch({ x: 0, y: 0, width: 4, height: 4 }, data, data.slice())).toBeNull();
	});

	it('trims to the changed pixels and offsets into document space', () => {
		const before = buffer(10, 10);
		const after = buffer(10, 10);
		setPixel(after, 4, 5, 200);
		setPixel(after, 6, 7, 100);

		const rect = { x: 2, y: 3, width: 8, height: 7 };
		const patch = createPatch(rect, slice(before, 2, 3, 8, 7), slice(after, 2, 3, 8, 7));

		expect(patch).not.toBeNull();
		expect(patch?.x).toBe(4);
		expect(patch?.y).toBe(5);
		expect(patch?.width).toBe(3);
		expect(patch?.height).toBe(3);
		expect(patchBytes(patch!)).toBe(3 * 3 * 4 * 2);
	});

	it('restores and reapplies exactly the edited pixels', () => {
		const before = buffer(10, 10, 30);
		const after = buffer(10, 10, 30);
		setPixel(after, 4, 5, 200);
		setPixel(after, 6, 7, 100);
		const patch = createPatch(
			{ x: 0, y: 0, width: 10, height: 10 },
			before.data.slice(),
			after.data.slice()
		)!;

		const target = buffer(10, 10, 30);
		writePatch(target, patch, 'after');
		expect(Array.from(target.data)).toEqual(Array.from(after.data));

		writePatch(target, patch, 'before');
		expect(Array.from(target.data)).toEqual(Array.from(before.data));
	});

	it('ignores parts of a patch that fall outside the target', () => {
		const target = buffer(4, 4, 9);
		const patch = {
			x: 3,
			y: 3,
			width: 3,
			height: 3,
			before: new Uint8ClampedArray(36),
			after: new Uint8ClampedArray(36).fill(77)
		};
		writePatch(target, patch, 'after');
		expect(target.data[(3 * 4 + 3) * 4]).toBe(77);
		expect(target.data[(2 * 4 + 3) * 4]).toBe(9);
	});
});
