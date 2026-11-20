import { PAINT_ICONS } from '../icons';
import type { PaintTool, Point, ToolHost } from '../types';

export function createPickTool(): PaintTool {
	return {
		id: 'pick',
		label: 'Eyedropper',
		caption: 'Pick',
		icon: PAINT_ICONS.pick,
		key: 'I',
		group: 'paint',
		options: ['color'],
		hint: 'Click to take the colour of any visible pixel.',
		cursor: 'crosshair',
		pointerDown(host: ToolHost, point: Point) {
			host.pickColor(point);
		}
	};
}
