import { derived, get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import {
	buildSectionStorageKey,
	foldedForScope
} from '$lib/components/form-fields/sectionState';
import type { SectionCollapsedContext } from '$lib/form/sectionCollapsedContext';

/**
 * Builds the `SectionCollapsedContext` implementation the generate page hands
 * to `DynamicForm`. Looks up the live tab by id on every call (rather than
 * closing over a `Tab` snapshot) because `DynamicForm` mounts once per tab
 * and stays mounted across preset/mode switches (see activeTabContext.ts) -
 * a snapshot taken at construction time would go stale the moment the tab's
 * preset, mode, or sectionCollapsed map changes.
 */
export function createSectionCollapsedController(tabId: string): SectionCollapsedContext {
	function currentTab() {
		return get(tabsStore).tabs.find((t) => t.id === tabId);
	}

	return {
		folded: derived(tabsStore, ($tabs) => {
			const tab = $tabs.tabs.find((t) => t.id === tabId);
			return foldedForScope(tab?.sectionCollapsed, tab?.selectedPreset, tab?.selectedMode);
		}),
		set(fieldPath: string, collapsed: boolean): void {
			const tab = currentTab();
			const key = buildSectionStorageKey(tab?.selectedPreset, tab?.selectedMode, fieldPath);
			if (!tab || !key) return;
			tabsStore.updateTab(tabId, {
				sectionCollapsed: { ...(tab.sectionCollapsed || {}), [key]: collapsed }
			});
		}
	};
}
