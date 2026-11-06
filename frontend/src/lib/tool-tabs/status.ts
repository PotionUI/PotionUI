import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { isToolTab, type ToolTabStatus } from '$lib/types/tabs';

export function sameStatus(a: ToolTabStatus | null | undefined, b: ToolTabStatus | null | undefined): boolean {
	if (!a || !b) return !a && !b;
	return (
		a.tone === b.tone &&
		a.label === b.label &&
		!!a.pulse === !!b.pulse &&
		!!a.ring === !!b.ring &&
		(a.count?.[0] ?? null) === (b.count?.[0] ?? null) &&
		(a.count?.[1] ?? null) === (b.count?.[1] ?? null)
	);
}

export function setToolTabStatus(tabId: string, status: ToolTabStatus | null): void {
	const tab = get(tabsStore).tabs.find((item) => item.id === tabId);
	if (!isToolTab(tab) || sameStatus(tab.status, status)) return;
	tabsStore.updateToolTab(tabId, { status: status ? { ...status } : null });
}

export function statusCountLabel(status: ToolTabStatus | null | undefined): string | null {
	return status?.count ? `${status.count[0]}/${status.count[1]}` : null;
}
