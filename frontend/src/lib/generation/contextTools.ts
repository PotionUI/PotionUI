import type { GenerationTab } from '$lib/types/tabs';

export function contextToolsAvailable(tab: Pick<GenerationTab, 'selectedPreset'> | null | undefined): boolean {
	return !!tab?.selectedPreset;
}
