import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { tabsStore } = await import('$lib/stores/tabs');
await import('$lib/tool-tabs/open');
const { default: ToolTabHost } = await import('$lib/tool-tabs/ToolTabHost.svelte');
const { isToolTab } = await import('$lib/types/tabs');
const { get } = await import('svelte/store');

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
		tabsStore.openToolTab('image', null, { name: 'Image editor' });
		const target = mountActiveTool();
		await settle();
		expect(target.querySelector('[data-testid="tool-tab-empty"]')?.textContent).toContain('Open a picture to edit');
	});

	it('shows the source and names the tab after it', async () => {
		const id = tabsStore.openToolTab('image', { path: '/m/fox.png', url: '/m/fox.png' }, { name: 'Image editor' });
		const target = mountActiveTool();
		await settle();
		expect(target.querySelector('[data-testid="tool-tab-source"]')?.getAttribute('src')).toBe('/m/fox.png');
		expect(get(tabsStore).tabs.find((tab) => tab.id === id)).toMatchObject({ name: 'fox', thumbnail: '/m/fox.png' });
	});

	it('says the tool is not available when no kind is registered', async () => {
		tabsStore.openToolTab('image-missing', null, { name: 'Gone' });
		const target = mountActiveTool();
		await settle();
		expect(target.querySelector('[data-testid="tool-tab-unavailable"]')).not.toBeNull();
	});
});
