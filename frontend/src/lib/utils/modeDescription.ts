import type { PresetModeInfo } from '$lib/types/api';

export function modeDescription(mode: Pick<PresetModeInfo, 'short_description' | 'description'>): string | null {
	const short = mode.short_description?.trim();
	if (short) return short;
	const long = mode.description?.trim();
	return long || null;
}
