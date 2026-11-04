import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/services/api', () => ({
	api: { getWorkspaces: vi.fn().mockResolvedValue({ success: true, data: [] }) }
}));

const { tabsStore } = await import('$lib/stores/tabs');
const { registerToolKind } = await import('$lib/tool-tabs/registry');
const { cancelCloseTab } = await import('$lib/tabs/closeConfirm');
const { default: TabBar } = await import('../../src/routes/generate/components/TabBar.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { isToolTab } = await import('$lib/types/tabs');

let destroy: (() => void) | undefined;

function mountBar() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: TabBar as never, target });
	destroy = () => {
		component.$destroy();
		target.remove();
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

const plusButton = () => document.body.querySelector<HTMLButtonElement>('button[aria-label="Add new tab"]')!;
const menu = () => document.body.querySelector<HTMLElement>('[role="menu"][aria-label="New tab"]');
const rows = () => [...(menu()?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
const dialog = () => document.body.querySelector<HTMLElement>('[role="alertdialog"]');

async function openMenu() {
	plusButton().click();
	await settle();
}

beforeEach(() => {
	tabsStore.reset();
	mountBar();
});

afterEach(async () => {
	cancelCloseTab();
	await settle();
	destroy?.();
	destroy = undefined;
	tabsStore.reset();
	document.body.innerHTML = '';
});

describe('the + menu', () => {
	it('opens a menu instead of adding a tab, listing generation, the editors and Elixir', async () => {
		await openMenu();
		expect(get(tabsStore).tabs).toHaveLength(1);
		expect(plusButton().getAttribute('aria-expanded')).toBe('true');
		expect(rows().map((row) => row.querySelector('.text-fg')?.textContent?.trim())).toEqual([
			'New generation',
			'Image editor',
			'Video editor',
			'Elixir'
		]);
		expect(menu()!.textContent).not.toMatch(/plugin/i);
	});

	it('New generation adds a generation tab', async () => {
		await openMenu();
		rows()[0].click();
		await settle();
		const { tabs, activeTabId } = get(tabsStore);
		expect(tabs).toHaveLength(2);
		expect(tabs[1].kind).toBe('generation');
		expect(activeTabId).toBe(tabs[1].id);
		expect(menu()).toBeNull();
	});

	it('Image editor adds an empty, active Image editor tab shown with its tool glyph', async () => {
		await openMenu();
		rows().find((row) => row.dataset.tool === 'image')!.click();
		await settle();
		const { tabs, activeTabId } = get(tabsStore);
		const tool = tabs.find(isToolTab)!;
		expect(tool).toMatchObject({ tool: 'image', source: null, name: 'Image editor' });
		expect(activeTabId).toBe(tool.id);
		const thumbs = document.body.querySelectorAll('[data-testid="tab-tool-thumb"]');
		expect(thumbs).toHaveLength(1);
	});

	it('lists plugin tool kinds as plain rows right after the editors, with no plugin wording', async () => {
		const off = registerToolKind({
			tool: 'acme.stamp',
			label: 'Stamp tool',
			description: 'Stamp shapes onto a picture',
			icon: 'brush',
			accepts: ['image'],
			pluginId: 'acme'
		});
		try {
			await openMenu();
			expect(rows().map((r) => r.querySelector('.text-fg')?.textContent?.trim())).toEqual([
				'New generation',
				'Image editor',
				'Video editor',
				'Elixir',
				'Stamp tool'
			]);
			const row = rows().find((r) => r.dataset.tool === 'acme.stamp')!;
			expect(row.className).toBe(rows().find((r) => r.dataset.tool === 'video')!.className);
			expect(row.textContent).toContain('Stamp shapes onto a picture');
			expect(menu()!.textContent).not.toMatch(/plugin/i);
			expect(menu()!.querySelector('svg')).not.toBeNull();
			row.click();
			await settle();
			expect(get(tabsStore).tabs.find(isToolTab)?.tool).toBe('acme.stamp');
		} finally {
			off();
		}
	});
});

describe('closing a tool tab from the strip', () => {
	it('asks to discard the unsaved edits and closes on confirm', async () => {
		const id = tabsStore.openToolTab('image', { path: '/m/dusk.png', url: '/m/dusk.png' });
		tabsStore.updateToolTab(id, { dirty: 3 });
		await settle();
		const closeButtons = document.body.querySelectorAll<HTMLElement>('.close-button');
		closeButtons[1].click();
		await settle();
		expect(dialog()!.textContent).toContain('Discard 3 unsaved edits?');
		const confirm = [...dialog()!.querySelectorAll('button')].find((b) => /Discard and close/.test(b.textContent ?? ''))!;
		confirm.click();
		await settle();
		expect(get(tabsStore).tabs.some((tab) => tab.id === id)).toBe(false);
	});
});
