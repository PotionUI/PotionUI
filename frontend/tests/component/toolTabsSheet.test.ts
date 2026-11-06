import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/stores/presetsCatalog', () => ({
	loadPresetNameMap: vi.fn().mockResolvedValue({ 'krea-2': 'Krea 2' })
}));

const { tabsStore } = await import('$lib/stores/tabs');
const { registerToolKind } = await import('$lib/tool-tabs/registry');
const { default: TabsSheet } = await import('$lib/tool-tabs/TabsSheet.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { isToolTab } = await import('$lib/types/tabs');

let destroy: (() => void) | undefined;
const onClose = vi.fn();

function mountSheet() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: TabsSheet as never, target, props: { onClose } });
	destroy = () => {
		component.$destroy();
		target.remove();
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

const rowTexts = () =>
	[...document.body.querySelectorAll<HTMLElement>('[data-testid="tabs-sheet-row"]')].map((row) => row.textContent?.replace(/\s+/g, ' ').trim());
const newButtons = () => [...document.body.querySelectorAll<HTMLButtonElement>('[data-testid="tabs-sheet-new"] button')];

beforeEach(() => {
	onClose.mockClear();
	tabsStore.reset();
	const generationId = get(tabsStore).tabs[0].id;
	tabsStore.updateTab(generationId, { name: 'Portrait study', selectedPreset: 'krea-2' });
	const dusk = tabsStore.openToolTab('image', { path: '/m/observatory-dusk.png', url: '/m/observatory-dusk.png' });
	tabsStore.updateToolTab(dusk, { dirty: 2, thumbnail: '/m/observatory-dusk.png' });
	const clip = tabsStore.openToolTab('video', { path: '/m/timelapse.mp4' });
	tabsStore.updateToolTab(clip, { run: { runId: 'r', progress: 42 } });
});

afterEach(async () => {
	destroy?.();
	destroy = undefined;
	tabsStore.reset();
	document.body.innerHTML = '';
});

describe('the phone Tabs sheet', () => {
	it('lists every tab with its thumbnail and status', async () => {
		mountSheet();
		await settle();
		expect(document.body.querySelector('[role="dialog"]')?.getAttribute('aria-label')).toBe('Tabs');
		expect(rowTexts()).toEqual([
			'Portrait study Krea 2 · generation',
			'observatory-dusk Image editor · 2 edits, unsaved',
			'timelapse Video editor · saving 42%'
		]);
		const rows = document.body.querySelectorAll('[data-testid="tabs-sheet-row"]');
		expect(rows[1].querySelector('img')?.getAttribute('src')).toBe('/m/observatory-dusk.png');
		expect(rows[2].querySelector('[aria-current="page"]')).not.toBeNull();
		expect(document.body.querySelectorAll('button[aria-label^="Close "]')).toHaveLength(3);
	});

	it('offers Generation, Image editor and Video editor, then plugin kinds as plain buttons', async () => {
		const off = registerToolKind({ tool: 'acme.stamp', label: 'Stamp tool', icon: 'brush', accepts: ['image'], pluginId: 'acme' });
		try {
			mountSheet();
			await settle();
			expect(newButtons().map((b) => b.textContent?.trim())).toEqual(['Generation', 'Image editor', 'Video editor', 'Elixir', 'Stamp tool']);
			expect(document.body.textContent).not.toMatch(/plugin/i);
		} finally {
			off();
		}
	});

	it('says nothing about plugins when there are none', async () => {
		mountSheet();
		await settle();
		expect(newButtons().map((b) => b.textContent?.trim())).toEqual(['Generation', 'Image editor', 'Video editor', 'Elixir']);
		expect(document.body.querySelector('[data-testid="tabs-sheet-new"]')?.className).toContain('grid-cols-2');
		expect(document.body.textContent).not.toMatch(/plugin/i);
	});

	it('names an elixir tab status in its row', async () => {
		const id = tabsStore.openToolTab('elixir', null, { key: 'e1', name: 'Potion study' });
		tabsStore.updateToolTab(id, { status: { tone: 'signal', label: 'Brewing, 3 of 6 steps done', count: [3, 6], pulse: true } });
		mountSheet();
		await settle();
		expect(rowTexts().at(-1)).toContain('Elixir · brewing, 3 of 6 steps done');
		expect(document.body.querySelectorAll('[data-testid="tabs-sheet-row"] [data-testid="tab-status"]')).toHaveLength(1);
	});

	it('opens an empty Image editor tab and closes', async () => {
		mountSheet();
		await settle();
		newButtons()[1].click();
		await settle();
		const state = get(tabsStore);
		const opened = state.tabs.find((tab) => tab.id === state.activeTabId)!;
		expect(isToolTab(opened) && opened.tool === 'image' && opened.source === null).toBe(true);
		expect(onClose).toHaveBeenCalled();
		expect(rowTexts().at(-1)).toBe('Image editor Image editor · empty');
	});

	it('switches to a tab when its row is pressed', async () => {
		mountSheet();
		await settle();
		const first = document.body.querySelector<HTMLButtonElement>('[data-testid="tabs-sheet-row"] button')!;
		first.click();
		await settle();
		expect(get(tabsStore).activeTabId).toBe(get(tabsStore).tabs[0].id);
	});
});
