import { derived, get, writable, type Readable } from 'svelte/store';
import type { KitNotice } from '$lib/kits/types';

export interface TabKitNotices {
	live: KitNotice[] | null;
	submit: KitNotice[];
}

export const kitNotices = writable<Record<string, TabKitNotices>>({});

function patch(tabId: string, change: Partial<TabKitNotices>): void {
	kitNotices.update((all) => ({ ...all, [tabId]: { ...(all[tabId] ?? { live: null, submit: [] }), ...change } }));
}

export function setLiveKitNotices(tabId: string, notices: KitNotice[] | null): void {
	if (!tabId) return;
	patch(tabId, { live: notices });
}

export function setSubmitKitNotices(tabId: string, notices: KitNotice[]): void {
	if (!tabId) return;
	patch(tabId, { submit: notices });
}

export function pruneKitNotices(openTabIds: readonly string[]): void {
	const keep = new Set(openTabIds);
	const all = get(kitNotices);
	if (Object.keys(all).every((id) => keep.has(id))) return;
	kitNotices.set(Object.fromEntries(Object.entries(all).filter(([id]) => keep.has(id))));
}

export function effectiveNotices(entry: TabKitNotices | undefined): KitNotice[] {
	if (!entry) return [];
	return entry.live ?? entry.submit;
}

export function noticesForTab(tabId: Readable<string | null | undefined>): Readable<KitNotice[]> {
	return derived([kitNotices, tabId], ([all, id]) => effectiveNotices(id ? all[id] : undefined));
}
