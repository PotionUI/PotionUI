import { settingsPaneWidth } from '$lib/stores/generationLayout';

export const RAIL_ICON_WIDTH = 52;

export function frameTitleWidth(viewportWidth: number, panelSide: 'left' | 'right' = 'left'): number {
	return RAIL_ICON_WIDTH + (panelSide === 'left' ? settingsPaneWidth(viewportWidth) : 0);
}
