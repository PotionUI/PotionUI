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

const beforeButton = () => document.body.querySelector<HTMLButtonElement>('button[aria-label^="Add a step before"]');

beforeEach(() => {
	for (const fn of Object.values(api)) fn.mockReset();
	api.listElixirs.mockResolvedValue({ ok: true, data: { elixirs: [] } });
	api.listPresets.mockResolvedValue({ ok: true, data: { presets: PRESETS } });
	api.followedSessions.mockResolvedValue({ ok: true, data: { nodes: {} } });
	api.validateElixir.mockResolvedValue({ ok: true, data: { valid: true, issues: [] } });
	api.listRuns.mockResolvedValue({ ok: true, data: { runs: [] } });
	api.getElixir.mockResolvedValue({ ok: true, data: record([node('orbit')]) });
	api.updateElixir.mockImplementation(async (_id: string, body: { definition: unknown }) => ({ ok: true, data: { ...record([]), revision: 4, definition: body.definition } }));
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

describe('adding a step before the start', () => {
	it('puts an add control before the start step and none before steps that follow', async () => {
		api.getElixir.mockResolvedValue({
			ok: true,
			data: record([node('orbit'), node('up', { preset_id: 'krea', mode: 't2i' })], [{ id: 'e', source: 'orbit', source_handle: 'videos', target: 'up', target_handle: 'x', iteration: 'each' }])
		});
		render();
		await vi.waitFor(() => expect(beforeButton()).not.toBeNull());
		expect(document.body.querySelectorAll('button[aria-label^="Add a step before"]')).toHaveLength(1);
	});

	it('offers files, the presets that make the needed input, and a prompt writer', async () => {
		render();
		await vi.waitFor(() => expect(beforeButton()).not.toBeNull());
		beforeButton()!.click();
		flushSync();
		const items = [...document.body.querySelectorAll<HTMLElement>('.lane-menu-item')].map((item) => item.textContent ?? '');
		expect(items.some((text) => text.includes('Files'))).toBe(true);
		expect(items.some((text) => text.includes('Krea'))).toBe(true);
		expect(items.some((text) => text.includes('Prompt writer'))).toBe(true);
	});

	it('adds the chosen step as the new start, wired to the old start, and saves', async () => {
		render();
		await vi.waitFor(() => expect(beforeButton()).not.toBeNull());
		beforeButton()!.click();
		flushSync();
		const krea = [...document.body.querySelectorAll<HTMLElement>('.lane-menu-item')].find((item) => item.textContent?.includes('Krea'))!;
		krea.click();
		await vi.waitFor(() => expect(api.updateElixir).toHaveBeenCalled());
		const body = api.updateElixir.mock.calls[0][1] as { definition: { nodes: Array<{ id: string; preset_id?: string }>; edges: Array<{ source: string; target: string; target_handle: string }> } };
		expect(body.definition.nodes.map((item) => item.id)[1]).toBe('orbit');
		expect(body.definition.nodes[0].preset_id).toBe('krea');
		expect(body.definition.edges).toEqual([expect.objectContaining({ source: body.definition.nodes[0].id, target: 'orbit', target_handle: 'first_frame' })]);
	});

	it('is also in the start card menu', async () => {
		render();
		await vi.waitFor(() => expect(document.body.querySelector('[data-card-id="orbit"]')).not.toBeNull());
		document.body.querySelector<HTMLButtonElement>('button[aria-label^="More for"]')!.click();
		flushSync();
		const names = [...document.body.querySelectorAll<HTMLElement>('.lane-menu-item')].map((item) => item.textContent ?? '');
		expect(names.some((text) => text.includes('Add a step before'))).toBe(true);
	});

	it('offers nothing before a start that takes only a prompt and has no writer slot', async () => {
		api.getElixir.mockResolvedValue({ ok: true, data: record([{ id: 'files', kind: 'media', title: null, media_type: 'image', assets: [] }]) });
		render();
		await vi.waitFor(() => expect(document.body.querySelector('[data-card-id="files"]')).not.toBeNull());
		expect(beforeButton()).toBeNull();
	});
});

describe('column resize handle', () => {
	it('is a keyboard focusable separator with the width in its label and a drag hint', async () => {
		render();
		await vi.waitFor(() => expect(document.body.querySelector('[role="separator"]')).not.toBeNull());
		const handle = document.body.querySelector<HTMLElement>('[role="separator"]')!;
		expect(handle.getAttribute('tabindex')).toBe('0');
		expect(handle.getAttribute('aria-label')).toBe('Width of column 1');
		expect(Number(handle.getAttribute('aria-valuenow'))).toBeGreaterThan(0);
	});

	it('uses the shared pane handle strip and grip and marks the column it drags', async () => {
		render();
		await vi.waitFor(() => expect(document.body.querySelector('[role="separator"]')).not.toBeNull());
		const handle = document.body.querySelector<HTMLElement>('[role="separator"]')!;
		expect(handle.classList.contains('cursor-col-resize')).toBe(true);
		expect(handle.querySelector('span[aria-hidden="true"]')).not.toBeNull();
		expect(handle.getAttribute('data-dragging')).toBe('false');
		handle.setPointerCapture = () => {};
		handle.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, clientX: 100 }));
		await vi.waitFor(() => expect(handle.getAttribute('data-dragging')).toBe('true'));
	});
});

describe('connectors', () => {
	it('a leaf card draws no stub into nothing when it has no add control after it', async () => {
		render(true);
		await vi.waitFor(() => expect(document.body.querySelector('[data-lane-node="orbit"]')).not.toBeNull());
		expect(document.body.querySelector('[data-lane-node="orbit"] > .cw')!.classList.contains('open-end')).toBe(true);
	});

	it('a leaf card keeps its stub when the add control follows it', async () => {
		render(false);
		await vi.waitFor(() => expect(document.body.querySelector('[data-lane-node="orbit"]')).not.toBeNull());
		expect(document.body.querySelector('[data-lane-node="orbit"] > .cw')!.classList.contains('open-end')).toBe(false);
	});
});
