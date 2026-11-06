import { afterEach, describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { get } from 'svelte/store';
import { mountComponent } from '../helpers/mount';

const { tabsStore } = await import('$lib/stores/tabs');
const { isToolTab } = await import('$lib/types/tabs');
const { registerToolKind } = await import('$lib/tool-tabs/registry');
const { toolTabActions } = await import('$lib/tool-tabs/actions');
const { default: ToolTabHost } = await import('$lib/tool-tabs/ToolTabHost.svelte');
const { default: ActionsToolFixture } = await import('./ActionsToolFixture.svelte');

async function settle() {
	for (let i = 0; i < 20; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

afterEach(() => {
	tabsStore.reset();
	document.body.innerHTML = '';
});

describe('ToolTabHost actions', () => {
	it('keeps the phone top bar actions while the same tab updates and clears them once the host moves to another tab', async () => {
		const off = registerToolKind({ tool: 'acme.actions', label: 'Actions', icon: 'brush', accepts: [], pluginId: 'acme', component: ActionsToolFixture as never });
		try {
			const id = tabsStore.openToolTab('acme.actions', null);
			const tab = () => get(tabsStore).tabs.find((entry) => entry.id === id && isToolTab(entry))!;
			const view = mountComponent(ToolTabHost, { tab: tab() });
			await settle();
			expect(get(toolTabActions(id))?.save?.label).toBe('Save');

			tabsStore.updateToolTab(id, { state: { stamps: 1 }, dirty: 1 });
			view.rerender({ tab: tab() });
			await settle();
			expect(get(toolTabActions(id))?.save?.label).toBe('Save');

			const other = tabsStore.openToolTab('acme.actions', { path: '/m/other.png', url: '/m/other.png' });
			expect(other).not.toBe(id);
			view.rerender({ tab: get(tabsStore).tabs.find((entry) => entry.id === other && isToolTab(entry))! });
			await settle();
			expect(get(toolTabActions(id))).toBeNull();

			view.destroy();
		} finally {
			off();
		}
	});
});
