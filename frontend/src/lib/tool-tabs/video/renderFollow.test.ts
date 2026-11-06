import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MediaToolRun } from '$lib/media-tools/types';
import { detachRender, followRender, isFollowing, settleRender, stopRender, type RenderListener } from './renderFollow';
import type { RenderRunApi } from './renderRun';

function run(id: string, status: MediaToolRun['status'], progress = 0): MediaToolRun {
	return { run_id: id, status, progress, label: '', items: [], replaced: false, error: null };
}

function fakeApi() {
	const states: Record<string, MediaToolRun> = {};
	const api: RenderRunApi & { get: ReturnType<typeof vi.fn> } = {
		get: vi.fn(async (id: string) => states[id] ?? run(id, 'running', 0.1)),
		list: async () => Object.values(states)
	};
	return { api, states };
}

function listener() {
	const log: string[] = [];
	const value: RenderListener = {
		progress: (r) => log.push(`progress:${r.run_id}`),
		done: (r) => log.push(`done:${r.run_id}:${r.status}`)
	};
	return { log, value };
}

const flush = async (rounds = 10) => {
	for (let i = 0; i < rounds; i++) await new Promise((resolve) => setTimeout(resolve, 0));
};

afterEach(() => {
	vi.useRealTimers();
	stopRender('tab');
});

describe('followRender', () => {
	it('swaps the listener when the same run is followed again instead of starting a second tracker', async () => {
		vi.useFakeTimers();
		const { api } = fakeApi();
		const first = listener();
		const second = listener();
		followRender('tab', 'r1', api, first.value, () => undefined);
		await vi.advanceTimersByTimeAsync(0);
		followRender('tab', 'r1', api, second.value, () => undefined);
		await vi.advanceTimersByTimeAsync(400);
		expect(api.get).toHaveBeenCalledTimes(2);
		expect(first.log).toEqual(['progress:r1']);
		expect(second.log).toEqual(['progress:r1']);
		expect(isFollowing('tab', 'r1')).toBe(true);
	});

	it('hands the result to the fallback once the editor let go', async () => {
		const { api, states } = fakeApi();
		const editor = listener();
		const fallback = vi.fn();
		states.r1 = run('r1', 'completed', 1);
		followRender('tab', 'r1', api, editor.value, fallback);
		detachRender('tab', editor.value);
		await flush();
		expect(fallback).toHaveBeenCalledWith(expect.objectContaining({ run_id: 'r1', status: 'completed' }));
		expect(editor.log).toEqual([]);
		expect(isFollowing('tab')).toBe(false);
	});

	it('settles a cancelled run at once and delivers it a single time', async () => {
		vi.useFakeTimers();
		const { api, states } = fakeApi();
		const editor = listener();
		followRender('tab', 'r1', api, editor.value, () => undefined);
		await vi.advanceTimersByTimeAsync(0);
		expect(settleRender('tab', run('r1', 'cancelled'))).toBe(true);
		states.r1 = run('r1', 'completed', 1);
		await vi.advanceTimersByTimeAsync(2000);
		expect(editor.log).toEqual(['progress:r1', 'done:r1:cancelled']);
		expect(settleRender('tab', run('r1', 'cancelled'))).toBe(false);
	});

	it('drops the first run when a different one is followed for the tab', async () => {
		vi.useFakeTimers();
		const { api, states } = fakeApi();
		const editor = listener();
		followRender('tab', 'r1', api, editor.value, () => undefined);
		await vi.advanceTimersByTimeAsync(0);
		followRender('tab', 'r2', api, editor.value, () => undefined);
		const r1Polls = () => api.get.mock.calls.filter(([id]) => id === 'r1').length;
		const before = r1Polls();
		await vi.advanceTimersByTimeAsync(2000);
		expect(r1Polls()).toBe(before);
		states.r1 = run('r1', 'completed', 1);
		states.r2 = run('r2', 'completed', 1);
		await vi.advanceTimersByTimeAsync(1000);
		expect(editor.log.filter((line) => line.startsWith('done'))).toEqual(['done:r2:completed']);
	});

	it('stops polling when told to, and when the tab is gone', async () => {
		vi.useFakeTimers();
		const { api } = fakeApi();
		const editor = listener();
		followRender('tab', 'r1', api, editor.value, () => undefined);
		await vi.advanceTimersByTimeAsync(0);
		stopRender('tab');
		const calls = api.get.mock.calls.length;
		await vi.advanceTimersByTimeAsync(5000);
		expect(api.get.mock.calls.length).toBe(calls);
		expect(isFollowing('tab')).toBe(false);

		let alive = true;
		followRender('tab', 'r2', api, editor.value, () => undefined, () => alive);
		await vi.advanceTimersByTimeAsync(0);
		alive = false;
		await vi.advanceTimersByTimeAsync(400);
		const after = api.get.mock.calls.length;
		await vi.advanceTimersByTimeAsync(5000);
		expect(api.get.mock.calls.length).toBe(after);
		expect(isFollowing('tab')).toBe(false);
	});
});
