import { goto } from '$app/navigation';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { isToolTab, type ToolTab } from '$lib/types/tabs';
import { isBlankToolTab } from '$lib/tool-tabs/sources';
import { ELIXIR_TOOL, elixirIdOf, type ElixirTabState } from './ids';

export { ELIXIR_TOOL, elixirIdOf, type ElixirTabState };

export const LAST_ELIXIR_STORAGE_KEY = 'potionui_elixir_last';

export interface ElixirRef {
	id: string;
	name?: string | null;
}

export function elixirTabs(): ToolTab[] {
	return get(tabsStore).tabs.filter((tab): tab is ToolTab => isToolTab(tab) && tab.tool === ELIXIR_TOOL);
}

export function openElixirIds(): Set<string> {
	return new Set(elixirTabs().map(elixirIdOf).filter((id): id is string => !!id));
}

export function rememberElixir(elixirId: string): void {
	try {
		localStorage.setItem(LAST_ELIXIR_STORAGE_KEY, elixirId);
	} catch {
		return;
	}
}

export function lastElixirId(): string | null {
	try {
		return localStorage.getItem(LAST_ELIXIR_STORAGE_KEY);
	} catch {
		return null;
	}
}

function goToGenerate(replace = false): void {
	if (typeof window === 'undefined' || window.location.pathname.startsWith('/generate')) return;
	setTimeout(() => void goto('/generate', { replaceState: replace }), 0);
}

export function openElixirTab(elixir: ElixirRef, options: { navigate?: boolean; replace?: boolean } = {}): string {
	const id = tabsStore.openToolTab(ELIXIR_TOOL, null, {
		key: elixir.id,
		state: { elixirId: elixir.id } satisfies ElixirTabState,
		name: elixir.name || null
	});
	tabsStore.setActiveTab(id);
	rememberElixir(elixir.id);
	if (options.navigate !== false) goToGenerate(options.replace);
	return id;
}

export function openElixirChooser(options: { navigate?: boolean; replace?: boolean } = {}): string {
	const state = get(tabsStore);
	const blank = state.tabs.find((tab) => isBlankToolTab(tab, ELIXIR_TOOL));
	const id = blank ? blank.id : tabsStore.openToolTab(ELIXIR_TOOL, null);
	tabsStore.setActiveTab(id);
	if (options.navigate !== false) goToGenerate(options.replace);
	return id;
}

export function openLastElixir(options: { navigate?: boolean; replace?: boolean } = {}): string {
	const tabs = elixirTabs();
	const remembered = lastElixirId();
	const target = tabs.find((tab) => remembered && elixirIdOf(tab) === remembered) ?? tabs[tabs.length - 1];
	if (!target) return openElixirChooser(options);
	tabsStore.setActiveTab(target.id);
	if (options.navigate !== false) goToGenerate(options.replace);
	return target.id;
}

export function closeElixirTabs(elixirId: string): void {
	for (const tab of elixirTabs()) {
		if (elixirIdOf(tab) === elixirId) tabsStore.removeTab(tab.id);
	}
}

export function renameElixirTabs(elixirId: string, name: string): void {
	for (const tab of elixirTabs()) {
		if (elixirIdOf(tab) === elixirId) tabsStore.setToolTabTitle(tab.id, name);
	}
}
