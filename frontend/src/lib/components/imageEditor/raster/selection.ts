import type { Point, Selection } from '../types';
import { maskBounds, rasterizePolygon } from './selectionMask';

export function buildSelection(path: Point[], width: number, height: number): Selection | null {
	if (path.length < 3) return null;
	const mask = rasterizePolygon(path, width, height);
	const bounds = maskBounds(mask, width, height);
	if (!bounds) return null;
	return { path, mask, bounds };
}

export function rectPath(a: Point, b: Point): Point[] {
	const x0 = Math.min(a.x, b.x);
	const y0 = Math.min(a.y, b.y);
	const x1 = Math.max(a.x, b.x);
	const y1 = Math.max(a.y, b.y);
	return [
		{ x: x0, y: y0 },
		{ x: x1, y: y0 },
		{ x: x1, y: y1 },
		{ x: x0, y: y1 }
	];
}

export function clampPath(path: Point[], width: number, height: number): Point[] {
	return path.map((p) => ({
		x: Math.min(width, Math.max(0, p.x)),
		y: Math.min(height, Math.max(0, p.y))
	}));
}

export function translatePath(path: Point[], dx: number, dy: number): Point[] {
	return path.map((p) => ({ x: p.x + dx, y: p.y + dy }));
}

export function pointInBounds(selection: Selection, point: Point): boolean {
	const b = selection.bounds;
	return point.x >= b.x && point.x <= b.x + b.width && point.y >= b.y && point.y <= b.y + b.height;
}

export function pointSelected(selection: Selection, point: Point, width: number): boolean {
	const x = Math.floor(point.x);
	const y = Math.floor(point.y);
	if (x < 0 || y < 0) return false;
	return selection.mask[y * width + x] >= 128;
}
