import type { PixelBuffer, Rect } from './types';

export interface Patch {
	x: number;
	y: number;
	width: number;
	height: number;
	before: Uint8ClampedArray;
	after: Uint8ClampedArray;
}

export function createPatch(
	rect: Rect,
	before: Uint8ClampedArray,
	after: Uint8ClampedArray
): Patch | null {
	const { width, height } = rect;
	let minX = width;
	let minY = height;
	let maxX = -1;
	let maxY = -1;

	for (let y = 0; y < height; y++) {
		const row = y * width * 4;
		for (let x = 0; x < width; x++) {
			const i = row + x * 4;
			if (
				before[i] !== after[i] ||
				before[i + 1] !== after[i + 1] ||
				before[i + 2] !== after[i + 2] ||
				before[i + 3] !== after[i + 3]
			) {
				if (x < minX) minX = x;
				if (x > maxX) maxX = x;
				if (y < minY) minY = y;
				if (y > maxY) maxY = y;
			}
		}
	}

	if (maxX < 0) return null;

	const w = maxX - minX + 1;
	const h = maxY - minY + 1;
	const trimmedBefore = new Uint8ClampedArray(w * h * 4);
	const trimmedAfter = new Uint8ClampedArray(w * h * 4);

	for (let y = 0; y < h; y++) {
		const from = ((minY + y) * width + minX) * 4;
		const to = y * w * 4;
		trimmedBefore.set(before.subarray(from, from + w * 4), to);
		trimmedAfter.set(after.subarray(from, from + w * 4), to);
	}

	return {
		x: Math.round(rect.x) + minX,
		y: Math.round(rect.y) + minY,
		width: w,
		height: h,
		before: trimmedBefore,
		after: trimmedAfter
	};
}

export function patchBytes(patch: Patch): number {
	return patch.before.byteLength + patch.after.byteLength;
}

export function writePatch(target: PixelBuffer, patch: Patch, side: 'before' | 'after'): void {
	const source = patch[side];
	for (let y = 0; y < patch.height; y++) {
		const ty = patch.y + y;
		if (ty < 0 || ty >= target.height) continue;
		const from = y * patch.width * 4;
		const to = (ty * target.width + patch.x) * 4;
		const x0 = Math.max(0, -patch.x);
		const x1 = Math.min(patch.width, target.width - patch.x);
		if (x1 <= x0) continue;
		target.data.set(source.subarray(from + x0 * 4, from + x1 * 4), to + x0 * 4);
	}
}
