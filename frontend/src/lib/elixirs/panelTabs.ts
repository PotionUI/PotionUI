import { sessionMode } from './follow';
import type { NodeLike } from './types';

export const QUICK_TAB = 'quick';
export const SETTINGS_TAB = 'settings';

export interface PanelTab {
	id: string;
	label: string;
}

export function hasLocalSettings(node: NodeLike | null | undefined): boolean {
	return Boolean(node?.overrides?.formData);
}

export function panelTabs(node: NodeLike | null | undefined, formAvailable: boolean): PanelTab[] {
	if (!node || node.kind !== 'preset' || !formAvailable || !node.preset_id || !node.mode) return [];
	const mode = sessionMode(node);
	if (mode !== 'new' && !node.session_snapshot) return [];
	const label = mode !== 'follow' && hasLocalSettings(node) ? 'All settings · Edited' : 'All settings';
	return [
		{ id: QUICK_TAB, label: 'Quick' },
		{ id: SETTINGS_TAB, label }
	];
}

export function activeTab(tabs: PanelTab[], chosen: string | null | undefined): string {
	return tabs.some((tab) => tab.id === chosen) ? (chosen as string) : QUICK_TAB;
}
