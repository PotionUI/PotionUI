import { hexToRgba } from '../palette';
import { floodFill } from '../raster/floodFill';
import { PAINT_ICONS } from '../icons';
import type { PaintTool, Point, ToolHost } from '../types';

export function createFillTool(): PaintTool {
	return {
		id: 'fill',
		label: 'Fill',
		icon: PAINT_ICONS.fill,
		key: 'G',
		group: 'paint',
		options: ['color', 'tolerance'],
		hint: 'Click an area to fill it with the colour. Tolerance sets how alike a pixel must be.',
		cursor: 'crosshair',
		pointerDown(host: ToolHost, point: Point) {
			const layer = host.activeLayer;
			if (!layer) return;
			const context = layer.canvas.getContext('2d');
			if (!context) return;
			const x = Math.floor(point.x - layer.x);
			const y = Math.floor(point.y - layer.y);
			if (x < 0 || y < 0 || x >= layer.canvas.width || y >= layer.canvas.height) return;

			const before = host.snapshotLayer(layer);
			const image = context.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
			const mask = host.layerSelectionMask(layer);
			const rect = floodFill(
				{ width: image.width, height: image.height, data: image.data },
				x,
				y,
				hexToRgba(host.settings.color),
				host.settings.tolerance,
				mask ?? undefined
			);
			if (!rect) return;
			context.putImageData(image, 0, 0);
			host.commitPixels('Fill', layer, rect, before);
			host.invalidate();
		}
	};
}
