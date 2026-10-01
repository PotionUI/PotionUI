import { PAINT_ICONS } from '../icons';
import { clampRectToBounds } from '../geometry';
import type { PaintTool, Point, ToolHost } from '../types';

export function createCropTool(): PaintTool {
	let start: Point | null = null;
	return {
		id: 'crop',
		label: 'Crop canvas',
		caption: 'Crop',
		icon: PAINT_ICONS.crop,
		key: 'C',
		group: 'canvas',
		panel: 'crop',
		hint: 'Drag the area to keep, then press Enter or Apply crop.',
		cursor: 'crosshair',
		pointerDown(host: ToolHost, point: Point) {
			start = point;
			host.setCropRect(null);
		},
		pointerMove(host: ToolHost, point: Point) {
			if (!start) return;
			const rect = clampRectToBounds(
				{
					x: Math.min(start.x, point.x),
					y: Math.min(start.y, point.y),
					width: Math.abs(point.x - start.x),
					height: Math.abs(point.y - start.y)
				},
				{ width: host.docWidth, height: host.docHeight }
			);
			host.setCropRect(rect);
		},
		pointerUp() {
			start = null;
		},
		pointerCancel(host: ToolHost) {
			start = null;
			host.setCropRect(null);
		},
		overlay(host: ToolHost, context: CanvasRenderingContext2D) {
			const crop = host.cropRect;
			if (!crop) return;
			context.save();
			context.globalAlpha = 0.55;
			context.fillStyle = 'rgb(0, 0, 0)';
			context.beginPath();
			context.rect(0, 0, host.docWidth, host.docHeight);
			context.rect(crop.x, crop.y, crop.width, crop.height);
			context.fill('evenodd');
			context.globalAlpha = 1;
			context.lineWidth = 1.5 / host.zoom;
			context.strokeStyle = host.cursorColor;
			context.strokeRect(crop.x, crop.y, crop.width, crop.height);
			context.restore();
		}
	};
}
