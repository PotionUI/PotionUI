import { get } from 'svelte/store';
import { activeTab, tabsStore } from '$lib/stores/tabs';
import { findGenerationTab, type MediaRef } from '$lib/types/tabs';
import { toolLabel } from './registry';
import { registerBuiltinToolKinds } from './builtin';
import type { ToolTabOpenOptions } from './types';

registerBuiltinToolKinds();

export function openToolTab(tool: string, source: MediaRef | null, options: ToolTabOpenOptions = {}): string {
	return tabsStore.openToolTab(tool, source, { activeTool: options.activeTool ?? null, name: toolLabel(tool) });
}

export function openToolTabs(tool: string, sources: MediaRef[], options: ToolTabOpenOptions = {}): string[] {
	return tabsStore.openToolTabs(tool, sources, { activeTool: options.activeTool ?? null, name: toolLabel(tool) });
}

export function findToolTabFor(source: MediaRef | null, tool?: string) {
	return tabsStore.findToolTabFor(source, tool);
}

export function ensureGenerationTabActive(): string {
	const state = get(tabsStore);
	const active = findGenerationTab(state.tabs, state.activeTabId);
	if (active) return active.id;
	const fallback = findGenerationTab(state.tabs, get(activeTab).id);
	if (fallback) {
		tabsStore.setActiveTab(fallback.id);
		return fallback.id;
	}
	tabsStore.addTab();
	return get(tabsStore).activeTabId;
}
