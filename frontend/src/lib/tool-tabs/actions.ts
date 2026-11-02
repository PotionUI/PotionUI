import { derived, writable, type Readable } from 'svelte/store';
import type { ToolTabActions } from './types';

const byTab = writable<Record<string, ToolTabActions>>({});

export function setToolTabActions(tabId: string, actions: ToolTabActions | null): void {
	byTab.update((current) => {
		if (!actions) {
			if (!(tabId in current)) return current;
			const next = { ...current };
			delete next[tabId];
			return next;
		}
		return { ...current, [tabId]: actions };
	});
}

export function toolTabActions(tabId: string): Readable<ToolTabActions | null> {
	return derived(byTab, ($byTab) => $byTab[tabId] ?? null);
}
