import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { ToolTabHost } from '$lib/tool-tabs/types';

const api = {
	listElixirs: vi.fn(),
	createElixir: vi.fn(),
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

function host(overrides: Partial<ToolTabHost> = {}): ToolTabHost {
	return {
		tabId: 'tab-1',
		tool: 'elixir',
		source: null,
		state: null,
		activeTool: null,
		run: null,
		hint: null,
		phone: false,
		key: null,
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
		openToolTab: vi.fn(() => 'x'),
		...overrides
	};
}

function render(props: ToolTabHost) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(ElixirTab, { target, props: { host: props } });
	flushSync();
}

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
	api.listElixirs.mockResolvedValue({ ok: true, data: { elixirs: [{ id: 'e1', name: 'Potion study', node_count: 0, updated_at: '2026-11-04T09:00:00Z' }] } });
	api.listPresets.mockResolvedValue({ ok: true, data: { presets: [] } });
	api.followedSessions.mockResolvedValue({ ok: true, data: { nodes: {} } });
	api.validateElixir.mockResolvedValue({ ok: true, data: { valid: true, issues: [] } });
	api.listRuns.mockResolvedValue({ ok: true, data: { runs: [] } });
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

describe('the Elixir tool tab', () => {
	it('with nothing chosen, shows the picker and claims the picked elixir for this tab', async () => {
		const tab = host();
		render(tab);
		expect(document.body.querySelector('[data-testid="elixir-chooser"]')?.textContent).toContain('Open an elixir');
		await vi.waitFor(() => expect(document.body.querySelector('[data-elixir-id="e1"]')).not.toBeNull());
		document.body.querySelector<HTMLButtonElement>('[data-elixir-id="e1"]')!.click();
		expect(tab.claimKey).toHaveBeenCalledWith('e1', { elixirId: 'e1' }, 'Potion study');
	});

	it('an empty elixir shows one toolbar without Back, the start options, and names the tab', async () => {
		api.getElixir.mockResolvedValue({ ok: true, data: record([]) });
		const tab = host({ key: 'e1' });
		render(tab);
		await vi.waitFor(() => expect(document.body.querySelector('[data-testid="elixir-empty"]')).not.toBeNull());
		expect(document.body.querySelectorAll('.elixir-header')).toHaveLength(1);
		expect(document.body.querySelector('[aria-label="Back to all elixirs"]')).toBeNull();
		expect(document.body.querySelector<HTMLInputElement>('#elixir-name')?.value).toBe('Potion study');
		expect(document.body.textContent).toContain('Start your elixir');
		expect(tab.setTitle).toHaveBeenCalledWith('Potion study');
		expect(tab.setStatus).toHaveBeenLastCalledWith(null);
		const brew = [...document.body.querySelectorAll<HTMLButtonElement>('.elixir-header button')].find((button) => button.textContent?.trim() === 'Brew');
		expect(brew?.disabled).toBe(true);
	});

	it('Preset step in the empty state adds the first step and saves', async () => {
		api.getElixir.mockResolvedValue({ ok: true, data: record([]) });
		api.updateElixir.mockImplementation(async (_id: string, body: { definition: unknown }) => ({ ok: true, data: { ...record([]), revision: 4, definition: body.definition } }));
		render(host({ key: 'e1' }));
		await vi.waitFor(() => expect(document.body.querySelector('[data-testid="elixir-empty"]')).not.toBeNull());
		[...document.body.querySelectorAll<HTMLButtonElement>('[data-testid="elixir-empty"] button')][0].click();
		await vi.waitFor(() => expect(api.updateElixir).toHaveBeenCalled());
		const body = api.updateElixir.mock.calls[0][1] as { expected_revision: number; definition: { nodes: Array<{ kind: string }> } };
		expect(body.expected_revision).toBe(3);
		expect(body.definition.nodes.map((node) => node.kind)).toEqual(['preset']);
		await vi.waitFor(() => expect(document.body.querySelector('[data-testid="elixir-empty"]')).toBeNull());
	});

	it('reports a running brew to the tab strip as done of total steps', async () => {
		api.getElixir.mockResolvedValue({ ok: true, data: record([{ id: 'n1', kind: 'preset', title: 'Orbit' }, { id: 'n2', kind: 'preset', title: 'Upscale' }]) });
		api.listRuns.mockResolvedValue({ ok: true, data: { runs: [{ id: 'r1', status: 'running', created_at: '2026-11-04T09:00:00Z' }] } });
		api.getRun.mockResolvedValue({
			ok: true,
			data: { id: 'r1', status: 'running', created_at: '2026-11-04T09:00:00Z', node_states: { n1: { status: 'done' }, n2: { status: 'running' } }, invocations: [], checkpoints: {}, graph_edges: [] }
		});
		const tab = host({ key: 'e1' });
		render(tab);
		await vi.waitFor(() => expect(tab.setStatus).toHaveBeenLastCalledWith(expect.objectContaining({ tone: 'signal', count: [1, 2] })));
	});

	it('an elixir that is gone offers to open another', async () => {
		api.getElixir.mockResolvedValue({ ok: false, error: { code: 'elixir_not_found', message: 'gone' } });
		render(host({ key: 'e1' }));
		await vi.waitFor(() => expect(document.body.textContent).toContain('This elixir does not exist or is not yours.'));
		expect(document.body.textContent).toContain('Open another elixir');
	});
});
