import type { PresetInfo } from '$lib/types/api';
import { isUnavailableMirror } from './presetMirrorState';

export type PresetDetailTab = 'overview' | 'access' | 'display' | 'configuration' | 'form' | 'prompt' | 'requirements';

export const DEFAULT_PRESET_DETAIL_TAB: PresetDetailTab = 'overview';
export const PRESET_DETAIL_TAB_PARAM = 'view';

export interface PresetDetailTabDescriptor {
	id: PresetDetailTab;
	label: string;
	icon: string;
	count?: number;
}

export function presetDetailTabsFor(preset: PresetInfo, hasConfiguration: boolean): PresetDetailTabDescriptor[] {
	const tabs: PresetDetailTabDescriptor[] = [
		{ id: 'overview', label: 'Overview', icon: 'info' },
		{
			id: 'access',
			label: 'Access',
			icon: 'group',
			count: preset.installed ? (preset.assignment_count || 0) + (preset.group_count || 0) : undefined
		}
	];
	if (preset.installed) tabs.push({ id: 'display', label: 'Display', icon: 'image' });
	if (isUnavailableMirror(preset)) return tabs;
	if (preset.installed && hasConfiguration) {
		tabs.push({ id: 'configuration', label: 'Configuration', icon: 'sliders' });
	}
	if (preset.installed) {
		tabs.push({ id: 'form', label: 'Form', icon: 'document' });
		tabs.push({ id: 'prompt', label: 'Prompt text', icon: 'edit' });
	}
	const missing = preset.requirements_summary?.missing ?? 0;
	tabs.push({ id: 'requirements', label: 'Requirements', icon: 'check', count: missing > 0 ? missing : undefined });
	return tabs;
}

const PRESET_DETAIL_TABS: readonly PresetDetailTab[] = [
	'overview',
	'access',
	'display',
	'configuration',
	'form',
	'prompt',
	'requirements'
];

export function restorePresetDetailTab(param: string | null): PresetDetailTab {
	return PRESET_DETAIL_TABS.find((id) => id === param) ?? DEFAULT_PRESET_DETAIL_TAB;
}

export function resolvePresetDetailTab(tabs: PresetDetailTabDescriptor[], requested: PresetDetailTab): PresetDetailTab {
	return tabs.some((tab) => tab.id === requested) ? requested : DEFAULT_PRESET_DETAIL_TAB;
}
