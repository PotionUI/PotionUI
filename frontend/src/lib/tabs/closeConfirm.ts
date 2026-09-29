import { get, writable } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { tabHasUnsavedWork } from '$lib/utils/newWorkspace';
import type { Tab } from '$lib/types/tabs';

const pending = writable<string | null>(null);

export const pendingTabClose = { subscribe: pending.subscribe };

export function tabIsRunning(tab: Tab): boolean {
	return tab.generation.isGenerating || (tab.generation.queue?.length ?? 0) > 0;
}

export function requestCloseTab(id: string): void {
	const { tabs } = get(tabsStore);
	if (tabs.length < 2 || !tabs.some((t) => t.id === id)) return;
	pending.set(id);
}

export function confirmCloseTab(): void {
	const id = get(pending);
	pending.set(null);
	if (id && get(tabsStore).tabs.some((t) => t.id === id)) tabsStore.removeTab(id);
}

export function cancelCloseTab(): void {
	pending.set(null);
}

export function describeTabClose(tab: Tab): { title: string; message: string } {
	const lines = [`“${tab.name}” will be closed.`];
	if (tabIsRunning(tab)) lines.push('It has a generation running or queued.');
	if (tabHasUnsavedWork(tab)) lines.push('It has unsaved changes.');
	return { title: 'Close tab?', message: lines.join('\n') };
}
