import { rasterizePolygon } from '$lib/paint/raster/selectionMask';
import type { Point, Rect } from '$lib/paint/types';
import { STORED_MASK_CUTOFF, type Rgb } from './mask';

export const LASSO_MIN_STEP = 1.5;

export function extendOutline(points: Point[], next: Point, minStep: number = LASSO_MIN_STEP): boolean {
	const last = points[points.length - 1];
	if (last && Math.hypot(next.x - last.x, next.y - last.y) < minStep) return false;
	points.push(next);
	return true;
}

export function closedShape(points: readonly Point[]): Point[] | null {
	return points.length >= 3 ? points.map((point) => ({ x: point.x, y: point.y })) : null;
}

export function stampShape(
	pixels: Uint8ClampedArray,
	width: number,
	height: number,
	path: readonly Point[],
	subtract: boolean,
	tint: Rgb
): number {
	const covered = rasterizePolygon([...path], width, height);
	let changed = 0;
	for (let index = 0; index < covered.length; index += 1) {
		if (covered[index] === 0) continue;
		const at = index * 4;
		pixels[at] = subtract ? 0 : tint[0];
		pixels[at + 1] = subtract ? 0 : tint[1];
		pixels[at + 2] = subtract ? 0 : tint[2];
		pixels[at + 3] = subtract ? 0 : 255;
		changed += 1;
	}
	return changed;
}

export function regionBounds(pixels: Uint8ClampedArray, width: number, height: number): Rect | null {
	let minX = width;
	let minY = height;
	let maxX = -1;
	let maxY = -1;
	for (let y = 0; y < height; y += 1) {
		const row = y * width * 4;
		for (let x = 0; x < width; x += 1) {
			if (!(pixels[row + x * 4 + 3] > STORED_MASK_CUTOFF)) continue;
			if (x < minX) minX = x;
			if (x > maxX) maxX = x;
			if (y < minY) minY = y;
			maxY = y;
		}
	}
	if (maxX < 0) return null;
	return { x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1 };
}
