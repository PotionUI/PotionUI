import { PAINT_ICONS } from '../icons';
import type { PaintTool } from '../types';

export function createAdjustTool(): PaintTool {
	return {
		id: 'adjust',
		label: 'Adjust',
		icon: PAINT_ICONS.adjust,
		key: 'A',
		group: 'adjust',
		panel: 'adjust',
		cursor: 'default'
	};
}
