import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { get } from 'svelte/store';
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
const { tabsStore } = await import('$lib/stores/tabs');
const { stepBars } = await import('$lib/elixirs/stepBar');
const { embeddedTabFor } = await import('$lib/types/tabs');

let instance: ReturnType<typeof mount> | undefined;
let owner = '';

function host(overrides: Partial<ToolTabHost> = {}): ToolTabHost {
	return {
		tabId: owner,
		tool: 'elixir',
		source: null,
		state: null,
		activeTool: null,
		run: null,
		hint: null,
		phone: false,
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
		claimKey: vi.fn(() => owner),
		openToolTab: vi.fn(() => 'x'),
		...overrides
	};
}

const node = (over: Record<string, unknown> = {}) => ({
	id: 'n1',
	kind: 'preset',
	title: 'Orbit left',
	preset_id: 'wan',
	mode: 'i2v',
	overrides: {},
	session_mode: 'copy',
	session_snapshot: { prompt: 'orbit', formData: { steps: 20 } },
	...over
});

const record = (nodes: unknown[], revision = 3) => ({
	id: 'e1',
	name: 'Potion study',
	revision,
	run_mode: 'review',
	definition: { nodes, edges: [] },
	results: {},
	result_revision: 0
});

function render(props: ToolTabHost) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(ElixirTab, { target, props: { host: props } });
	flushSync();
}

const openButton = () => document.body.querySelector<HTMLButtonElement>('button[aria-label$="in preset"]');
const model = () => get(stepBars)[owner];
const embedded = () => embeddedTabFor(get(tabsStore).tabs, owner);

const settled = (ms = 950) => new Promise((resolve) => setTimeout(resolve, ms));

