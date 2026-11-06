import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { tabsStore } = await import('$lib/stores/tabs');
const { default: ToolTabHost } = await import('$lib/tool-tabs/ToolTabHost.svelte');
const { isToolTab } = await import('$lib/types/tabs');
const { get } = await import('svelte/store');
const { registerToolKind } = await import('$lib/tool-tabs/registry');
const { default: ProbeToolTab } = await import('./fixtures/ProbeToolTab.svelte');
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

const PROBE = 'acme.probe' as const;

function registerProbe(load = vi.fn(async () => ProbeToolTab)) {
	const definition = { tool: PROBE, label: 'Probe', icon: 'brush', accepts: ['image' as const], pluginId: 'acme', load };
	return { load, definition, off: registerToolKind(definition) };
}

describe('ToolTabHost', () => {
	it('mounts the registered tool component with its empty state', async () => {
		const { off } = registerProbe();
		try {
			tabsStore.openToolTab(PROBE, null);
			const target = mountActiveTool();
			await settle();
			expect(target.querySelector('[data-testid="tool-tab-host"]')?.getAttribute('data-tool')).toBe(PROBE);
			expect(target.querySelector('[data-testid="probe-empty"]')?.textContent).toContain('Nothing opened');
		} finally {
			off();
		}
	});

	it('shows the source and names the tab after it', async () => {
		const { off } = registerProbe();
		try {
			const id = tabsStore.openToolTab(PROBE, { path: '/m/fox.png', url: '/m/fox.png' });
			const target = mountActiveTool();
			await settle();
			expect(target.querySelector('[data-testid="probe-source"]')?.getAttribute('src')).toBe('/m/fox.png');
			expect(get(tabsStore).tabs.find((tab) => tab.id === id)).toMatchObject({ name: 'fox', thumbnail: '/m/fox.png' });
		} finally {
			off();
		}
	});

	it('says the tool is not available when no kind is registered', async () => {
		tabsStore.openToolTab('image-missing', null);
		const target = mountActiveTool();
		await settle();
		expect(target.querySelector('[data-testid="tool-tab-unavailable"]')).not.toBeNull();
		expect(target.querySelector('[data-testid="probe-tool"]')).toBeNull();
	});

	it('resolves the tool component once and keeps it mounted across registration refreshes', async () => {
		const { load, definition, off } = registerProbe();
		try {
			tabsStore.openToolTab(PROBE, null);
			const target = mountActiveTool();
			await settle();
			const mounted = target.querySelector('[data-testid="probe-tool"]');
			expect(mounted).not.toBeNull();
			expect(load).toHaveBeenCalledTimes(1);

			extensionRegistrations.update((tick) => tick + 1);
			registerToolKind(definition);
			await settle();

			expect(load).toHaveBeenCalledTimes(1);
			expect(target.querySelector('[data-testid="probe-tool"]')).toBe(mounted);
		} finally {
			off();
		}
	});
});
