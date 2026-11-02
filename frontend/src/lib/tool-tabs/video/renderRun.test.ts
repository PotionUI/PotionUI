import { describe, expect, it, vi } from 'vitest';
import type { MediaToolRun } from '$lib/media-tools/types';
import { findRun, percentOf, secondsLeft, trackRun, type RenderRunApi } from './renderRun';

function run(status: MediaToolRun['status'], progress = 0, extra: Partial<MediaToolRun> = {}): MediaToolRun {
	return { run_id: 'r1', status, progress, label: '', items: [], replaced: false, error: null, ...extra };
}

function api(sequence: Array<MediaToolRun | Error>): RenderRunApi & { get: ReturnType<typeof vi.fn> } {
	const queue = [...sequence];
	return {
		get: vi.fn(async () => {
			const next = queue.shift() ?? sequence[sequence.length - 1];
			if (next instanceof Error) throw next;
			return next;
		}),
		list: async () => []
	};
}

const noWait = async () => undefined;

describe('trackRun', () => {
	it('reports progress until the run finishes', async () => {
		const seen: number[] = [];
		const client = api([run('queued'), run('running', 0.42), run('completed', 1, { items: [{ id: 'i1' } as never] })]);
		const done = await trackRun('r1', { api: client, signal: new AbortController().signal, onProgress: (r) => seen.push(r.progress), wait: noWait });
		expect(seen).toEqual([0, 0.42]);
		expect(done?.status).toBe('completed');
		expect(done?.items).toHaveLength(1);
	});

	it('stops quietly when the tab lets go', async () => {
		const controller = new AbortController();
		const client = api([run('running', 0.1)]);
		const done = await trackRun('r1', {
			api: client,
			signal: controller.signal,
			onProgress: () => controller.abort(),
			wait: noWait
		});
		expect(done).toBeNull();
		expect(client.get).toHaveBeenCalledTimes(1);
	});

	it('turns a lost run into a failure and retries a network blip', async () => {
		const missing = Object.assign(new Error('gone'), { response: { status: 404 } });
		expect((await trackRun('r1', { api: api([missing]), signal: new AbortController().signal, onProgress: () => undefined, wait: noWait }))?.error?.code).toBe('run_not_found');
		const blip = new Error('offline');
		const client = api([blip, run('cancelled')]);
		const done = await trackRun('r1', { api: client, signal: new AbortController().signal, onProgress: () => undefined, wait: noWait });
		expect(done?.status).toBe('cancelled');
		expect(client.get).toHaveBeenCalledTimes(2);
	});
});

describe('run helpers', () => {
	it('finds a run in the runs list', async () => {
		const client: RenderRunApi = { get: async () => run('running'), list: async () => [run('running', 0.3), { ...run('queued'), run_id: 'r2' }] };
		expect((await findRun(client, 'r2'))?.status).toBe('queued');
		expect(await findRun(client, 'nope')).toBeNull();
	});

	it('turns progress into a percentage and an estimate', () => {
		expect(percentOf({ progress: 0.42 })).toBeCloseTo(42, 6);
		expect(percentOf({ progress: Number.NaN })).toBe(0);
		expect(secondsLeft(0.5, 10_000)).toBeCloseTo(10, 6);
		expect(secondsLeft(0, 10_000)).toBeNull();
	});
});
