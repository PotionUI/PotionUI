import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore, activeTab, activeToolTab, normalizeTabsState } from './tabs';
import { embeddedTabFor, isEmbeddedTab, userGenerationTabs, visibleTabs } from '$lib/types/tabs';

function openElixir() {
	return tabsStore.openToolTab('elixir', null, { key: 'e1', state: { elixirId: 'e1' }, name: 'Potion study' });
}

describe('embedded generation tabs', () => {
	beforeEach(() => tabsStore.reset());

	it('opens hidden behind a tool tab and becomes the generation view', () => {
		const first = get(tabsStore).tabs[0].id;
		const owner = openElixir();
		const id = tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'local', selectedPreset: 'wan', selectedMode: 'i2v' });
		const state = get(tabsStore);
		expect(state.activeTabId).toBe(owner);
		expect(state.lastGenerationTabId).toBe(id);
		expect(get(activeTab).id).toBe(id);
		expect(get(activeToolTab)?.id).toBe(owner);
		const embedded = state.tabs.find((tab) => tab.id === id);
		expect(isEmbeddedTab(embedded)).toBe(true);
		expect(embedded && isEmbeddedTab(embedded) && embedded.embedded).toMatchObject({ toolTabId: owner, nodeId: 'n1', persistence: 'local', returnTo: first });
		expect(visibleTabs(state.tabs).some((tab) => tab.id === id)).toBe(false);
		expect(userGenerationTabs(state.tabs).some((tab) => tab.id === id)).toBe(false);
	});

	it('is replaced rather than stacked when another step opens', () => {
		const first = get(tabsStore).tabs[0].id;
		const owner = openElixir();
		const one = tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'local' });
		const two = tabsStore.openEmbeddedTab(owner, { nodeId: 'n2', persistence: 'session' });
		const state = get(tabsStore);
		expect(state.tabs.some((tab) => tab.id === one)).toBe(false);
		expect(embeddedTabFor(state.tabs, owner)?.id).toBe(two);
		expect(embeddedTabFor(state.tabs, owner)?.embedded.returnTo).toBe(first);
	});

	it('closing returns the generation view to the tab it replaced', () => {
		const first = get(tabsStore).tabs[0].id;
		const owner = openElixir();
		tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'local' });
		tabsStore.closeEmbeddedTab(owner);
		const state = get(tabsStore);
		expect(embeddedTabFor(state.tabs, owner)).toBeNull();
		expect(state.lastGenerationTabId).toBe(first);
		expect(state.activeTabId).toBe(owner);
	});

	it('is shown again when the owning tool tab is re-activated', () => {
		const first = get(tabsStore).tabs[0].id;
		const owner = openElixir();
		const id = tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'local' });
		tabsStore.setActiveTab(first);
		expect(get(tabsStore).lastGenerationTabId).toBe(first);
		tabsStore.setActiveTab(owner);
		expect(get(tabsStore).lastGenerationTabId).toBe(id);
	});

	it('activating the embedded tab activates its owner', () => {
		const owner = openElixir();
		const id = tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'local' });
		tabsStore.setActiveTab(get(tabsStore).tabs[0].id);
		tabsStore.setActiveTab(id);
		expect(get(tabsStore).activeTabId).toBe(owner);
		expect(get(tabsStore).lastGenerationTabId).toBe(id);
	});

	it('is removed together with its owning tool tab', () => {
		const first = get(tabsStore).tabs[0].id;
		const owner = openElixir();
		tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'local' });
		tabsStore.removeTab(owner);
		const state = get(tabsStore);
		expect(state.tabs.some(isEmbeddedTab)).toBe(false);
		expect(state.lastGenerationTabId).toBe(first);
	});

	it('does not count as a generation tab when naming a new one', () => {
		const owner = openElixir();
		tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'local' });
		tabsStore.addTab();
		const names = userGenerationTabs(get(tabsStore).tabs).map((tab) => tab.name);
		expect(names).toEqual(['Generation 1', 'Generation 2']);
	});

	it('is never the generation view once its owner is gone', () => {
		const owner = openElixir();
		const id = tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'local' });
		const state = get(tabsStore);
		const orphaned = normalizeTabsState({ ...state, tabs: state.tabs.filter((tab) => tab.id !== owner), activeTabId: state.tabs[0].id, lastGenerationTabId: id });
		expect(orphaned.lastGenerationTabId).toBe(state.tabs[0].id);
	});

	it('keeps edits made to the embedded tab through updateTab', () => {
		const owner = openElixir();
		const id = tabsStore.openEmbeddedTab(owner, { nodeId: 'n1', persistence: 'local', prompt: 'a' });
		tabsStore.updateTab(id, { prompt: 'b' });
		expect(embeddedTabFor(get(tabsStore).tabs, owner)?.prompt).toBe('b');
	});
});
