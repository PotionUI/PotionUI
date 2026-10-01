import type { Point, Rect } from '../types';

export function rasterizePolygon(
	points: Point[],
	width: number,
	height: number
): Uint8ClampedArray {
	const mask = new Uint8ClampedArray(width * height);
	if (points.length < 3) return mask;

	for (let y = 0; y < height; y++) {
		const scan = y + 0.5;
		const crossings: number[] = [];
		for (let i = 0; i < points.length; i++) {
			const a = points[i];
			const b = points[(i + 1) % points.length];
			if ((a.y <= scan && b.y > scan) || (b.y <= scan && a.y > scan)) {
				crossings.push(a.x + ((scan - a.y) / (b.y - a.y)) * (b.x - a.x));
			}
		}
		crossings.sort((p, q) => p - q);
		for (let i = 0; i + 1 < crossings.length; i += 2) {
			const from = Math.max(0, Math.ceil(crossings[i] - 0.5));
			const to = Math.min(width, Math.ceil(crossings[i + 1] - 0.5));
			for (let x = from; x < to; x++) mask[y * width + x] = 255;
		}
	}
	return mask;
}

export function rectMask(rect: Rect, width: number, height: number): Uint8ClampedArray {
	const mask = new Uint8ClampedArray(width * height);
	const x0 = Math.max(0, Math.round(rect.x));
	const y0 = Math.max(0, Math.round(rect.y));
	const x1 = Math.min(width, Math.round(rect.x + rect.width));
	const y1 = Math.min(height, Math.round(rect.y + rect.height));
	for (let y = y0; y < y1; y++) {
		mask.fill(255, y * width + x0, y * width + Math.max(x0, x1));
	}
	return mask;
}

export function maskBounds(mask: Uint8ClampedArray, width: number, height: number): Rect | null {
	let minX = width;
	let minY = height;
	let maxX = -1;
	let maxY = -1;
	for (let y = 0; y < height; y++) {
		for (let x = 0; x < width; x++) {
			if (mask[y * width + x] >= 128) {
				if (x < minX) minX = x;
				if (x > maxX) maxX = x;
				if (y < minY) minY = y;
				if (y > maxY) maxY = y;
			}
		}
	}
	if (maxX < 0) return null;
	return { x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1 };
}

export function invertMask(mask: Uint8ClampedArray): Uint8ClampedArray {
	const out = new Uint8ClampedArray(mask.length);
	for (let i = 0; i < mask.length; i++) out[i] = 255 - mask[i];
	return out;
}

export function combineMasks(
	a: Uint8ClampedArray,
	b: Uint8ClampedArray,
	op: 'union' | 'subtract' | 'intersect'
): Uint8ClampedArray {
	const out = new Uint8ClampedArray(a.length);
	for (let i = 0; i < a.length; i++) {
		if (op === 'union') out[i] = Math.max(a[i], b[i]);
		else if (op === 'subtract') out[i] = Math.min(a[i], 255 - b[i]);
		else out[i] = Math.min(a[i], b[i]);
	}
	return out;
}

export function maskCoverage(mask: Uint8ClampedArray): number {
	let count = 0;
	for (let i = 0; i < mask.length; i++) if (mask[i] >= 128) count++;
	return count;
}
