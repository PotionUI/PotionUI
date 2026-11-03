import { writable } from 'svelte/store';

export const LIVE_SYNC_TAB_CONTEXT_KEY = 'liveSyncTabId';

const anchorsStore = writable<Record<string, Record<string, number>>>({});

export const liveSyncAnchors = { subscribe: anchorsStore.subscribe };

export function registerLiveSyncAnchor(tabId: string, key: string): () => void {
	anchorsStore.update((all) => {
		const tab = { ...(all[tabId] ?? {}) };
		tab[key] = (tab[key] ?? 0) + 1;
		return { ...all, [tabId]: tab };
	});
	return () =>
		anchorsStore.update((all) => {
			const tab = { ...(all[tabId] ?? {}) };
			const count = (tab[key] ?? 0) - 1;
			if (count > 0) tab[key] = count;
			else delete tab[key];
			return { ...all, [tabId]: tab };
		});
}
