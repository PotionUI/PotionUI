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
