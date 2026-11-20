import { PAINT_ICONS } from '../icons';
import type { PaintTool } from '../types';

export function createFiltersTool(): PaintTool {
	return {
		id: 'filters',
		label: 'Filters',
		icon: PAINT_ICONS.filters,
		key: 'R',
		group: 'adjust',
		panel: 'filters',
		cursor: 'default'
	};
}
