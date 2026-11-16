import { PAINT_ICONS } from '../icons';
import { clampPath, rectPath } from '../raster/selection';
import type { PaintTool, Point, ToolHost } from '../types';

export function createRectSelectTool(): PaintTool {
	let start: Point | null = null;
	return {
		id: 'rect',
		label: 'Rectangle select',
		caption: 'Select',
		icon: PAINT_ICONS.rect,
		key: 'M',
		group: 'select',
		panel: 'selection',
		hint: 'Drag to select a rectangle. Then cut, copy, move or delete it.',
		cursor: 'crosshair',
		pointerDown(_host: ToolHost, point: Point) {
			start = point;
		},
		pointerMove(host: ToolHost, point: Point) {
			if (!start) return;
			host.setSelection(clampPath(rectPath(start, point), host.docWidth, host.docHeight), false);
		},
		pointerUp(host: ToolHost, point: Point) {
			if (!start) return;
			const path = clampPath(rectPath(start, point), host.docWidth, host.docHeight);
			start = null;
			host.setSelection(path, true);
		},
		pointerCancel(host: ToolHost) {
			start = null;
			host.setSelection(null, true);
		}
	};
}

export function createLassoTool(): PaintTool {
	let points: Point[] = [];
	return {
		id: 'lasso',
		label: 'Lasso select',
		caption: 'Lasso',
		icon: PAINT_ICONS.lasso,
		key: 'L',
		group: 'select',
		panel: 'selection',
		hint: 'Draw freehand around what you want. Release to close the shape.',
		cursor: 'crosshair',
		pointerDown(_host: ToolHost, point: Point) {
			points = [point];
		},
		pointerMove(host: ToolHost, point: Point) {
			if (points.length === 0) return;
			const last = points[points.length - 1];
			if (Math.hypot(point.x - last.x, point.y - last.y) < 1.5 / host.zoom) return;
			points.push(point);
			host.setSelection(clampPath(points, host.docWidth, host.docHeight), false);
		},
		pointerUp(host: ToolHost) {
			const path = clampPath(points, host.docWidth, host.docHeight);
			points = [];
			host.setSelection(path.length >= 3 ? path : null, true);
		},
		pointerCancel(host: ToolHost) {
			points = [];
			host.setSelection(null, true);
		}
	};
}
