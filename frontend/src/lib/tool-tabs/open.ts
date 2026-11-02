import { get } from 'svelte/store';
import { activeTab, tabsStore } from '$lib/stores/tabs';

export function ensureGenerationTabActive(): string {
	const id = get(activeTab).id;
	if (get(tabsStore).activeTabId !== id) tabsStore.setActiveTab(id);
	return id;
}
