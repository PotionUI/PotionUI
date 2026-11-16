import { get, writable } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EditorRun, StartRunBody } from '$lib/services/api/editorTasks';
import { LOST_RUN_MESSAGE, createRunEngine, previewUrl, type RunMessage, type RunPorts, type RunSocket, type RunView, type RunViews } from './runEngine';

function run(over: Partial<EditorRun> = {}): EditorRun {
	return { id: 'er_1', task: 'edit_region', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: 'gen_1', progress: 0, error: null, soft: false, rect: null, candidates: [], ...over };
}

function refusal(status: number, error: string) {
	return { response: { status, data: { detail: { error } } } };
}

function setup(connected = true) {
	let handler: ((message: RunMessage) => void) | null = null;
	let listener: ((connected: boolean) => void) | null = null;
	const state = { connected };
	const socket: RunSocket = {
		subscribe: vi.fn((_id: string, next: (message: RunMessage) => void) => {
			handler = next;
			return () => {
				handler = null;
			};
		}),
		connected: () => state.connected,
		onConnection: (next) => {
			listener = next;
			return () => (listener = null);
		},
		close: vi.fn()
	};
	const ports = {
		start: vi.fn(async (_task: string, _body: StartRunBody) => run()),
		get: vi.fn(async (_id: string) => run({ status: 'running', progress: 0.2 })),
		cancel: vi.fn(async (_id: string) => run({ status: 'cancelled' })),
		socket: vi.fn(() => socket)
	};
	const settled: RunView[] = [];
	const views = writable<RunViews>({});
	const engine = createRunEngine(ports as RunPorts, { views, pollMs: 1000, safetyMs: 10000, onSettled: (view) => settled.push(view) });
	return { engine, ports, socket, settled, views, state, send: (message: RunMessage) => handler?.(message), hasHandler: () => handler !== null, connection: (value: boolean) => listener?.(value) };
}

