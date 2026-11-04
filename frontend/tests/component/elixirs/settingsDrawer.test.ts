import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { ToolTabHost } from '$lib/tool-tabs/types';

const api = {
	listElixirs: vi.fn(),
	getElixir: vi.fn(),
	listPresets: vi.fn(),
	followedSessions: vi.fn(),
	validateElixir: vi.fn(),
	listRuns: vi.fn(),
	getRun: vi.fn(),
	updateElixir: vi.fn()
};
vi.mock('$lib/services/api/elixirs', () => ({ elixirsApi: () => api }));

if (!globalThis.CSS?.escape) vi.stubGlobal('CSS', { ...(globalThis.CSS ?? {}), escape: (value: string) => value });

const { default: ElixirTab } = await import('$lib/components/elixirs/ElixirTab.svelte');

let instance: ReturnType<typeof mount> | undefined;

function host(phone = false): ToolTabHost {
	return {
		tabId: 'tab-1',
		tool: 'elixir',
		source: null,
		state: null,
		activeTool: null,
		run: null,
		hint: null,
		phone,
		key: 'e1',
		setState: vi.fn(),
		setDirty: vi.fn(),
		setTitle: vi.fn(),
		setThumbnail: vi.fn(),
		setSource: vi.fn(),
		replaceSource: vi.fn(),
		setActiveTool: vi.fn(),
		setRun: vi.fn(),
		clearHint: vi.fn(),
		setActions: vi.fn(),
		setStatus: vi.fn(),
		claimKey: vi.fn(() => 'tab-1'),
		openToolTab: vi.fn(() => 'x')
	};
}

const node = (id: string, over: Record<string, unknown> = {}) => ({
	id,
	kind: 'preset',
	title: id,
	preset_id: 'wan',
	mode: 'i2v',
	overrides: {},
	session_snapshot: null,
	...over
});

const record = (nodes: unknown[], edges: unknown[] = []) => ({
	id: 'e1',
	name: 'Potion study',
	revision: 3,
	run_mode: 'review',
	definition: { nodes, edges },
	results: {},
	result_revision: 0
});

const PRESETS = [
	{ preset_id: 'wan', mode: 'i2v', preset_name: 'Wan', mode_label: 'Image to video', prompt: true, inputs: { first_frame: { label: 'First frame', media_type: 'image' } }, outputs: { videos: { label: 'Videos', media_type: 'video' } } },
	{ preset_id: 'krea', mode: 't2i', preset_name: 'Krea', mode_label: 'Text to image', prompt: true, inputs: {}, outputs: { images: { label: 'Images', media_type: 'image' } } }
];

function render(phone = false) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(ElixirTab, { target, props: { host: host(phone) } });
	flushSync();
}

const settingsButton = () => document.body.querySelector<HTMLButtonElement>('button[aria-label="orbit settings"]');
const drawer = () => document.body.querySelector<HTMLElement>('[data-testid="elixir-drawer"]');
const scrim = () => document.body.querySelector<HTMLElement>('.drawer-scrim');

beforeEach(() => {
	for (const fn of Object.values(api)) fn.mockReset();
	api.listElixirs.mockResolvedValue({ ok: true, data: { elixirs: [] } });
	api.listPresets.mockResolvedValue({ ok: true, data: { presets: PRESETS } });
	api.followedSessions.mockResolvedValue({ ok: true, data: { nodes: {} } });
	api.validateElixir.mockResolvedValue({ ok: true, data: { valid: true, issues: [] } });
	api.listRuns.mockResolvedValue({ ok: true, data: { runs: [] } });
	api.getElixir.mockResolvedValue({
		ok: true,
		data: record([node('orbit', { kind: 'llm', title: 'orbit', preset_id: null, mode: null, instruction: 'Old', instruction_chips: {}, pinned: {}, assets: [] })])
	});
	api.updateElixir.mockImplementation(async (_id: string, body: { definition: unknown }) => ({ ok: true, data: { ...record([]), revision: 4, definition: body.definition } }));
	localStorage.clear();
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

async function openDrawer() {
	render();
	await vi.waitFor(() => expect(settingsButton()).not.toBeNull());
	settingsButton()!.click();
	flushSync();
	await vi.waitFor(() => expect(drawer()).not.toBeNull());
}

describe('the settings drawer', () => {
	it('is a standard drawer with a scrim, and a click on the scrim closes it', async () => {
		await openDrawer();
		expect(scrim()).not.toBeNull();
		scrim()!.click();
		flushSync();
		expect(drawer()).toBeNull();
		expect(scrim()).toBeNull();
	});

	it('closes on Escape', async () => {
		await openDrawer();
		drawer()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();
		expect(drawer()).toBeNull();
	});

	it('commits a typed writer instruction before a scrim click closes it', async () => {
		await openDrawer();
		const box = document.body.querySelector<HTMLElement>('[data-testid="elixir-node-instruction"] [contenteditable], [data-testid="elixir-node-instruction"] textarea')!;
		expect(box).toBeTruthy();
		if (box instanceof HTMLTextAreaElement) box.value = 'Typed';
		else box.textContent = 'Typed';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		scrim()!.click();
		flushSync();
		await vi.waitFor(() => expect(api.updateElixir).toHaveBeenCalled(), { timeout: 3000 });
		const sent = api.updateElixir.mock.calls[0][1] as { definition: { nodes: Array<{ instruction: string }> } };
		expect(sent.definition.nodes[0].instruction).toContain('Typed');
	});

	it('keeps its resizable width and resets it on double click', async () => {
		await openDrawer();
		expect(drawer()!.style.getPropertyValue('--side-drawer-width')).toBe('640px');
		const handle = document.body.querySelector<HTMLElement>('[aria-label="Width of the settings panel"]')!;
		handle.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true }));
		flushSync();
		expect(drawer()!.style.getPropertyValue('--side-drawer-width')).toBe('656px');
		handle.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		flushSync();
		expect(drawer()!.style.getPropertyValue('--side-drawer-width')).toBe('640px');
	});

	it('stays open when the Variables modal opens on top and Escape is pressed inside it', async () => {
		await openDrawer();
		const variables = [...document.body.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes('Variables'))!;
		variables.click();
		flushSync();
		const modal = document.body.querySelector<HTMLElement>('[role="listbox"][aria-label="Variables"]');
		expect(modal).not.toBeNull();
		expect(drawer()).not.toBeNull();
		modal!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();
		expect(drawer()).not.toBeNull();
	});
});
