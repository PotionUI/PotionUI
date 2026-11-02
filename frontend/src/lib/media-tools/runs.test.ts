import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { toasts } from '$lib/stores/toast';
import {
	CANCEL_FAILED_MESSAGE,
	POLL_ERROR_GIVE_UP_MS,
	POLL_FAST_MS,
	POLL_SLOW_AFTER_MS,
	POLL_SLOW_MS,
	createMediaToolRuns,
	errorDelay,
	pollDelay,
	type RunsApi
} from './runs';
import type { MediaToolRun, StartMediaToolRunBody } from './types';

const BODY: StartMediaToolRunBody = {
	steps: [{ tool: 'speed', params: { speed: 2 } }],
	inputs: [{ item_id: 'row-1' }],
	output: { mode: 'new' }
};

function run(overrides: Partial<MediaToolRun> = {}): MediaToolRun {
	return {
		run_id: 'run-1',
		status: 'running',
		progress: 0.25,
		label: 'Encoding',
		items: [],
		replaced: false,
		error: null,
		...overrides
	};
}

function fakeApi(): { api: RunsApi; start: ReturnType<typeof vi.fn>; get: ReturnType<typeof vi.fn>; cancel: ReturnType<typeof vi.fn> } {
	const start = vi.fn().mockResolvedValue({ run_id: 'run-1' });
	const getRun = vi.fn().mockResolvedValue(run());
	const cancel = vi.fn().mockResolvedValue(run({ status: 'cancelled' }));
	return { api: { start, get: getRun, cancel }, start, get: getRun, cancel };
}

const OPTIONS = { keys: ['row-1'], toolId: 'speed', label: 'Speed', body: BODY };

beforeEach(() => {
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
});

describe('delays', () => {
	it('polls fast first and slows down after ten seconds', () => {
		expect(pollDelay(0)).toBe(POLL_FAST_MS);
		expect(pollDelay(POLL_SLOW_AFTER_MS - 1)).toBe(POLL_FAST_MS);
		expect(pollDelay(POLL_SLOW_AFTER_MS)).toBe(POLL_SLOW_MS);
		expect(POLL_FAST_MS).toBe(400);
		expect(POLL_SLOW_MS).toBe(1000);
	});

	it('backs off after a failed poll, up to a cap', () => {
		expect([1, 2, 3, 4].map(errorDelay)).toEqual([400, 800, 1600, 3200]);
		expect(errorDelay(10)).toBe(5000);
	});
});

describe('starting a run', () => {
	it('posts the body and shows the run on every key it was started for', async () => {
		const { api, start } = fakeApi();
		const runs = createMediaToolRuns(api);

		await runs.start({ ...OPTIONS, keys: ['row-1', 'row-2'] });

		expect(start).toHaveBeenCalledWith(BODY);
		const state = get(runs);
		expect(Object.keys(state)).toEqual(['row-1', 'row-2']);
		expect(state['row-1']).toMatchObject({
			runId: 'run-1',
			toolId: 'speed',
			label: 'Speed',
			status: 'queued',
			progress: 0,
			outputMode: 'new',
			cancelling: false
		});
		runs.destroy();
	});

	it('registers nothing and lets the refusal through when the server says no', async () => {
		const { api, start } = fakeApi();
		const refusal = { response: { status: 400 } };
		start.mockRejectedValue(refusal);
		const runs = createMediaToolRuns(api);

		await expect(runs.start(OPTIONS)).rejects.toBe(refusal);

		expect(get(runs)).toEqual({});
		expect(vi.getTimerCount()).toBe(0);
	});
});

