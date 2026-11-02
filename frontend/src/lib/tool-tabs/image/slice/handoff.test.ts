import { describe, expect, it } from 'vitest';
import { get, writable } from 'svelte/store';
import type { GenerationTab, Tab } from '$lib/types/tabs';
import { handOffToNewTab } from './handoff';

const SCHEMA = { properties: { g: { type: 'group', children: [{ name: 'refs', type: 'image', title: 'References', multiple: true, max_items: 6 }] } } };
const PLAIN = { properties: { g: { type: 'group', children: [{ name: 'image', type: 'image', title: 'Init' }] } } };

function fakeTabs() {
	const store = writable<{ tabs: Tab[] }>({ tabs: [] });
	return {
		subscribe: store.subscribe,
		addTabWithData: (name: string) => {
			store.update((state) => ({ tabs: [...state.tabs, { id: 'new', kind: 'generation', name, formData: {} } as unknown as Tab] }));
			return 'new';
		},
		updateTab: (id: string, updates: Partial<GenerationTab>) =>
			store.update((state) => ({ tabs: state.tabs.map((tab) => (tab.id === id ? ({ ...tab, ...updates } as Tab) : tab)) })),
		pick: (preset: string) => store.update((state) => ({ tabs: state.tabs.map((tab) => ({ ...tab, selectedPreset: preset, selectedMode: 'txt2img' }) as Tab) })),
		tab: () => get(store).tabs[0] as GenerationTab
	};
}

const items = [1, 2, 3, 4].map((n) => ({ id: `i${n}`, filename: `cell-${n}.png`, media_type: 'image', url: `/u/${n}` }));
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('handing slices to a new generation tab', () => {
	it('waits for a preset with a references field, then fills it once', async () => {
		const tabs = fakeTabs();
		const commits: number[] = [];
		const id = handOffToNewTab(items, 'mira refs', { tabs, loadSchema: async (preset) => (preset === 'plain' ? PLAIN : SCHEMA), oncommit: (_tab, _field, takes) => commits.push(takes) });
		expect(id).toBe('new');
		tabs.pick('plain');
		await settle();
		expect(tabs.tab().formData.refs).toBeUndefined();
		tabs.pick('krea');
		await settle();
		expect((tabs.tab().formData.refs as unknown[]).length).toBe(4);
		expect(commits).toEqual([4]);
		tabs.pick('other');
		await settle();
		expect(commits).toEqual([4]);
		expect((tabs.tab().formData.refs as unknown[]).length).toBe(4);
	});
});
