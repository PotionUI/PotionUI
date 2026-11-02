import type { GenerationTab } from '$lib/types/tabs';
import { tabHasUnsavedSessionChanges } from '$lib/utils/unsavedChangesGuard';

const stored = new WeakMap<GenerationTab, boolean>();

function storedSessionDirty(tab: GenerationTab): boolean {
	const known = stored.get(tab);
	if (known !== undefined) return known;
	const dirty = tabHasUnsavedSessionChanges(tab);
	stored.set(tab, dirty);
	return dirty;
}

export function tabShowsUnsavedChanges(tab: GenerationTab, live: Record<string, boolean>): boolean {
	return tab.id in live ? live[tab.id] : storedSessionDirty(tab);
}
