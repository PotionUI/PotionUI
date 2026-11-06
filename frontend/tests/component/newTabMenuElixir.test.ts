import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { get } from 'svelte/store';

const listElixirs = vi.fn();
const createElixir = vi.fn();
vi.mock('$lib/services/api/elixirs', () => ({ elixirsApi: () => ({ listElixirs, createElixir }) }));

const { tabsStore } = await import('$lib/stores/tabs');
const { registerBuiltinToolKinds } = await import('$lib/tool-tabs/builtin');
const { default: NewTabMenu } = await import('$lib/tool-tabs/NewTabMenu.svelte');
const { allElixirsOpen } = await import('$lib/elixirs/events');

let instance: ReturnType<typeof mount> | undefined;
const onOpenTool = vi.fn();

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

const summaries = [
	{ id: 'e1', name: 'Potion study', node_count: 6, status: 'brewing', updated_at: '2026-11-04T09:00:00Z', latest_outputs: [] },
	{ id: 'e2', name: 'Lookbook loop', node_count: 1, status: 'done', status_at: '2026-11-03T09:00:00Z', updated_at: '2026-11-03T09:00:00Z', latest_outputs: [{ kind: 'history', generation_id: 'g', file_id: '1', preview_url: '/m/a.webp', media_type: 'image' }] }
];

async function openMenu() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(NewTabMenu, { target, props: { onNewGeneration: vi.fn(), onOpenTool } });
	flushSync();
	target.querySelector<HTMLButtonElement>('button[aria-label="Add new tab"]')!.click();
	await settle();
}

async function drillIn() {
	await openMenu();
	document.body.querySelector<HTMLButtonElement>('[data-testid="new-tab-menu"] [data-tool="elixir"]')!.click();
	await vi.waitFor(() => expect(document.body.querySelector('[data-testid="elixir-picker-row"]')).not.toBeNull(), { timeout: 5000 });
	flushSync();
}

beforeEach(() => {
	registerBuiltinToolKinds();
	tabsStore.reset();
	listElixirs.mockResolvedValue({ ok: true, data: { elixirs: summaries } });
	createElixir.mockResolvedValue({ ok: true, data: { id: 'e9', name: 'Dusk' } });
	onOpenTool.mockReset();
	allElixirsOpen.set(false);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

describe('the + menu Elixir row', () => {
	it('is an ordinary row with a chevron after the editors', async () => {
		await openMenu();
		const rows = [...document.body.querySelectorAll<HTMLButtonElement>('[data-testid="new-tab-menu"] [data-tool]')];
		expect(rows.map((row) => row.dataset.tool)).toEqual(['image', 'video', 'elixir']);
		expect(rows[2].getAttribute('aria-haspopup')).toBe('menu');
		expect(rows[2].textContent).toContain('Chain presets, pick what continues');
		expect(document.body.textContent).not.toMatch(/plugin/i);
	});

	it('drills into the picker with search, New elixir, recent rows and All elixirs', async () => {
		await drillIn();
		expect(onOpenTool).not.toHaveBeenCalled();
		const picker = document.body.querySelector('[data-testid="elixir-picker"]')!;
		expect(picker).not.toBeNull();
		expect(document.activeElement?.getAttribute('aria-label')).toBe('Find an elixir');
		const rows = [...picker.querySelectorAll<HTMLElement>('[data-testid="elixir-picker-row"]')];
		expect(rows.map((row) => row.dataset.elixirId)).toEqual(['e1', 'e2']);
		expect(rows[0].textContent).toContain('Brewing');
		expect(rows[0].textContent).toContain('6 steps');
		expect(rows[1].textContent).toContain('1 step');
		expect(rows[1].querySelector('[data-testid="elixir-thumbs"] img')).not.toBeNull();
		expect(picker.querySelector('[data-testid="elixir-picker-all"]')!.textContent).toContain('2');
		expect(picker.textContent).toContain('An elixir that is already open switches to its tab.');
	});

	it('opens a picked elixir as its own tab, and the back button returns to the menu', async () => {
		await drillIn();
		document.body.querySelector<HTMLButtonElement>('[aria-label="Back to new tab menu"]')!.click();
		await settle();
		expect(document.body.querySelector('[data-testid="elixir-picker"]')).toBeNull();
		expect(document.body.querySelector('[data-testid="new-tab-menu"] [data-tool="elixir"]')).not.toBeNull();
		document.body.querySelector<HTMLButtonElement>('[data-testid="new-tab-menu"] [data-tool="elixir"]')!.click();
		await vi.waitFor(() => expect(document.body.querySelector('[data-elixir-id="e2"]')).not.toBeNull(), { timeout: 5000 });
		document.body.querySelector<HTMLButtonElement>('[data-elixir-id="e2"]')!.click();
		await settle();
		const state = get(tabsStore);
		expect(state.tabs.find((tab) => tab.id === state.activeTabId)).toMatchObject({ tool: 'elixir', key: 'e2', name: 'Lookbook loop' });
		expect(document.body.querySelector('[data-testid="new-tab-menu"]')).toBeNull();
	});

	it('marks an elixir that is already open', async () => {
		tabsStore.openToolTab('elixir', null, { key: 'e2', name: 'Lookbook loop' });
		await drillIn();
		const row = document.body.querySelector('[data-elixir-id="e2"]')!;
		expect(row.querySelector('[data-testid="elixir-open-chip"]')).not.toBeNull();
		expect(row.querySelector('[data-testid="elixir-thumbs"]')).toBeNull();
	});

	it('searches, offers a named new elixir, and Enter creates it when nothing matches', async () => {
		await drillIn();
		const input = document.body.querySelector<HTMLInputElement>('input[aria-label="Find an elixir"]')!;
		input.value = 'Dusk';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(document.body.textContent).toContain('No elixir named “Dusk”.');
		expect(document.body.querySelector('[data-testid="elixir-picker-new"]')!.textContent).toContain('New elixir “Dusk”');
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		await settle();
		expect(createElixir).toHaveBeenCalledWith('Dusk');
		const state = get(tabsStore);
		expect(state.tabs.find((tab) => tab.id === state.activeTabId)).toMatchObject({ tool: 'elixir', key: 'e9' });
	});

	it('moves the highlight with the arrow keys and opens it with Enter', async () => {
		await drillIn();
		const input = document.body.querySelector<HTMLInputElement>('input[aria-label="Find an elixir"]')!;
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
		await settle();
		expect(document.body.querySelector('[data-elixir-id="e2"]')!.getAttribute('aria-selected')).toBe('true');
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		await settle();
		const state = get(tabsStore);
		expect(state.tabs.find((tab) => tab.id === state.activeTabId)).toMatchObject({ key: 'e2' });
	});

	it('All elixirs opens the manager', async () => {
		await drillIn();
		document.body.querySelector<HTMLButtonElement>('[data-testid="elixir-picker-all"]')!.click();
		await settle();
		expect(get(allElixirsOpen)).toBe(true);
	});
});
