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

function host(): ToolTabHost {
	return {
		tabId: 'tab-1',
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
		claimKey: vi.fn(() => 'tab-1'),
		openToolTab: vi.fn(() => 'x')
	};
}

const photos = {
	id: 'photos',
	kind: 'media',
	title: 'Potion photos',
	media_type: 'image',
	assets: [{ id: 'a1', url: '/a1.png', media_type: 'image', filename: 'a1.png' }]
};

const writer = { id: 'writer', kind: 'llm', title: 'Describe', instruction: 'Describe it', instruction_chips: {}, target_node_id: null, pinned: {}, assets: [] };

const record = (nodes: unknown[]) => ({
	id: 'e1',
	name: 'Potion study',
	revision: 3,
	run_mode: 'review',
	definition: { nodes, edges: [] },
	results: {},
	result_revision: 0
});

beforeEach(() => {
	for (const fn of Object.values(api)) fn.mockReset();
	api.listElixirs.mockResolvedValue({ ok: true, data: { elixirs: [] } });
	api.listPresets.mockResolvedValue({ ok: true, data: { presets: [] } });
	api.followedSessions.mockResolvedValue({ ok: true, data: { nodes: {} } });
	api.validateElixir.mockResolvedValue({ ok: true, data: { valid: true, issues: [] } });
	api.listRuns.mockResolvedValue({ ok: true, data: { runs: [] } });
	api.getElixir.mockResolvedValue({ ok: true, data: record([photos, writer]) });
	api.updateElixir.mockImplementation(async (_id: string, body: { definition: unknown }) => ({ ok: true, data: { ...record([]), revision: 4, definition: body.definition } }));
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

describe('a writer that looks at nothing yet', () => {
	it('opens the Looks at menu and connects the chosen files', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(ElixirTab, { target, props: { host: host() } });
		flushSync();
		await vi.waitFor(() => expect(document.body.querySelector('[data-card-id="writer"]')).not.toBeNull());
		const slot = [...document.body.querySelectorAll<HTMLButtonElement>('[data-card-id="writer"] button')].find((button) => /^Looks at: Nothing/.test(button.getAttribute('aria-label') ?? button.textContent ?? ''))!;
		slot.click();
		flushSync();
		await vi.waitFor(() => expect(document.body.querySelectorAll('.lane-menu-item').length).toBeGreaterThan(0));
		await new Promise((resolve) => setTimeout(resolve, 20));
		const files = [...document.body.querySelectorAll<HTMLElement>('.lane-menu-item')].find((item) => item.textContent?.includes('Potion photos'))!;
		files.click();
		await vi.waitFor(() => expect(api.updateElixir).toHaveBeenCalled());
		const body = api.updateElixir.mock.calls[0][1] as { definition: { edges: Array<{ source: string; target: string }> } };
		expect(body.definition.edges).toEqual([expect.objectContaining({ source: 'photos', target: 'writer' })]);
	});
});
