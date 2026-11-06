import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from './tabs';
import { isToolTab, type ToolTab } from '$lib/types/tabs';
import { toPersistedTab } from './tabPersistence';

function toolTabs(): ToolTab[] {
	return get(tabsStore).tabs.filter(isToolTab);
}

function tabById(id: string): ToolTab | undefined {
	return toolTabs().find((tab) => tab.id === id);
}

describe('keyed tool tabs', () => {
	beforeEach(() => tabsStore.reset());

	it('opens one tab per key and focuses it on a second open', () => {
		const first = tabsStore.openToolTab('elixir', null, { key: 'e1', state: { elixirId: 'e1' }, name: 'Potion study' });
		expect(tabById(first)).toMatchObject({ key: 'e1', state: { elixirId: 'e1' }, name: 'Potion study', status: null });
		const other = tabsStore.openToolTab('elixir', null, { key: 'e2', state: { elixirId: 'e2' }, name: 'Lookbook' });
		expect(other).not.toBe(first);
		expect(get(tabsStore).activeTabId).toBe(other);
		const again = tabsStore.openToolTab('elixir', null, { key: 'e1', state: { elixirId: 'e1' }, name: 'Renamed' });
		expect(again).toBe(first);
		expect(get(tabsStore).activeTabId).toBe(first);
		expect(toolTabs()).toHaveLength(2);
		expect(tabById(first)?.name).toBe('Potion study');
	});

	it('fills the active blank tab of that tool instead of adding one', () => {
		const blank = tabsStore.openToolTab('elixir', null);
		const filled = tabsStore.openToolTab('elixir', null, { key: 'e1', state: { elixirId: 'e1' }, name: 'Potion study' });
		expect(filled).toBe(blank);
		expect(toolTabs()).toHaveLength(1);
		expect(tabById(blank)).toMatchObject({ key: 'e1', name: 'Potion study' });
	});

	it('does not fill a blank tab of another tool', () => {
		const image = tabsStore.openToolTab('image', null);
		const elixir = tabsStore.openToolTab('elixir', null, { key: 'e1' });
		expect(elixir).not.toBe(image);
		expect(tabsStore.findToolTabByKey('elixir', 'e1')?.id).toBe(elixir);
		expect(tabsStore.findToolTabByKey('image', 'e1')).toBeNull();
		expect(tabsStore.findToolTabByKey('elixir', null)).toBeNull();
	});

	it('a blank tab claims a key, or gives way to the tab that already holds it', () => {
		const held = tabsStore.openToolTab('elixir', null, { key: 'e1', name: 'Potion study' });
		const chooser = tabsStore.openToolTab('elixir', null);
		expect(tabsStore.claimToolTabKey(chooser, 'e1', { elixirId: 'e1' }, 'Potion study')).toBe(held);
		expect(tabById(chooser)).toBeUndefined();
		expect(get(tabsStore).activeTabId).toBe(held);

		const next = tabsStore.openToolTab('elixir', null);
		expect(tabsStore.claimToolTabKey(next, 'e2', { elixirId: 'e2' }, 'Lookbook')).toBe(next);
		expect(tabById(next)).toMatchObject({ key: 'e2', state: { elixirId: 'e2' }, name: 'Lookbook' });
	});

	it('persists the key and the status with the tab', () => {
		const id = tabsStore.openToolTab('elixir', null, { key: 'e1', state: { elixirId: 'e1' } });
		tabsStore.updateToolTab(id, { status: { tone: 'signal', label: 'Brewing', count: [3, 6], pulse: true } });
		expect(toPersistedTab(tabById(id)!)).toMatchObject({ key: 'e1', status: { tone: 'signal', count: [3, 6] } });
	});
});
