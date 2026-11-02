import { writable, type Readable } from 'svelte/store';
import { logger, getErrorMessage } from '$lib/utils/logger';
import { toasts } from '$lib/stores/toast';
import {
	cancelMediaToolRun,
	getMediaToolRun,
	startMediaToolRun
} from '$lib/services/api/mediaTools';
import {
	isTerminalStatus,
	type MediaToolItem,
	type MediaToolSaveMode,
	type MediaToolRun,
	type MediaToolRunStatus,
	type MediaToolRunSummary,
	type StartMediaToolRunBody
} from './types';

export const POLL_FAST_MS = 400;
export const POLL_SLOW_MS = 1000;
export const POLL_SLOW_AFTER_MS = 10_000;
export const POLL_ERROR_CAP_MS = 5000;
export const POLL_ERROR_GIVE_UP_MS = 120_000;
export const CANCEL_FAILED_MESSAGE = "Couldn't cancel. It may already be finished.";

export interface MediaToolRunState {
	key: string;
	runId: string;
	toolId: string;
	label: string;
	progress: number;
	status: MediaToolRunStatus;
	message: string | null;
	outputMode: MediaToolSaveMode;
	cancelling: boolean;
}

export type MediaToolRunOutcome =
	| { status: 'completed'; items: MediaToolItem[]; replaced: boolean; summary?: MediaToolRunSummary }
	| { status: 'failed'; message: string; code: string | null }
	| { status: 'cancelled' };

export interface StartRunOptions {
	keys: string[];
	toolId: string;
	label: string;
	body: StartMediaToolRunBody;
}

export interface RunHandle {
	runId: string;
	done: Promise<MediaToolRunOutcome>;
}

export interface RunsApi {
	start: (body: StartMediaToolRunBody) => Promise<{ run_id: string }>;
	get: (runId: string, signal?: AbortSignal) => Promise<MediaToolRun>;
	cancel: (runId: string) => Promise<MediaToolRun | null | undefined>;
}

interface RunRecord {
	runId: string;
	toolId: string;
	label: string;
	keys: string[];
	outputMode: MediaToolSaveMode;
	startedAt: number;
	progress: number;
	status: MediaToolRunStatus;
	message: string | null;
	cancelling: boolean;
	errors: number;
	errorSince: number | null;
	timer: ReturnType<typeof setTimeout> | null;
	controller: AbortController | null;
	settle: (outcome: MediaToolRunOutcome) => void;
}

export interface MediaToolRunsStore extends Readable<Record<string, MediaToolRunState>> {
	start(options: StartRunOptions): Promise<RunHandle>;
	cancel(key: string): Promise<void>;
	cancelRun(runId: string): Promise<void>;
	destroy(): void;
}

const defaultApi: RunsApi = {
	start: startMediaToolRun,
	get: getMediaToolRun,
	cancel: cancelMediaToolRun
};

function httpStatus(error: unknown): number | null {
	const status = (error as { response?: { status?: unknown } } | null)?.response?.status;
	return typeof status === 'number' ? status : null;
}

function isDefinitiveRefusal(status: number | null): boolean {
	return status !== null && status >= 400 && status < 500 && status !== 408 && status !== 429;
}

function isAbort(error: unknown): boolean {
	const name = (error as { name?: string } | null)?.name;
	const code = (error as { code?: string } | null)?.code;
	return name === 'AbortError' || name === 'CanceledError' || code === 'ERR_CANCELED';
}

export function pollDelay(elapsedMs: number): number {
	return elapsedMs >= POLL_SLOW_AFTER_MS ? POLL_SLOW_MS : POLL_FAST_MS;
}

export function errorDelay(errors: number): number {
	return Math.min(POLL_ERROR_CAP_MS, POLL_FAST_MS * 2 ** Math.max(0, errors - 1));
}

