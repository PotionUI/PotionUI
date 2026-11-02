import { errorDelay, pollDelay, POLL_ERROR_GIVE_UP_MS } from '$lib/media-tools/runs';
import { isTerminalStatus, type MediaToolRun } from '$lib/media-tools/types';

export interface RenderRunApi {
	get: (runId: string, signal?: AbortSignal) => Promise<MediaToolRun>;
	list: () => Promise<MediaToolRun[]>;
}

export interface TrackOptions {
	api: RenderRunApi;
	signal: AbortSignal;
	onProgress: (run: MediaToolRun) => void;
	wait?: (ms: number, signal: AbortSignal) => Promise<void>;
	now?: () => number;
}

function httpStatus(error: unknown): number | null {
	const status = (error as { response?: { status?: unknown } } | null)?.response?.status;
	return typeof status === 'number' ? status : null;
}

export function abortableWait(ms: number, signal: AbortSignal): Promise<void> {
	return new Promise((resolve) => {
		if (signal.aborted) return resolve();
		const timer = setTimeout(done, ms);
		function done() {
			clearTimeout(timer);
			signal.removeEventListener('abort', done);
			resolve();
		}
		signal.addEventListener('abort', done);
	});
}

function lost(runId: string, code: string, message: string): MediaToolRun {
	return { run_id: runId, status: 'failed', progress: 0, label: '', items: [], replaced: false, error: { code, message } };
}

export async function trackRun(runId: string, options: TrackOptions): Promise<MediaToolRun | null> {
	const wait = options.wait ?? abortableWait;
	const now = options.now ?? Date.now;
	const started = now();
	let errors = 0;
	let errorSince: number | null = null;
	while (!options.signal.aborted) {
		try {
			const run = await options.api.get(runId, options.signal);
			if (options.signal.aborted) return null;
			errors = 0;
			errorSince = null;
			if (isTerminalStatus(run.status)) return run;
			options.onProgress(run);
			await wait(pollDelay(now() - started), options.signal);
		} catch (error) {
			if (options.signal.aborted) return null;
			const status = httpStatus(error);
			if (status === 404) return lost(runId, 'run_not_found', 'This render was interrupted before it finished.');
			if (status !== null && status >= 400 && status < 500 && status !== 408 && status !== 429) {
				return lost(runId, 'poll_refused', 'The status of this render could not be read.');
			}
			errors += 1;
			errorSince ??= now();
			if (now() - errorSince >= POLL_ERROR_GIVE_UP_MS) {
				return lost(runId, 'connection_lost', 'The connection was lost before the render finished.');
			}
			await wait(errorDelay(errors), options.signal);
		}
	}
	return null;
}

export async function findRun(api: RenderRunApi, runId: string): Promise<MediaToolRun | null> {
	const runs = await api.list();
	return runs.find((run) => run.run_id === runId) ?? null;
}

export function percentOf(run: Pick<MediaToolRun, 'progress'> | null | undefined): number {
	const value = Number(run?.progress);
	if (!Number.isFinite(value)) return 0;
	return Math.max(0, Math.min(100, value * 100));
}

export function secondsLeft(progress: number, elapsedMs: number): number | null {
	if (!(progress > 0.02) || progress >= 1 || !(elapsedMs > 0)) return null;
	return Math.max(0, ((elapsedMs / progress) * (1 - progress)) / 1000);
}