const body = {} as StartRunBody;

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('run engine', () => {
	it('starts a run, tracks it on the socket and settles from a socket update without polling', async () => {
		const t = setup();
		await t.engine.start('edit_region', body, 's1');
		expect(t.ports.start).toHaveBeenCalledWith('edit_region', body);
		expect(t.socket.subscribe).toHaveBeenCalledWith('gen_1', expect.any(Function));
		t.send({ type: 'editor_run_update', generation_id: 'gen_1', run: run({ status: 'running', progress: 0.4 }) });
		expect(get(t.views).er_1).toMatchObject({ status: 'running', progress: 0.4, stepId: 's1' });
		t.send({ type: 'editor_run_update', generation_id: 'gen_1', run: run({ status: 'done', candidates: [{ patch: { item_id: 'p' }, url: '/a.png', seed: 1 }] }) });
		expect(t.settled).toHaveLength(1);
		expect(t.settled[0]).toMatchObject({ status: 'done', progress: 1 });
		await vi.advanceTimersByTimeAsync(60000);
		expect(t.ports.get).not.toHaveBeenCalled();
		expect(t.engine.tracking('er_1')).toBe(false);
	});

	it('keeps the live preview from workbench updates', async () => {
		const t = setup();
		await t.engine.start('edit_region', body, 's1');
		t.send({ type: 'workbench_update', generation_id: 'gen_1', image: 'AAAA' });
		expect(get(t.views).er_1.preview).toBe('data:image/png;base64,AAAA');
		t.send({ type: 'generation_status', generation_id: 'gen_1', progress: 55 });
		expect(get(t.views).er_1.progress).toBeCloseTo(0.55);
		expect(previewUrl('/api/media/x.png')).toBe('/api/media/x.png');
		expect(previewUrl(5)).toBeNull();
	});

	it('polls when the socket is down and stops once the run is terminal', async () => {
		const t = setup(false);
		t.ports.get.mockResolvedValueOnce(run({ status: 'running', progress: 0.3 })).mockResolvedValueOnce(run({ status: 'done' }));
		await t.engine.start('edit_region', body, 's1');
		await vi.advanceTimersByTimeAsync(1000);
		expect(get(t.views).er_1.progress).toBe(0.3);
		await vi.advanceTimersByTimeAsync(1000);
		expect(t.settled[0].status).toBe('done');
		await vi.advanceTimersByTimeAsync(10000);
		expect(t.ports.get).toHaveBeenCalledTimes(2);
	});

	it('recovers from a missed message with a slow safety poll', async () => {
		const t = setup(true);
		t.ports.get.mockResolvedValue(run({ status: 'done' }));
		await t.engine.start('edit_region', body, 's1');
		await vi.advanceTimersByTimeAsync(9000);
		expect(t.ports.get).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(1500);
		expect(t.ports.get).toHaveBeenCalledTimes(1);
		expect(t.settled[0].status).toBe('done');
	});

	it('never runs more than one poll loop per run', async () => {
		const t = setup(false);
		await t.engine.start('edit_region', body, 's1');
		t.engine.adopt(run(), 's1');
		t.engine.adopt(run(), 's1');
		await t.engine.attach('er_1', 's1');
		expect(t.engine.count()).toBe(1);
		await vi.advanceTimersByTimeAsync(1000);
		expect(t.ports.get).toHaveBeenCalledTimes(1);
	});

	it('does not stack ticks while a poll is slow', async () => {
		const t = setup(false);
		let release: (value: EditorRun) => void = () => undefined;
		t.ports.get.mockImplementationOnce(() => new Promise<EditorRun>((resolve) => (release = resolve)));
		await t.engine.start('edit_region', body, 's1');
		await vi.advanceTimersByTimeAsync(5000);
		expect(t.ports.get).toHaveBeenCalledTimes(1);
		release(run({ status: 'running' }));
		await vi.advanceTimersByTimeAsync(1000);
		expect(t.ports.get).toHaveBeenCalledTimes(2);
	});

	it('keeps polling through a network error', async () => {
		const t = setup(false);
		t.ports.get.mockRejectedValueOnce({ message: 'Network Error' }).mockResolvedValueOnce(run({ status: 'done' }));
		await t.engine.start('edit_region', body, 's1');
		await vi.advanceTimersByTimeAsync(2000);
		expect(t.settled[0].status).toBe('done');
	});

	it('cancels a run and settles it as cancelled', async () => {
		const t = setup();
		await t.engine.start('edit_region', body, 's1');
		expect(await t.engine.cancel('er_1')).toEqual({ ok: true });
		expect(t.settled[0].status).toBe('cancelled');
		expect(t.hasHandler()).toBe(false);
	});

	it('leaves the run going when the cancel is refused', async () => {
		const t = setup();
		t.ports.cancel.mockRejectedValueOnce(refusal(400, 'cancel_failed'));
		await t.engine.start('edit_region', body, 's1');
		expect(await t.engine.cancel('er_1')).toEqual({ ok: false, reason: 'refused' });
		expect(t.engine.tracking('er_1')).toBe(true);
		expect(t.settled).toHaveLength(0);
		t.send({ type: 'editor_run_update', generation_id: 'gen_1', run: run({ status: 'running', progress: 0.5 }) });
		expect(get(t.views).er_1.status).toBe('running');
	});

	it('reports a cancel that failed for another reason', async () => {
		const t = setup();
		t.ports.cancel.mockRejectedValueOnce({ message: 'Network Error' });
		await t.engine.start('edit_region', body, 's1');
		expect(await t.engine.cancel('er_1')).toEqual({ ok: false, reason: 'failed' });
	});

	it('settles a failed run with its reason', async () => {
		const t = setup();
		await t.engine.start('edit_region', body, 's1');
		t.send({ type: 'editor_run_update', generation_id: 'gen_1', run: run({ status: 'failed', error: 'The model ran out of memory' }) });
		expect(t.settled[0]).toMatchObject({ status: 'failed', error: 'The model ran out of memory' });
	});

	it('re-attaches after a reload and follows the run to its end', async () => {
		const t = setup();
		await t.engine.attach('er_1', 's1', 'running');
		expect(t.ports.get).toHaveBeenCalledWith('er_1');
		expect(t.engine.tracking('er_1')).toBe(true);
		t.send({ type: 'editor_run_update', generation_id: 'gen_1', run: run({ status: 'done' }) });
		expect(t.settled[0].status).toBe('done');
	});

	it('settles a run the server reports finished at once', async () => {
		const t = setup();
		t.ports.get.mockResolvedValueOnce(run({ status: 'failed', error: 'The app restarted while this was generating' }));
		await t.engine.attach('er_1', 's1');
		expect(t.settled[0]).toMatchObject({ status: 'failed', error: 'The app restarted while this was generating' });
		expect(t.engine.tracking('er_1')).toBe(false);
	});

	it('settles a run the server lost as failed', async () => {
		const t = setup();
		t.ports.get.mockRejectedValueOnce(refusal(404, 'run_not_found'));
		await t.engine.attach('er_1', 's1');
		expect(t.settled[0]).toMatchObject({ status: 'failed', error: LOST_RUN_MESSAGE });
	});

	it('keeps trying to re-attach while offline', async () => {
		const t = setup(false);
		t.ports.get.mockRejectedValueOnce({ message: 'Network Error' }).mockResolvedValueOnce(run({ status: 'done' }));
		await t.engine.attach('er_1', 's1');
		expect(t.engine.tracking('er_1')).toBe(true);
		await vi.advanceTimersByTimeAsync(1000);
		expect(t.settled[0].status).toBe('done');
	});

	it('cancels and forgets a run when its step is removed', async () => {
		const t = setup();
		await t.engine.start('edit_region', body, 's1');
		await t.engine.remove('er_1');
		expect(t.ports.cancel).toHaveBeenCalledWith('er_1');
		expect(t.engine.tracking('er_1')).toBe(false);
		expect(get(t.views).er_1).toBeUndefined();
	});

	it('does not call cancel for a finished run when its step is removed', async () => {
		const t = setup();
		t.ports.start.mockResolvedValueOnce(run({ status: 'done' }));
		await t.engine.start('edit_region', body, 's1');
		await t.engine.remove('er_1');
		expect(t.ports.cancel).not.toHaveBeenCalled();
	});

	it('stops every loop, subscription and the socket on dispose', async () => {
		const t = setup(false);
		await t.engine.start('edit_region', body, 's1');
		t.engine.dispose();
		await vi.advanceTimersByTimeAsync(60000);
		expect(t.ports.get).not.toHaveBeenCalled();
		expect(t.socket.close).toHaveBeenCalled();
		expect(t.hasHandler()).toBe(false);
		expect(t.engine.count()).toBe(0);
	});

	it('speeds the loop up again when the socket drops', async () => {
		const t = setup(true);
		await t.engine.start('edit_region', body, 's1');
		t.state.connected = false;
		t.connection(false);
		await vi.advanceTimersByTimeAsync(1000);
		expect(t.ports.get).toHaveBeenCalledTimes(1);
	});
});
