import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { isToolTab, type MediaRef } from '$lib/types/tabs';
import { mediaFileStem } from './sources';

export function saveNameFor(tabId: string, source: MediaRef | null | undefined): string | null {
	const tab = get(tabsStore).tabs.find((entry) => entry.id === tabId);
	if (isToolTab(tab) && tab.renamed && tab.name.trim()) return tab.name.trim();
	return mediaFileStem(source);
}
