import { fitView, panBy, zoomAbout, MAX_ZOOM, MIN_ZOOM } from '$lib/components/imageEditor/geometry';

export interface Size {
	width: number;
	height: number;
}

export interface Point {
	x: number;
	y: number;
}

export interface View {
	zoom: number;
	panX: number;
	panY: number;
}

export interface Viewport extends View {
	fit: boolean;
}

export { MIN_ZOOM, MAX_ZOOM };

export const ZOOM_STEP = 1.25;

export function fitViewport(): Viewport {
	return { fit: true, zoom: 1, panX: 0, panY: 0 };
}

export function resolveView(viewport: Viewport, stage: Size, media: Size, padding: number): View {
	if (!viewport.fit || media.width <= 0 || media.height <= 0) {
		return { zoom: viewport.zoom, panX: viewport.panX, panY: viewport.panY };
	}
	return fitView(stage, media, padding, 1);
}

function manual(view: View): Viewport {
	return { fit: false, zoom: view.zoom, panX: view.panX, panY: view.panY };
}

export function zoomAt(view: View, factor: number, anchor: Point): Viewport {
	return manual(zoomAbout(view, factor, anchor, MIN_ZOOM, MAX_ZOOM));
}

export function zoomCentered(view: View, factor: number, stage: Size): Viewport {
	return zoomAt(view, factor, { x: stage.width / 2, y: stage.height / 2 });
}

export function actualSize(stage: Size, media: Size): Viewport {
	return {
		fit: false,
		zoom: 1,
		panX: (stage.width - media.width) / 2,
		panY: (stage.height - media.height) / 2
	};
}

export function panView(view: View, dx: number, dy: number): Viewport {
	return manual(panBy(view, dx, dy));
}

export function wheelFactor(deltaY: number, deltaMode: number, pinch: boolean): number {
	const pixels = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * 400 : deltaY;
	const rate = pinch ? 0.01 : 0.0015;
	return Math.exp(-Math.max(-400, Math.min(400, pixels)) * rate);
}

export function pinchStep(view: View, before: [Point, Point], after: [Point, Point]): Viewport {
	const distance = (pair: [Point, Point]) => Math.hypot(pair[0].x - pair[1].x, pair[0].y - pair[1].y);
	const mid = (pair: [Point, Point]) => ({ x: (pair[0].x + pair[1].x) / 2, y: (pair[0].y + pair[1].y) / 2 });
	const start = distance(before);
	const factor = start > 0 ? distance(after) / start : 1;
	const from = mid(before);
	const to = mid(after);
	const zoomed = zoomAbout(view, factor, from, MIN_ZOOM, MAX_ZOOM);
	return manual(panBy(zoomed, to.x - from.x, to.y - from.y));
}

export function zoomPercent(view: View): number {
	return Math.round(view.zoom * 100);
}

export function isActualSize(viewport: Viewport): boolean {
	return !viewport.fit && Math.abs(viewport.zoom - 1) < 1e-6;
}
