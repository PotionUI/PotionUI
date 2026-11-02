import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { tabsStore } = await import('$lib/stores/tabs');
const { default: ToolTabHost } = await import('$lib/tool-tabs/ToolTabHost.svelte');
const { isToolTab } = await import('$lib/types/tabs');
const { get } = await import('svelte/store');
const { registerToolKind } = await import('$lib/tool-tabs/registry');
const { extensionRegistrations } = await import('$lib/plugin-api/extensionRefresh');

let app: Record<string, unknown> | undefined;

async function settle() {
	for (let i = 0; i < 20; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

afterEach(() => {
	if (app) unmount(app);
	app = undefined;
	tabsStore.reset();
	document.body.innerHTML = '';
});

function mountActiveTool() {
	const tab = get(tabsStore).tabs.find(isToolTab)!;
	const target = document.createElement('div');
	document.body.appendChild(target);
	app = mount(ToolTabHost, { target, props: { tab } });
	return target;
}

describe('ToolTabHost', () => {
	it('mounts the Image editor placeholder with its empty state', async () => {
		tabsStore.openToolTab('image', null);
		const target = mountActiveTool();
		await settle();
		expect(target.querySelector('[data-testid="tool-tab-empty"]')?.textContent).toContain('Open a picture to edit');
	});

	it('shows the source and names the tab after it', async () => {
		const id = tabsStore.openToolTab('image', { path: '/m/fox.png', url: '/m/fox.png' });
		const target = mountActiveTool();
		await settle();
		expect(target.querySelector('[data-testid="tool-tab-source"]')?.getAttribute('src')).toBe('/m/fox.png');
		expect(get(tabsStore).tabs.find((tab) => tab.id === id)).toMatchObject({ name: 'fox', thumbnail: '/m/fox.png' });
	});

	it('says the tool is not available when no kind is registered', async () => {
		tabsStore.openToolTab('image-missing', null);
		const target = mountActiveTool();
		await settle();
		expect(target.querySelector('[data-testid="tool-tab-unavailable"]')).not.toBeNull();
	});

	it('resolves the tool component once and keeps it mounted across registration refreshes', async () => {
		const load = vi.fn(() => import('$lib/tool-tabs/placeholders/ImageToolPlaceholder.svelte'));
		const definition = { tool: 'acme.once' as const, label: 'Once', icon: 'brush', accepts: [], pluginId: 'acme', load };
		const off = registerToolKind(definition);
		try {
			tabsStore.openToolTab('acme.once', null);
			const target = mountActiveTool();
			await settle();
			const mounted = target.querySelector('[data-testid="tool-tab-placeholder"]');
			expect(mounted).not.toBeNull();
			expect(load).toHaveBeenCalledTimes(1);

			extensionRegistrations.update((tick) => tick + 1);
			registerToolKind(definition);
			await settle();

			expect(load).toHaveBeenCalledTimes(1);
			expect(target.querySelector('[data-testid="tool-tab-placeholder"]')).toBe(mounted);
		} finally {
			off();
		}
	});
});
