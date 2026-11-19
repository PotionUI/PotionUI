import { goto } from '$app/navigation';
import { tabsStore } from '$lib/stores/tabs';
import type { GenerationHistoryItem } from '$lib/types/history';
import { buildHistoryReuseTabData, buildReuseTabTitle } from '$lib/utils/historyReuse';
import { announceReuseConversion } from '$lib/utils/reuseNotice';

export async function reuseInNewTab(
	generation: GenerationHistoryItem,
	resolvePresetName: (presetId: string) => string | null = () => null
): Promise<boolean> {
	if (!generation.preset_id) return false;
	const reuse = buildHistoryReuseTabData(generation);
	tabsStore.addTabWithData(buildReuseTabTitle('Reused', generation, resolvePresetName), reuse.tabData);
	announceReuseConversion(reuse);
	await goto('/generate');
	return true;
}
