import { EDITOR_ICONS } from '$lib/media/editors/editorIcons';
import { PAINT_ICONS } from '../icons';
import { drawDot, drawSegment, strokeBounds } from '../raster/brush';
import { pixelRect, rectUnion } from '../geometry';
import type { Layer, PaintTool, Point, Rect, ToolHost, ToolPointer } from '../types';
import { drawCursorRing } from './cursorRing';
import { pressureScale } from './pressure';

interface Stroke {
	layer: Layer;
	buffer: HTMLCanvasElement;
	before: HTMLCanvasElement;
	context: CanvasRenderingContext2D;
	last: Point;
	dirty: Rect | null;
}

function local(layer: Layer, point: Point): Point {
	return { x: point.x - layer.x, y: point.y - layer.y };
}

function extend(active: Stroke, from: Point, to: Point, size: number) {
	active.dirty = rectUnion(active.dirty, strokeBounds(from, to, size));
}

export function createBrushTool(): PaintTool {
	let stroke: Stroke | null = null;
	return {
		id: 'brush',
		label: 'Brush',
		icon: PAINT_ICONS.brush,
		key: 'B',
		group: 'paint',
		options: ['size', 'opacity', 'color'],
		hint: 'Click and drag to paint. Pen pressure changes the width. Alt+click picks a colour.',
		cursor: 'brush',
		pointerDown(host: ToolHost, point: Point, pointer: ToolPointer) {
			if (pointer.altKey) {
				host.pickColor(point);
				return;
			}
			const layer = host.activeLayer;
			if (!layer) return;
			const buffer = document.createElement('canvas');
			buffer.width = layer.canvas.width;
			buffer.height = layer.canvas.height;
			const context = buffer.getContext('2d');
			if (!context) return;
			host.clipToSelection(context, layer);
			const at = local(layer, point);
			const size = host.settings.size * pressureScale(pointer);
			stroke = {
				layer,
				buffer,
				context,
				before: host.snapshotLayer(layer),
				last: at,
				dirty: null
			};
			drawDot(context, at, { size, color: host.settings.color, erase: false });
			extend(stroke, at, at, size);
			host.setStrokePreview(layer, buffer, host.settings.opacity / 100);
			host.invalidate();
		},
		pointerMove(host: ToolHost, point: Point, pointer: ToolPointer) {
			if (!stroke) return;
			const at = local(stroke.layer, point);
			const size = host.settings.size * pressureScale(pointer);
			drawSegment(stroke.context, stroke.last, at, {
				size,
				color: host.settings.color,
				erase: false
			});
			extend(stroke, stroke.last, at, size);
			stroke.last = at;
			host.invalidate();
		},
		pointerUp(host: ToolHost) {
			if (!stroke) return;
			const finished = stroke;
			stroke = null;
			const target = finished.layer.canvas.getContext('2d');
			if (target && finished.dirty) {
				target.save();
				target.globalAlpha = host.settings.opacity / 100;
				target.globalCompositeOperation = 'source-over';
				target.drawImage(finished.buffer, 0, 0);
				target.restore();
				const rect = pixelRect(finished.dirty, finished.layer.canvas);
				host.setStrokePreview(null, null, 1);
				if (rect) host.commitPixels('Brush stroke', finished.layer, rect, finished.before);
			} else {
				host.setStrokePreview(null, null, 1);
			}
			host.invalidate();
		},
		pointerCancel(host: ToolHost) {
			stroke = null;
			host.setStrokePreview(null, null, 1);
			host.invalidate();
		},
		overlay: drawCursorRing
	};
}

interface EraserStroke {
	layer: Layer;
	context: CanvasRenderingContext2D;
	before: HTMLCanvasElement;
	last: Point;
	dirty: Rect | null;
}

export function createEraserTool(): PaintTool {
	let eraserStroke: EraserStroke | null = null;
	return {
		id: 'eraser',
		label: 'Eraser',
		icon: EDITOR_ICONS.eraser,
		key: 'E',
		group: 'paint',
		options: ['size'],
		hint: 'Click and drag to erase to transparent on the active layer.',
		cursor: 'brush',
		pointerDown(host: ToolHost, point: Point, pointer: ToolPointer) {
			const layer = host.activeLayer;
			if (!layer) return;
			const context = layer.canvas.getContext('2d');
			if (!context) return;
			const at = local(layer, point);
			const size = host.settings.size * pressureScale(pointer);
			eraserStroke = {
				layer,
				context,
				before: host.snapshotLayer(layer),
				last: at,
				dirty: null
			};
			context.save();
			host.clipToSelection(context, layer);
			drawDot(context, at, { size, color: 'rgb(0, 0, 0)', erase: true });
			context.restore();
			eraserStroke.dirty = rectUnion(null, strokeBounds(at, at, size));
			host.invalidate();
		},
		pointerMove(host: ToolHost, point: Point, pointer: ToolPointer) {
			if (!eraserStroke) return;
			const at = local(eraserStroke.layer, point);
			const size = host.settings.size * pressureScale(pointer);
			eraserStroke.context.save();
			host.clipToSelection(eraserStroke.context, eraserStroke.layer);
			drawSegment(eraserStroke.context, eraserStroke.last, at, {
				size,
				color: 'rgb(0, 0, 0)',
				erase: true
			});
			eraserStroke.context.restore();
			eraserStroke.dirty = rectUnion(eraserStroke.dirty, strokeBounds(eraserStroke.last, at, size));
			eraserStroke.last = at;
			host.invalidate();
		},
		pointerUp(host: ToolHost) {
			if (!eraserStroke) return;
			const finished = eraserStroke;
			eraserStroke = null;
			if (finished.dirty) {
				const rect = pixelRect(finished.dirty, finished.layer.canvas);
				if (rect) host.commitPixels('Erase', finished.layer, rect, finished.before);
			}
			host.invalidate();
		},
		pointerCancel(host: ToolHost) {
			if (eraserStroke) {
				const { before, context } = eraserStroke;
				context.save();
				context.globalCompositeOperation = 'copy';
				context.drawImage(before, 0, 0);
				context.restore();
				eraserStroke = null;
			}
			host.invalidate();
		},
		overlay: drawCursorRing
	};
}