describe('polling', () => {
	it('asks for the first status straight away, not after a delay', async () => {
		const { api, get: getRun } = fakeApi();
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);

		await vi.advanceTimersByTimeAsync(0);

		expect(getRun).toHaveBeenCalledTimes(1);
		expect(getRun.mock.calls[0][0]).toBe('run-1');
		expect(get(runs)['row-1']).toMatchObject({ status: 'running', progress: 0.25, message: 'Encoding' });
		runs.destroy();
	});

	it('keeps asking every 400 ms while the run is going', async () => {
		const { api, get: getRun } = fakeApi();
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);

		await vi.advanceTimersByTimeAsync(399);
		expect(getRun).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(1);
		expect(getRun).toHaveBeenCalledTimes(2);
		await vi.advanceTimersByTimeAsync(400);
		expect(getRun).toHaveBeenCalledTimes(3);
		runs.destroy();
	});

	it('backs off to one second once the run is ten seconds old', async () => {
		const { api, get: getRun } = fakeApi();
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(POLL_SLOW_AFTER_MS);
		getRun.mockClear();

		await vi.advanceTimersByTimeAsync(999);
		expect(getRun).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(1);
		expect(getRun).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(1000);
		expect(getRun).toHaveBeenCalledTimes(2);
		runs.destroy();
	});

	it('follows the progress the server reports and keeps it between 0 and 1', async () => {
		const { api, get: getRun } = fakeApi();
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);
		getRun.mockResolvedValueOnce(run({ progress: 0.5 })).mockResolvedValueOnce(run({ progress: 7 }));

		await vi.advanceTimersByTimeAsync(0);
		expect(get(runs)['row-1'].progress).toBe(0.5);
		await vi.advanceTimersByTimeAsync(400);
		expect(get(runs)['row-1'].progress).toBe(1);
		runs.destroy();
	});
});

describe('finishing', () => {
	it('hands back the items, drops the run and stops polling when it completes', async () => {
		const { api, get: getRun } = fakeApi();
		const item = { id: 'row-2', filename: 'a-speed.mp4', media_type: 'video', url: '/a-speed.mp4' };
		getRun.mockResolvedValue(run({ status: 'completed', progress: 1, items: [item], replaced: false }));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);

		await vi.advanceTimersByTimeAsync(0);

		await expect(handle.done).resolves.toEqual({ status: 'completed', items: [item], replaced: false });
		expect(get(runs)).toEqual({});
		expect(vi.getTimerCount()).toBe(0);
		getRun.mockClear();
		await vi.advanceTimersByTimeAsync(5000);
		expect(getRun).not.toHaveBeenCalled();
	});

	it('hands back the run summary when the run saved no items', async () => {
		const { api, get: getRun } = fakeApi();
		const summary = { message: 'Saved hero.', links: [] };
		getRun.mockResolvedValue(run({ status: 'completed', progress: 1, items: [], replaced: false, summary }));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);

		await vi.advanceTimersByTimeAsync(0);

		await expect(handle.done).resolves.toEqual({ status: 'completed', items: [], replaced: false, summary });
		runs.destroy();
	});

	it('reports whether the original was replaced', async () => {
		const { api, get: getRun } = fakeApi();
		getRun.mockResolvedValue(run({ status: 'completed', items: [], replaced: true }));
		const handle = await createMediaToolRuns(api).start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);
		await expect(handle.done).resolves.toMatchObject({ replaced: true });
	});

	it('reports the server error when it fails', async () => {
		const { api, get: getRun } = fakeApi();
		getRun.mockResolvedValue(
			run({ status: 'failed', error: { code: 'ffmpeg_failed', message: 'The encoder stopped' } })
		);
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);

		await expect(handle.done).resolves.toEqual({
			status: 'failed',
			message: 'The encoder stopped',
			code: 'ffmpeg_failed'
		});
		expect(get(runs)).toEqual({});
	});

	it('words a failure that arrives without an error body', async () => {
		const { api, get: getRun } = fakeApi();
		getRun.mockResolvedValue(run({ status: 'failed', error: null }));
		const handle = await createMediaToolRuns(api).start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);
		await expect(handle.done).resolves.toMatchObject({ status: 'failed', message: 'The edit could not be applied' });
	});

	it('treats a run the server cancelled as cancelled', async () => {
		const { api, get: getRun } = fakeApi();
		getRun.mockResolvedValue(run({ status: 'cancelled' }));
		const handle = await createMediaToolRuns(api).start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);
		await expect(handle.done).resolves.toEqual({ status: 'cancelled' });
	});

	it('clears the run from every key it covered', async () => {
		const { api, get: getRun } = fakeApi();
		getRun.mockResolvedValue(run({ status: 'completed' }));
		const runs = createMediaToolRuns(api);
		await runs.start({ ...OPTIONS, keys: ['a', 'b', 'c'] });
		await vi.advanceTimersByTimeAsync(0);
		expect(get(runs)).toEqual({});
	});
});

