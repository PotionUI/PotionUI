import { describe, expect, it } from 'vitest';
import { alphaPatch, applyAlphaPatch, trimPatches } from './maskHistory';

function frame(width: number, height: number, paint: Array<[number, number, number]> = []) {
	const alpha = new Uint8Array(width * height);
	for (const [x, y, value] of paint) alpha[y * width + x] = value;
	return alpha;
}

describe('alphaPatch', () => {
	it('returns null when nothing changed', () => {
		expect(alphaPatch(frame(4, 4), frame(4, 4), 4, 4)).toBeNull();
	});

	it('keeps only the changed rectangle', () => {
		const before = frame(8, 8);
		const after = frame(8, 8, [
			[2, 3, 255],
			[4, 5, 128]
		]);
		const patch = alphaPatch(before, after, 8, 8)!;
		expect(patch).toMatchObject({ x: 2, y: 3, width: 3, height: 3 });
		expect(patch.before.byteLength).toBe(9);
		expect(patch.after[0]).toBe(255);
	});

	it('walks a frame back and forth through the same patch', () => {
		const before = frame(8, 8, [[1, 1, 200]]);
		const after = frame(8, 8, [
			[1, 1, 200],
			[6, 2, 255],
			[5, 6, 90]
		]);
		const patch = alphaPatch(before, after, 8, 8)!;
		const live = Uint8Array.from(after);
		applyAlphaPatch(live, patch, 8, 'before');
		expect(live).toEqual(before);
		applyAlphaPatch(live, patch, 8, 'after');
		expect(live).toEqual(after);
	});

	it('holds far less than a full frame for a small stroke on a large mask', () => {
		const width = 4096;
		const height = 4096;
		const before = new Uint8Array(width * height);
		const after = Uint8Array.from(before);
		for (let y = 100; y < 140; y++) for (let x = 200; x < 260; x++) after[y * width + x] = 255;
		const patch = alphaPatch(before, after, width, height)!;
		expect(patch.before.byteLength + patch.after.byteLength).toBe(2 * 40 * 60);
	});
});

describe('trimPatches', () => {
	const patch = (bytes: number) => ({ x: 0, y: 0, width: bytes, height: 1, before: new Uint8Array(bytes), after: new Uint8Array(bytes) });

	it('drops the oldest patches past the byte budget but keeps the newest', () => {
		const list = [patch(40), patch(40), patch(40)];
		expect(trimPatches(list, 170)).toHaveLength(2);
		expect(trimPatches([patch(500)], 10)).toHaveLength(1);
	});

	it('caps by step count', () => {
		expect(trimPatches(Array.from({ length: 5 }, () => patch(1)), 1000, 3)).toHaveLength(3);
	});
});