export function createMediaToolRuns(api: RunsApi = defaultApi): MediaToolRunsStore {
	const state = writable<Record<string, MediaToolRunState>>({});
	const records = new Map<string, RunRecord>();
	let epoch = 0;

	function view(record: RunRecord, key: string): MediaToolRunState {
		return {
			key,
			runId: record.runId,
			toolId: record.toolId,
			label: record.label,
			progress: record.progress,
			status: record.status,
			message: record.message,
			outputMode: record.outputMode,
			cancelling: record.cancelling
		};
	}

	function publish(record: RunRecord) {
		state.update((current) => {
			const next = { ...current };
			for (const key of record.keys) next[key] = view(record, key);
			return next;
		});
	}

	function unpublish(record: RunRecord) {
		state.update((current) => {
			const next = { ...current };
			for (const key of record.keys) {
				if (next[key]?.runId === record.runId) delete next[key];
			}
			return next;
		});
	}

	function stop(record: RunRecord) {
		if (record.timer !== null) clearTimeout(record.timer);
		record.timer = null;
		record.controller?.abort();
		record.controller = null;
	}

	function finish(record: RunRecord, outcome: MediaToolRunOutcome) {
		if (!records.has(record.runId)) return;
		stop(record);
		records.delete(record.runId);
		unpublish(record);
		record.settle(outcome);
	}

	function schedule(record: RunRecord, delay: number) {
		if (!records.has(record.runId)) return;
		record.timer = setTimeout(() => void poll(record), delay);
	}

	function apply(record: RunRecord, run: MediaToolRun) {
		record.progress = Math.min(1, Math.max(0, Number(run.progress) || 0));
		record.status = run.status;
		record.message = run.label || null;
		if (run.status === 'completed') {
			finish(record, {
				status: 'completed',
				items: run.items ?? [],
				replaced: run.replaced === true,
				...(run.summary ? { summary: run.summary } : {})
			});
		} else if (run.status === 'failed') {
			finish(record, {
				status: 'failed',
				message: run.error?.message || 'The edit could not be applied',
				code: run.error?.code ?? null
			});
		} else if (run.status === 'cancelled') {
			finish(record, { status: 'cancelled' });
		} else {
			publish(record);
			schedule(record, pollDelay(Date.now() - record.startedAt));
		}
	}

	async function poll(record: RunRecord) {
		record.timer = null;
		if (!records.has(record.runId)) return;
		const controller = new AbortController();
		record.controller = controller;
		try {
			const run = await api.get(record.runId, controller.signal);
			if (!records.has(record.runId) || controller.signal.aborted) return;
			record.controller = null;
			record.errors = 0;
			record.errorSince = null;
			apply(record, run);
		} catch (error) {
			if (!records.has(record.runId) || controller.signal.aborted || isAbort(error)) return;
			record.controller = null;
			const status = httpStatus(error);
			if (status === 404) {
				finish(record, {
					status: 'failed',
					message: 'This edit was interrupted before it finished.',
					code: 'run_not_found'
				});
				return;
			}
			if (isDefinitiveRefusal(status)) {
				finish(record, {
					status: 'failed',
					message: 'The status of this edit could not be read.',
					code: 'poll_refused'
				});
				return;
			}
			record.errors += 1;
			record.errorSince ??= Date.now();
			if (Date.now() - record.errorSince >= POLL_ERROR_GIVE_UP_MS) {
				logger.error('Lost track of a media tool run:', getErrorMessage(error));
				finish(record, {
					status: 'failed',
					message: 'The connection was lost before the edit finished.',
					code: 'connection_lost'
				});
				return;
			}
			schedule(record, errorDelay(record.errors));
		}
	}

	async function start(options: StartRunOptions): Promise<RunHandle> {
		const startedEpoch = epoch;
		const { run_id: runId } = await api.start(options.body);
		let settle: (outcome: MediaToolRunOutcome) => void = () => undefined;
		const done = new Promise<MediaToolRunOutcome>((resolve) => {
			settle = resolve;
		});
		const record: RunRecord = {
			runId,
			toolId: options.toolId,
			label: options.label,
			keys: [...options.keys],
			outputMode: options.body.output.mode,
			startedAt: Date.now(),
			progress: 0,
			status: 'queued',
			message: null,
			cancelling: false,
			errors: 0,
			errorSince: null,
			timer: null,
			controller: null,
			settle
		};
		if (startedEpoch !== epoch) {
			settle({ status: 'cancelled' });
			api.cancel(runId).catch(() => undefined);
			return { runId, done };
		}
		records.set(runId, record);
		publish(record);
		schedule(record, 0);
		return { runId, done };
	}

	async function cancelRun(runId: string) {
		const record = records.get(runId);
		if (!record || record.cancelling) return;
		record.cancelling = true;
		publish(record);
		try {
			const snapshot = await api.cancel(runId);
			if (!records.has(runId)) return;
			if (snapshot && isTerminalStatus(snapshot.status)) apply(record, snapshot);
		} catch (error) {
			if (!records.has(runId)) return;
			logger.error('Could not cancel the media tool run:', getErrorMessage(error));
			toasts.warning(CANCEL_FAILED_MESSAGE);
			record.cancelling = false;
			publish(record);
			if (record.timer !== null) {
				clearTimeout(record.timer);
				record.timer = null;
				void poll(record);
			}
		}
	}

	async function cancel(key: string) {
		for (const record of records.values()) {
			if (record.keys.includes(key)) {
				await cancelRun(record.runId);
				return;
			}
		}
	}

	function destroy() {
		epoch += 1;
		for (const record of [...records.values()]) {
			stop(record);
			records.delete(record.runId);
			record.settle({ status: 'cancelled' });
		}
		state.set({});
	}

	return { subscribe: state.subscribe, start, cancel, cancelRun, destroy };
}

export const mediaToolRuns = createMediaToolRuns();