describe('cancelling', () => {
	it('deletes the run, settles as cancelled and stops polling', async () => {
		const { api, get: getRun, cancel } = fakeApi();
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);

		await runs.cancel('row-1');

		expect(cancel).toHaveBeenCalledWith('run-1');
		await expect(handle.done).resolves.toEqual({ status: 'cancelled' });
		expect(get(runs)).toEqual({});
		expect(vi.getTimerCount()).toBe(0);
		getRun.mockClear();
		await vi.advanceTimersByTimeAsync(5000);
		expect(getRun).not.toHaveBeenCalled();
	});

	it('cancels a run through any key it covered', async () => {
		const { api, cancel } = fakeApi();
		const runs = createMediaToolRuns(api);
		await runs.start({ ...OPTIONS, keys: ['a', 'b'] });
		await runs.cancel('b');
		expect(cancel).toHaveBeenCalledTimes(1);
		expect(get(runs)).toEqual({});
	});

	it('marks the run as cancelling while the request is in flight', async () => {
		const { api, cancel } = fakeApi();
		let release: () => void = () => undefined;
		cancel.mockReturnValue(new Promise<void>((resolve) => (release = resolve)));
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);

		const pending = runs.cancel('row-1');
		expect(get(runs)['row-1'].cancelling).toBe(true);
		release();
		await pending;
		runs.destroy();
	});

	it('hands the result over when the server answers the cancel with a run that completed', async () => {
		const { api, get: getRun, cancel } = fakeApi();
		const item = { id: 'r2', filename: 'a-speed.mp4', media_type: 'video', url: '/a-speed.mp4' };
		cancel.mockResolvedValue(run({ status: 'completed', progress: 1, items: [item], replaced: true }));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);

		await runs.cancel('row-1');

		await expect(handle.done).resolves.toEqual({ status: 'completed', items: [item], replaced: true });
		expect(get(runs)).toEqual({});
		getRun.mockClear();
		await vi.advanceTimersByTimeAsync(5000);
		expect(getRun).not.toHaveBeenCalled();
	});

	it('reports the failure when the server answers the cancel with a run that failed', async () => {
		const { api, cancel } = fakeApi();
		cancel.mockResolvedValue(run({ status: 'failed', error: { code: 'publish_failed', message: 'Storage is full' } }));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);

		await runs.cancel('row-1');

		await expect(handle.done).resolves.toEqual({ status: 'failed', message: 'Storage is full', code: 'publish_failed' });
	});

	it('settles as cancelled only when the answer says the run was cancelled', async () => {
		const { api, cancel } = fakeApi();
		cancel.mockResolvedValue(run({ status: 'cancelled' }));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);

		await runs.cancel('row-1');

		await expect(handle.done).resolves.toEqual({ status: 'cancelled' });
	});

	it('keeps the run cancelling and lets the poll deliver the end when the answer is not final', async () => {
		const { api, get: getRun, cancel } = fakeApi();
		cancel.mockResolvedValue(run({ status: 'running', progress: 0.9 }));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);
		let settled = false;
		void handle.done.then(() => (settled = true));

		await runs.cancel('row-1');
		await vi.advanceTimersByTimeAsync(0);

		expect(settled).toBe(false);
		expect(get(runs)['row-1']).toMatchObject({ cancelling: true });
		expect(vi.getTimerCount()).toBe(1);
		getRun.mockResolvedValue(run({ status: 'cancelled' }));
		await vi.advanceTimersByTimeAsync(400);
		await expect(handle.done).resolves.toEqual({ status: 'cancelled' });
		expect(get(runs)).toEqual({});
	});

	it('delivers a completion that arrives by poll after a cancel that came too late', async () => {
		const { api, get: getRun, cancel } = fakeApi();
		const item = { id: 'r2', filename: 'a.mp4', media_type: 'video', url: '/a.mp4' };
		cancel.mockResolvedValue(run({ status: 'running', progress: 0.95, label: 'Saving' }));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);

		await runs.cancel('row-1');
		getRun.mockResolvedValue(run({ status: 'completed', progress: 1, items: [item], replaced: false }));
		await vi.advanceTimersByTimeAsync(400);

		await expect(handle.done).resolves.toEqual({ status: 'completed', items: [item], replaced: false });
	});

	it('treats an empty 200 as not final, never as cancelled', async () => {
		const { api, cancel } = fakeApi();
		cancel.mockResolvedValue(null);
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		let settled = false;
		void handle.done.then(() => (settled = true));

		await runs.cancel('row-1');
		await vi.advanceTimersByTimeAsync(0);

		expect(settled).toBe(false);
		expect(get(runs)['row-1']).toMatchObject({ cancelling: true });
		runs.destroy();
	});

	it('does not abort a poll in flight that may carry the completion when the answer is not final', async () => {
		const { api, get: getRun, cancel } = fakeApi();
		const signals: AbortSignal[] = [];
		let resolvePoll: (value: MediaToolRun) => void = () => undefined;
		getRun.mockImplementation((_id: string, signal?: AbortSignal) => {
			if (signal) signals.push(signal);
			return new Promise<MediaToolRun>((resolve) => (resolvePoll = resolve));
		});
		cancel.mockResolvedValue(run({ status: 'running' }));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);

		await runs.cancel('row-1');

		expect(signals[0].aborted).toBe(false);
		const item = { id: 'r2', filename: 'a.mp4', media_type: 'video', url: '/a.mp4' };
		resolvePoll(run({ status: 'completed', progress: 1, items: [item], replaced: true }));
		await expect(handle.done).resolves.toEqual({ status: 'completed', items: [item], replaced: true });
	});

	it('ignores a second press while the first cancel is being settled', async () => {
		const { api, cancel } = fakeApi();
		cancel.mockResolvedValue(run({ status: 'running' }));
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);

		await runs.cancel('row-1');
		await runs.cancel('row-1');

		expect(cancel).toHaveBeenCalledTimes(1);
		runs.destroy();
	});

	it('asks the server once however many times it is pressed', async () => {
		const { api, cancel } = fakeApi();
		cancel.mockReturnValue(new Promise<void>(() => undefined));
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);
		void runs.cancel('row-1');
		void runs.cancel('row-1');
		expect(cancel).toHaveBeenCalledTimes(1);
		runs.destroy();
	});

	it('keeps following the run when the server refuses to cancel it', async () => {
		const { api, get: getRun, cancel } = fakeApi();
		cancel.mockRejectedValue(new Error('too late'));
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);

		await runs.cancel('row-1');

		expect(get(runs)['row-1'].cancelling).toBe(false);
		getRun.mockClear();
		await vi.advanceTimersByTimeAsync(400);
		expect(getRun).toHaveBeenCalledTimes(1);
		runs.destroy();
	});

	it('says so when the server refuses, and checks the real status at once instead of waiting for the next tick', async () => {
		for (const entry of get(toasts)) toasts.remove(entry.id);
		const { api, get: getRun, cancel } = fakeApi();
		cancel.mockRejectedValue(new Error('too late'));
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);
		getRun.mockClear();

		await runs.cancel('row-1');

		expect(get(toasts).map((entry) => [entry.type, entry.message])).toEqual([['warning', CANCEL_FAILED_MESSAGE]]);
		expect(CANCEL_FAILED_MESSAGE).toBe("Couldn't cancel. It may already be finished.");
		await vi.advanceTimersByTimeAsync(0);
		expect(getRun).toHaveBeenCalledTimes(1);
		runs.destroy();
	});

	it('lets the band show the run as finished when the refused cancel was too late', async () => {
		const { api, get: getRun, cancel } = fakeApi();
		cancel.mockRejectedValue(new Error('already finished'));
		const item = { id: 'r2', filename: 'a.mp4', media_type: 'video', url: '/a.mp4' };
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);
		getRun.mockResolvedValue(run({ status: 'completed', progress: 1, items: [item] }));

		await runs.cancel('row-1');
		await vi.advanceTimersByTimeAsync(0);

		await expect(handle.done).resolves.toMatchObject({ status: 'completed', items: [item] });
		expect(get(runs)).toEqual({});
	});

	it('does not poll twice at once after a refused cancel', async () => {
		const { api, get: getRun, cancel } = fakeApi();
		cancel.mockRejectedValue(new Error('no'));
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);
		getRun.mockClear();

		await runs.cancel('row-1');
		await vi.advanceTimersByTimeAsync(0);

		expect(getRun).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(399);
		expect(getRun).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(1);
		expect(getRun).toHaveBeenCalledTimes(2);
		runs.destroy();
	});

	it('stays quiet when the run finished while the cancel request was in flight', async () => {
		for (const entry of get(toasts)) toasts.remove(entry.id);
		const { api, get: getRun, cancel } = fakeApi();
		let refuse: (reason: unknown) => void = () => undefined;
		cancel.mockReturnValue(new Promise((_resolve, reject) => (refuse = reject)));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);

		const pending = runs.cancel('row-1');
		getRun.mockResolvedValue(run({ status: 'completed', progress: 1, items: [] }));
		await vi.advanceTimersByTimeAsync(400);
		await expect(handle.done).resolves.toMatchObject({ status: 'completed' });
		refuse(new Error('too late'));
		await pending;

		expect(get(toasts)).toEqual([]);
	});

	it('hands back the items exactly as the server sent them, thumbnails included', async () => {
		const { api, get: getRun } = fakeApi();
		const item = {
			id: 'r2',
			filename: 'a.png',
			media_type: 'image',
			url: '/a.png',
			thumbnail_small: '/t/s.webp',
			thumbnail_medium: '/t/m.webp',
			thumbnail_large: '/t/l.webp'
		};
		getRun.mockResolvedValue(run({ status: 'completed', progress: 1, items: [item], replaced: true }));
		const handle = await createMediaToolRuns(api).start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);
		await expect(handle.done).resolves.toEqual({ status: 'completed', items: [item], replaced: true });
	});

	it('ignores a key nothing is running for', async () => {
		const { api, cancel } = fakeApi();
		await createMediaToolRuns(api).cancel('nobody');
		expect(cancel).not.toHaveBeenCalled();
	});
});

