import { PAINT_ICONS } from '../icons';
import { hitCorner, scaleFromCorner } from '../geometry';
import type { DocSnapshot, Layer, PaintTool, Point, Rect, ToolHost } from '../types';

type Corner = 'tl' | 'tr' | 'bl' | 'br';

interface Drag {
	layer: Layer;
	start: Point;
	corner: Corner | null;
	base: Rect;
	before: DocSnapshot;
	moved: boolean;
}

function handleRadius(host: ToolHost): number {
	const coarse = typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;
	return (coarse ? 22 : 12) / host.zoom;
}

function layerRect(layer: Layer): Rect {
	return { x: layer.x, y: layer.y, width: layer.canvas.width, height: layer.canvas.height };
}

export function createTransformTool(): PaintTool {
	let drag: Drag | null = null;
	return {
		id: 'transform',
		label: 'Transform',
		caption: 'Scale',
		icon: PAINT_ICONS.transform,
		key: 'T',
		group: 'edit',
		panel: 'transform',
		hint: 'Drag the layer to move it. Drag a corner handle to scale it.',
		cursor: 'default',
		pointerDown(host: ToolHost, point: Point) {
			const layer = host.activeLayer;
			if (!layer) return;
			host.ensureLayerSource(layer);
			drag = {
				layer,
				start: point,
				corner: hitCorner(layerRect(layer), point, handleRadius(host)),
				base: layerRect(layer),
				before: host.captureDoc(),
				moved: false
			};
		},
		pointerMove(host: ToolHost, point: Point) {
			if (!drag) return;
			drag.moved = true;
			const source = drag.layer.source;
			if (!drag.corner || !source) {
				drag.layer.x = drag.base.x + Math.round(point.x - drag.start.x);
				drag.layer.y = drag.base.y + Math.round(point.y - drag.start.y);
				host.invalidate();
				return;
			}
			const next = scaleFromCorner(drag.base, drag.corner, point);
			const canvas = document.createElement('canvas');
			canvas.width = Math.max(1, Math.round(next.width));
			canvas.height = Math.max(1, Math.round(next.height));
			const context = canvas.getContext('2d');
			if (!context) return;
			context.imageSmoothingQuality = 'high';
			context.drawImage(source, 0, 0, canvas.width, canvas.height);
			host.replaceLayerCanvas(drag.layer, canvas, Math.round(next.x), Math.round(next.y));
			host.invalidate();
		},
		pointerUp(host: ToolHost) {
			if (!drag) return;
			const finished = drag;
			drag = null;
			if (finished.moved) host.commitStructure('Transform', finished.before);
		},
		pointerCancel() {
			drag = null;
		},
		overlay(host: ToolHost, context: CanvasRenderingContext2D) {
			const layer = host.activeLayer;
			if (!layer) return;
			const rect = layerRect(layer);
			const handle = (host.zoom > 0 ? 10 / host.zoom : 10) / 2;
			context.save();
			context.globalAlpha = 1;
			context.lineWidth = 1.5 / host.zoom;
			context.strokeStyle = host.cursorColor;
			context.strokeRect(rect.x, rect.y, rect.width, rect.height);
			context.fillStyle = host.cursorColor;
			for (const [x, y] of [
				[rect.x, rect.y],
				[rect.x + rect.width, rect.y],
				[rect.x, rect.y + rect.height],
				[rect.x + rect.width, rect.y + rect.height]
			]) {
				context.fillRect(x - handle, y - handle, handle * 2, handle * 2);
			}
			context.restore();
		}
	};
}
