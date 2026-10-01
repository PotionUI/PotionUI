import type { Point, Rect, View } from './types';

export const MAX_SIDE = 4096;
export const MIN_SIDE = 16;
export const MIN_ZOOM = 0.05;
export const MAX_ZOOM = 16;
export const FIT_MAX_ZOOM = 2;

export function clampSide(value: number): number {
	if (!Number.isFinite(value)) return MIN_SIDE;
	return Math.min(MAX_SIDE, Math.max(MIN_SIDE, Math.round(value)));
}

export function fitWithin(
	width: number,
	height: number,
	maxSide: number = MAX_SIDE
): { width: number; height: number; scale: number } {
	const longest = Math.max(width, height);
	if (longest <= maxSide) return { width, height, scale: 1 };
	const scale = maxSide / longest;
	return {
		width: Math.max(1, Math.round(width * scale)),
		height: Math.max(1, Math.round(height * scale)),
		scale
	};
}

export function fitView(
	stage: { width: number; height: number },
	doc: { width: number; height: number },
	padding: number = 24,
	maxZoom: number = FIT_MAX_ZOOM
): View {
	const availableWidth = Math.max(1, stage.width - padding * 2);
	const availableHeight = Math.max(1, stage.height - padding * 2);
	const raw = Math.min(availableWidth / doc.width, availableHeight / doc.height);
	const zoom = Math.min(maxZoom, Math.max(MIN_ZOOM, raw));
	return {
		zoom,
		panX: (stage.width - doc.width * zoom) / 2,
		panY: (stage.height - doc.height * zoom) / 2
	};
}

export function screenToDoc(view: View, point: Point): Point {
	return {
		x: (point.x - view.panX) / view.zoom,
		y: (point.y - view.panY) / view.zoom
	};
}

export function docToScreen(view: View, point: Point): Point {
	return {
		x: point.x * view.zoom + view.panX,
		y: point.y * view.zoom + view.panY
	};
}

export function zoomAbout(
	view: View,
	factor: number,
	anchor: Point,
	min: number = MIN_ZOOM,
	max: number = MAX_ZOOM
): View {
	const zoom = Math.min(max, Math.max(min, view.zoom * factor));
	const ratio = zoom / view.zoom;
	return {
		zoom,
		panX: anchor.x - (anchor.x - view.panX) * ratio,
		panY: anchor.y - (anchor.y - view.panY) * ratio
	};
}

export function panBy(view: View, dx: number, dy: number): View {
	return { zoom: view.zoom, panX: view.panX + dx, panY: view.panY + dy };
}

export function rectFromPoints(a: Point, b: Point, pad: number = 0): Rect {
	const x0 = Math.min(a.x, b.x) - pad;
	const y0 = Math.min(a.y, b.y) - pad;
	const x1 = Math.max(a.x, b.x) + pad;
	const y1 = Math.max(a.y, b.y) + pad;
	return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

export function rectUnion(a: Rect | null, b: Rect): Rect {
	if (!a) return { ...b };
	const x0 = Math.min(a.x, b.x);
	const y0 = Math.min(a.y, b.y);
	const x1 = Math.max(a.x + a.width, b.x + b.width);
	const y1 = Math.max(a.y + a.height, b.y + b.height);
	return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

export function rectIntersect(a: Rect, b: Rect): Rect | null {
	const x0 = Math.max(a.x, b.x);
	const y0 = Math.max(a.y, b.y);
	const x1 = Math.min(a.x + a.width, b.x + b.width);
	const y1 = Math.min(a.y + a.height, b.y + b.height);
	if (x1 <= x0 || y1 <= y0) return null;
	return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

export function pixelRect(rect: Rect, bounds: { width: number; height: number }): Rect | null {
	const x0 = Math.floor(rect.x);
	const y0 = Math.floor(rect.y);
	const x1 = Math.ceil(rect.x + rect.width);
	const y1 = Math.ceil(rect.y + rect.height);
	return rectIntersect(
		{ x: x0, y: y0, width: x1 - x0, height: y1 - y0 },
		{ x: 0, y: 0, width: bounds.width, height: bounds.height }
	);
}

export function flipPosition(
	axis: 'horizontal' | 'vertical',
	doc: { width: number; height: number },
	layer: { x: number; y: number; width: number; height: number }
): { x: number; y: number } {
	if (axis === 'horizontal') return { x: doc.width - layer.x - layer.width, y: layer.y };
	return { x: layer.x, y: doc.height - layer.y - layer.height };
}

export function rotatePosition(
	direction: 'cw' | 'ccw',
	doc: { width: number; height: number },
	layer: { x: number; y: number; width: number; height: number }
): { x: number; y: number } {
	if (direction === 'cw') return { x: doc.height - layer.y - layer.height, y: layer.x };
	return { x: layer.y, y: doc.width - layer.x - layer.width };
}

export function clampRectToBounds(
	rect: Rect,
	bounds: { width: number; height: number }
): Rect | null {
	const x0 = Math.max(0, Math.min(bounds.width, Math.round(rect.x)));
	const y0 = Math.max(0, Math.min(bounds.height, Math.round(rect.y)));
	const x1 = Math.max(0, Math.min(bounds.width, Math.round(rect.x + rect.width)));
	const y1 = Math.max(0, Math.min(bounds.height, Math.round(rect.y + rect.height)));
	if (x1 - x0 < 1 || y1 - y0 < 1) return null;
	return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

export function scaleFromCorner(
	base: Rect,
	corner: 'tl' | 'tr' | 'bl' | 'br',
	point: Point,
	minSide: number = 8
): Rect {
	const anchorX = corner === 'tl' || corner === 'bl' ? base.x + base.width : base.x;
	const anchorY = corner === 'tl' || corner === 'tr' ? base.y + base.height : base.y;
	const width = Math.max(minSide, Math.abs(point.x - anchorX));
	const factor = width / base.width;
	const height = Math.max(minSide, base.height * factor);
	return {
		x: corner === 'tl' || corner === 'bl' ? anchorX - width : anchorX,
		y: corner === 'tl' || corner === 'tr' ? anchorY - height : anchorY,
		width,
		height
	};
}

export function fitInside(
	content: { width: number; height: number },
	frame: { width: number; height: number },
	fraction: number = 0.6
): { width: number; height: number } {
	const scale = Math.min(
		1,
		(frame.width * fraction) / content.width,
		(frame.height * fraction) / content.height
	);
	return {
		width: Math.max(1, Math.round(content.width * scale)),
		height: Math.max(1, Math.round(content.height * scale))
	};
}

export function hitCorner(
	rect: Rect,
	point: Point,
	radius: number
): 'tl' | 'tr' | 'bl' | 'br' | null {
	const corners: Array<['tl' | 'tr' | 'bl' | 'br', number, number]> = [
		['tl', rect.x, rect.y],
		['tr', rect.x + rect.width, rect.y],
		['bl', rect.x, rect.y + rect.height],
		['br', rect.x + rect.width, rect.y + rect.height]
	];
	for (const [name, x, y] of corners) {
		if (Math.abs(point.x - x) <= radius && Math.abs(point.y - y) <= radius) return name;
	}
	return null;
}