describe('trouble reaching the server', () => {
	it('retries with a growing delay and recovers', async () => {
		const { api, get: getRun } = fakeApi();
		getRun
			.mockRejectedValueOnce(new Error('offline'))
			.mockRejectedValueOnce(new Error('offline'))
			.mockResolvedValue(run({ status: 'completed' }));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);

		await vi.advanceTimersByTimeAsync(0);
		expect(getRun).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(399);
		expect(getRun).toHaveBeenCalledTimes(1);
		await vi.advanceTimersByTimeAsync(1);
		expect(getRun).toHaveBeenCalledTimes(2);
		await vi.advanceTimersByTimeAsync(799);
		expect(getRun).toHaveBeenCalledTimes(2);
		await vi.advanceTimersByTimeAsync(1);
		expect(getRun).toHaveBeenCalledTimes(3);

		await expect(handle.done).resolves.toMatchObject({ status: 'completed' });
	});

	it('keeps polling network errors well past the first few seconds, at the five second cap', async () => {
		const { api, get: getRun } = fakeApi();
		getRun.mockRejectedValue(new Error('offline'));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		let settled = false;
		void handle.done.then(() => (settled = true));

		await vi.advanceTimersByTimeAsync(30_000);

		expect(settled).toBe(false);
		expect(getRun.mock.calls.length).toBeGreaterThan(8);
		const before = getRun.mock.calls.length;
		await vi.advanceTimersByTimeAsync(5000);
		expect(getRun.mock.calls.length - before).toBe(1);
		runs.destroy();
	});

	it('gives up on network errors only after two minutes', async () => {
		const { api, get: getRun } = fakeApi();
		getRun.mockRejectedValue(new Error('offline'));
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		let settled = false;
		void handle.done.then(() => (settled = true));

		await vi.advanceTimersByTimeAsync(POLL_ERROR_GIVE_UP_MS - 6000);
		expect(settled).toBe(false);
		await vi.advanceTimersByTimeAsync(12_000);

		await expect(handle.done).resolves.toMatchObject({ status: 'failed', code: 'connection_lost' });
		expect(get(runs)).toEqual({});
		expect(vi.getTimerCount()).toBe(0);
		expect(POLL_ERROR_GIVE_UP_MS).toBe(120_000);
	});

	it('keeps polling through server errors and requests that were asked to slow down', async () => {
		for (const status of [500, 502, 503, 408, 429]) {
			const { api, get: getRun } = fakeApi();
			getRun.mockRejectedValueOnce({ response: { status } }).mockRejectedValueOnce({ response: { status } });
			getRun.mockResolvedValue(run({ status: 'completed' }));
			const handle = await createMediaToolRuns(api).start(OPTIONS);

			await vi.advanceTimersByTimeAsync(2000);

			await expect(handle.done).resolves.toMatchObject({ status: 'completed' });
		}
	});

	it('fails at once on an answer that will never change', async () => {
		for (const status of [400, 401, 403, 410, 422]) {
			const { api, get: getRun } = fakeApi();
			getRun.mockRejectedValue({ response: { status } });
			const handle = await createMediaToolRuns(api).start(OPTIONS);
			await vi.advanceTimersByTimeAsync(0);
			await expect(handle.done).resolves.toMatchObject({ status: 'failed', code: 'poll_refused' });
			expect(getRun).toHaveBeenCalledTimes(1);
		}
	});

	it('fails at once when the server no longer knows the run', async () => {
		const { api, get: getRun } = fakeApi();
		getRun.mockRejectedValue({ response: { status: 404 } });
		const handle = await createMediaToolRuns(api).start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);
		await expect(handle.done).resolves.toMatchObject({ status: 'failed', code: 'run_not_found' });
		expect(getRun).toHaveBeenCalledTimes(1);
	});

	it('starts the two minutes again after a good poll', async () => {
		const { api, get: getRun } = fakeApi();
		let offline = true;
		getRun.mockImplementation(async () => {
			if (offline) throw new Error('offline');
			return run();
		});
		const runs = createMediaToolRuns(api);
		const handle = await runs.start(OPTIONS);
		let settled = false;
		void handle.done.then(() => (settled = true));

		await vi.advanceTimersByTimeAsync(100_000);
		offline = false;
		await vi.advanceTimersByTimeAsync(6000);
		offline = true;
		await vi.advanceTimersByTimeAsync(100_000);

		expect(settled).toBe(false);
		offline = false;
		getRun.mockResolvedValue(run({ status: 'completed' }));
		await vi.advanceTimersByTimeAsync(6000);
		await expect(handle.done).resolves.toMatchObject({ status: 'completed' });
	});
});