beforeEach(() => {
	for (const fn of Object.values(api)) fn.mockReset();
	tabsStore.reset();
	owner = tabsStore.openToolTab('elixir', null, { key: 'e1', state: { elixirId: 'e1' }, name: 'Potion study' });
	api.listElixirs.mockResolvedValue({ ok: true, data: { elixirs: [] } });
	api.listPresets.mockResolvedValue({
		ok: true,
		data: { presets: [{ preset_id: 'wan', mode: 'i2v', preset_name: 'Wan 2.2', mode_label: 'Image to video', inputs: {}, outputs: {} }] }
	});
	api.followedSessions.mockResolvedValue({ ok: true, data: { nodes: {} } });
	api.validateElixir.mockResolvedValue({ ok: true, data: { valid: true, issues: [] } });
	api.listRuns.mockResolvedValue({ ok: true, data: { runs: [] } });
	api.getElixir.mockResolvedValue({ ok: true, data: record([node()]) });
	api.updateElixir.mockImplementation(async (_id: string, body: { definition: unknown }) => ({ ok: true, data: { ...record([]), revision: 4, definition: body.definition } }));
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

describe('Open in preset from a step card', () => {
	it('puts an icon button on preset cards and none on a files card', async () => {
		api.getElixir.mockResolvedValue({ ok: true, data: record([node(), { id: 'm1', kind: 'media', assets: [], media_type: null }]) });
		render(host());
		await vi.waitFor(() => expect(openButton()).not.toBeNull());
		expect(document.body.querySelectorAll('button[aria-label$="in preset"]')).toHaveLength(1);
	});

	it('opens the step in the same tab without creating another tab', async () => {
		const tab = host();
		render(tab);
		await vi.waitFor(() => expect(openButton()).not.toBeNull());
		const before = get(tabsStore).tabs.length;
		openButton()!.click();
		await vi.waitFor(() => expect(model()).toBeTruthy());
		expect(embedded()?.embedded).toMatchObject({ nodeId: 'n1', persistence: 'local' });
		expect(embedded()).toMatchObject({ selectedPreset: 'wan', selectedMode: 'i2v', prompt: 'orbit' });
		expect(get(tabsStore).tabs.length).toBe(before + 1);
		expect(get(tabsStore).activeTabId).toBe(owner);
		expect(tab.setTitle).toHaveBeenLastCalledWith('Potion study › Orbit left');
		expect(model()).toMatchObject({ stepLabel: 'Orbit left', follow: false, index: 0 });
	});

	it('keeps the lane mounted while the step is open', async () => {
		render(host());
		await vi.waitFor(() => expect(openButton()).not.toBeNull());
		openButton()!.click();
		await vi.waitFor(() => expect(model()).toBeTruthy());
		expect(document.body.querySelector('[data-card-id="n1"]')).not.toBeNull();
	});

	it('Back with no edits returns to the lane and restores the tab title', async () => {
		const tab = host();
		render(tab);
		await vi.waitFor(() => expect(openButton()).not.toBeNull());
		openButton()!.click();
		await vi.waitFor(() => expect(model()).toBeTruthy());
		model().back();
		await vi.waitFor(() => expect(model()).toBeUndefined());
		expect(embedded()).toBeNull();
		expect(tab.setTitle).toHaveBeenLastCalledWith('Potion study');
	});

	it('marks the tab dirty on an edit and saves it to the node', async () => {
		const tab = host();
		render(tab);
		await vi.waitFor(() => expect(openButton()).not.toBeNull());
		openButton()!.click();
		await vi.waitFor(() => expect(model()).toBeTruthy());
		tabsStore.updateTab(embedded()!.id, { formPublished: true });
		await settled();
		tabsStore.updateTab(embedded()!.id, { prompt: 'orbit wider' });
		await vi.waitFor(() => expect(tab.setDirty).toHaveBeenLastCalledWith(1));
		expect(model().edits).toBe(1);

		model().save();
		await vi.waitFor(() => expect(api.updateElixir).toHaveBeenCalled());
		const body = api.updateElixir.mock.calls[0][1] as { expected_revision: number; definition: { nodes: Array<{ id: string; overrides: unknown }> } };
		expect(body.definition.nodes[0].overrides).toMatchObject({ prompt: 'orbit wider' });
		await vi.waitFor(() => expect(model()).toBeUndefined());
		expect(tab.setDirty).toHaveBeenLastCalledWith(0);
		expect(embedded()).toBeNull();
		await vi.waitFor(() => expect(document.body.textContent).toContain('Edited'));
	});

	it('asks before leaving with unsaved edits', async () => {
		render(host());
		await vi.waitFor(() => expect(openButton()).not.toBeNull());
		openButton()!.click();
		await vi.waitFor(() => expect(model()).toBeTruthy());
		tabsStore.updateTab(embedded()!.id, { formPublished: true });
		await settled();
		tabsStore.updateTab(embedded()!.id, { prompt: 'changed' });
		await vi.waitFor(() => expect(model().edits).toBe(1));
		model().back();
		await vi.waitFor(() => expect(model().confirm).toEqual({ reason: 'leave', target: null }));
		model().answerConfirm('discard');
		await vi.waitFor(() => expect(model()).toBeUndefined());
		expect(api.updateElixir).not.toHaveBeenCalled();
	});

	it('does not create or save any session in local mode', async () => {
		render(host());
		await vi.waitFor(() => expect(openButton()).not.toBeNull());
		openButton()!.click();
		await vi.waitFor(() => expect(model()).toBeTruthy());
		expect(embedded()?.selectedSessionId).toBeNull();
	});

	it('resumes the open step when the lane is mounted again', async () => {
		render(host());
		await vi.waitFor(() => expect(openButton()).not.toBeNull());
		openButton()!.click();
		await vi.waitFor(() => expect(model()).toBeTruthy());
		unmount(instance!);
		instance = undefined;
		await vi.waitFor(() => expect(model()).toBeUndefined());
		expect(embedded()).not.toBeNull();
		render(host());
		await vi.waitFor(() => expect(model()).toBeTruthy());
		expect(model().stepLabel).toBe('Orbit left');
	});
});
