import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import type { ToolTab, ToolTabStatus } from '$lib/types/tabs';
import type { ElixirsApi } from '$lib/services/api/elixirs';

vi.mock('$lib/services/api/elixirs', () => ({ elixirsApi: () => ({}) }));

const { watchElixirTab } = await import('./watch');

const definition = {
	nodes: [
		{ id: 'files', kind: 'media' },
		{ id: 'orbit', kind: 'preset' },
		{ id: 'upscale', kind: 'preset' }
	],
	edges: []
};

function runWith(status: string, done: string[]) {
	return {
		id: 'r1',
		status,
		node_states: Object.fromEntries(['files', 'orbit', 'upscale'].map((id) => [id, { status: done.includes(id) ? 'done' : 'running' }])),
		invocations: [],
		checkpoints: {}
	};
}

function fakeApi(runs: Array<ReturnType<typeof runWith>>) {
	let index = 0;
	return {
		listRuns: vi.fn(async () => ({ ok: true, data: { runs: [{ id: 'r1', status: runs[Math.min(index, runs.length - 1)].status }] } })),
		getElixir: vi.fn(async () => ({ ok: true, data: { definition } })),
		getRun: vi.fn(async () => ({ ok: true, data: runs[Math.min(index++, runs.length - 1)] }))
	} as unknown as ElixirsApi;
}

function tab(status: ToolTabStatus | null = null): ToolTab {
	return { kind: 'tool', id: 't1', name: 'Potion study', tool: 'elixir', source: null, state: { elixirId: 'e1' }, key: 'e1', status };
}

describe('background elixir tab watcher', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	it('reports progress of a brew, then done once it finishes', async () => {
		const seen: Array<ToolTabStatus | null> = [];
		const api = fakeApi([runWith('running', ['files', 'orbit']), runWith('completed', ['files', 'orbit', 'upscale'])]);
		const stop = watchElixirTab({ tab: tab(), setStatus: (status) => seen.push(status) }, () => api);
		await vi.advanceTimersByTimeAsync(0);
		expect(seen.at(-1)).toMatchObject({ tone: 'signal', count: [1, 2], pulse: true });
		await vi.advanceTimersByTimeAsync(3000);
		expect(seen.at(-1)).toEqual({ tone: 'success', label: 'Brew done' });
		stop();
		const calls = (api.getRun as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
		await vi.advanceTimersByTimeAsync(60000);
		expect((api.getRun as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(calls);
	});

	it('leaves an idle elixir alone', async () => {
		const seen: Array<ToolTabStatus | null> = [];
		const api = fakeApi([runWith('completed', ['orbit', 'upscale'])]);
		const stop = watchElixirTab({ tab: tab(), setStatus: (status) => seen.push(status) }, () => api);
		await vi.advanceTimersByTimeAsync(0);
		expect(seen).toEqual([]);
		expect(api.getRun).not.toHaveBeenCalled();
		stop();
	});

	it('clears the brewing mark of a brew that was stopped', async () => {
		const seen: Array<ToolTabStatus | null> = [];
		const api = fakeApi([runWith('cancelled', [])]);
		const stop = watchElixirTab({ tab: tab({ tone: 'signal', label: 'Brewing', pulse: true }), setStatus: (status) => seen.push(status) }, () => api);
		await vi.advanceTimersByTimeAsync(0);
		expect(seen).toEqual([null]);
		stop();
	});

	it('does nothing for a tab with no elixir yet', () => {
		const setStatus = vi.fn();
		const stop = watchElixirTab({ tab: { ...tab(), key: null, state: null }, setStatus }, () => fakeApi([]));
		stop();
		expect(setStatus).not.toHaveBeenCalled();
	});
});