describe('destroy', () => {
	it('stops every timer and aborts the request in flight', async () => {
		const { api, get: getRun } = fakeApi();
		let signal: AbortSignal | undefined;
		getRun.mockImplementation((_id: string, passed?: AbortSignal) => {
			signal = passed;
			return new Promise(() => undefined);
		});
		const runs = createMediaToolRuns(api);
		const first = await runs.start(OPTIONS);
		await vi.advanceTimersByTimeAsync(0);
		expect(signal?.aborted).toBe(false);

		runs.destroy();

		expect(signal?.aborted).toBe(true);
		expect(vi.getTimerCount()).toBe(0);
		expect(get(runs)).toEqual({});
		await expect(first.done).resolves.toEqual({ status: 'cancelled' });
	});

	it('leaves no timer behind between polls either', async () => {
		const { api, start } = fakeApi();
		start.mockResolvedValueOnce({ run_id: 'run-a' }).mockResolvedValueOnce({ run_id: 'run-b' });
		const runs = createMediaToolRuns(api);
		await runs.start(OPTIONS);
		await runs.start({ ...OPTIONS, keys: ['row-9'] });
		await vi.advanceTimersByTimeAsync(0);
		expect(vi.getTimerCount()).toBeGreaterThan(0);

		runs.destroy();

		expect(vi.getTimerCount()).toBe(0);
	});

	it('cancels a run whose request was still being sent when it was destroyed', async () => {
		const { api, start, cancel } = fakeApi();
		let release: (value: { run_id: string }) => void = () => undefined;
		start.mockReturnValue(new Promise((resolve) => (release = resolve)));
		const runs = createMediaToolRuns(api);
		const pending = runs.start(OPTIONS);

		runs.destroy();
		release({ run_id: 'run-late' });
		const handle = await pending;

		await expect(handle.done).resolves.toEqual({ status: 'cancelled' });
		expect(cancel).toHaveBeenCalledWith('run-late');
		expect(get(runs)).toEqual({});
		expect(vi.getTimerCount()).toBe(0);
	});

	it('can be used again afterwards', async () => {
		const { api } = fakeApi();
		const runs = createMediaToolRuns(api);
		runs.destroy();
		await runs.start(OPTIONS);
		expect(get(runs)['row-1']).toBeDefined();
		runs.destroy();
	});
});
