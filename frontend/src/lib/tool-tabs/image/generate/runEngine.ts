import { get, writable, type Writable } from 'svelte/store';
import { describeEditorTaskFailure, type EditorRun, type RunStatus, type StartRunBody } from '$lib/services/api/editorTasks';

export interface RunMessage {
	type: string;
	generation_id?: string;
	run?: unknown;
	image?: unknown;
	progress?: unknown;
}

export interface RunSocket {
	subscribe(generationId: string, handler: (message: RunMessage) => void): () => void;
	connected(): boolean;
	onConnection(listener: (connected: boolean) => void): () => void;
	close(): void;
}

export interface RunPorts {
	start(task: string, body: StartRunBody): Promise<EditorRun>;
	get(runId: string): Promise<EditorRun>;
	cancel(runId: string): Promise<EditorRun>;
	socket(): RunSocket;
}

export interface RunView {
	id: string;
	stepId: string | null;
	status: RunStatus;
	progress: number;
	preview: string | null;
	run: EditorRun | null;
	error: string | null;
}

export type RunViews = Record<string, RunView>;

export interface RunEngineOptions {
	pollMs?: number;
	safetyMs?: number;
	views?: Writable<RunViews>;
	onSettled?(view: RunView): void;
}

export type CancelOutcome = { ok: true } | { ok: false; reason: 'refused' | 'failed' };

export const LOST_RUN_MESSAGE = 'This run could not be found any more.';

export const runViews: Writable<RunViews> = writable({});

interface Entry {
	id: string;
	generationId: string | null;
	unsubscribe: (() => void) | null;
	timer: ReturnType<typeof setTimeout> | null;
	busy: boolean;
}

const TERMINAL: ReadonlySet<RunStatus> = new Set(['done', 'failed', 'cancelled']);

export function isTerminal(status: RunStatus): boolean {
	return TERMINAL.has(status);
}

export function previewUrl(image: unknown): string | null {
	if (typeof image !== 'string' || !image) return null;
	if (image.startsWith('/api/') || image.startsWith('data:')) return image;
	if (image.startsWith('http')) return image.replace(/^https?:\/\/[^/]+/, '');
	return `data:image/png;base64,${image}`;
}

function fraction(value: unknown): number | null {
	if (typeof value !== 'number' || !Number.isFinite(value)) return null;
	return Math.max(0, Math.min(1, value > 1 ? value / 100 : value));
}

export function createRunEngine(ports: RunPorts, options: RunEngineOptions = {}) {
	const pollMs = options.pollMs ?? 2500;
	const safetyMs = options.safetyMs ?? 15000;
	const views = options.views ?? runViews;
	const entries = new Map<string, Entry>();
	const settled = new Set<string>();
	const attaching = new Set<string>();
	let socket: RunSocket | null = null;
	let unwatch: (() => void) | null = null;
	let disposed = false;

	function patch(id: string, change: Partial<RunView>): RunView {
		let next!: RunView;
		views.update((all) => {
			const base: RunView = all[id] ?? { id, stepId: null, status: 'queued', progress: 0, preview: null, run: null, error: null };
			next = { ...base, ...change };
			return { ...all, [id]: next };
		});
		return next;
	}

	function viewOf(id: string): RunView | null {
		return get(views)[id] ?? null;
	}

	function ensureSocket(): RunSocket {
		if (!socket) {
			socket = ports.socket();
			unwatch = socket.onConnection(() => {
				for (const entry of entries.values()) schedule(entry);
			});
		}
		return socket;
	}

	function interval(entry: Entry): number {
		return socket && entry.generationId && socket.connected() ? safetyMs : pollMs;
	}

	function schedule(entry: Entry): void {
		if (disposed || entries.get(entry.id) !== entry) return;
		if (entry.timer) clearTimeout(entry.timer);
		entry.timer = setTimeout(() => void tick(entry), interval(entry));
	}

	async function tick(entry: Entry): Promise<void> {
		entry.timer = null;
		if (disposed || entries.get(entry.id) !== entry || entry.busy) return;
		entry.busy = true;
		try {
			const run = await ports.get(entry.id);
			if (entries.get(entry.id) === entry) apply(run);
		} catch (error) {
			if (entries.get(entry.id) !== entry) return;
			if (describeEditorTaskFailure(error).kind === 'not_found') {
				finish(entry, { status: 'failed', error: LOST_RUN_MESSAGE });
				return;
			}
		} finally {
			entry.busy = false;
		}
		if (entries.get(entry.id) === entry) schedule(entry);
	}

	function stop(entry: Entry): void {
		if (entry.timer) clearTimeout(entry.timer);
		entry.timer = null;
		entry.unsubscribe?.();
		entry.unsubscribe = null;
		entries.delete(entry.id);
	}

	function finish(entry: Entry, change: Partial<RunView>): void {
		stop(entry);
		const view = patch(entry.id, { progress: change.status === 'done' ? 1 : (viewOf(entry.id)?.progress ?? 0), ...change });
		if (!settled.has(entry.id)) {
			settled.add(entry.id);
			options.onSettled?.(view);
		}
	}

	function listen(entry: Entry, generationId: string): void {
		if (entry.generationId === generationId) return;
		entry.unsubscribe?.();
		entry.generationId = generationId;
		entry.unsubscribe = ensureSocket().subscribe(generationId, (message) => onMessage(entry, message));
	}

	function onMessage(entry: Entry, message: RunMessage): void {
		if (entries.get(entry.id) !== entry) return;
		if (message.type === 'editor_run_update' && message.run && typeof message.run === 'object') {
			const run = message.run as EditorRun;
			if (run.id === entry.id) apply(run);
			return;
		}
		if (message.type === 'workbench_update') {
			const preview = previewUrl(message.image);
			if (preview) patch(entry.id, { preview });
			return;
		}
		if (message.type === 'generation_status') {
			const progress = fraction(message.progress);
			if (progress !== null) patch(entry.id, { progress });
		}
	}

	function apply(run: EditorRun): void {
		const entry = entries.get(run.id);
		if (!entry) return;
		patch(run.id, { status: run.status, progress: run.progress, run, error: run.error });
		if (isTerminal(run.status)) {
			finish(entry, { status: run.status, run, error: run.error, progress: run.status === 'done' ? 1 : run.progress });
			return;
		}
		if (run.generation_id && !disposed) listen(entry, run.generation_id);
		schedule(entry);
	}

	function adopt(run: EditorRun, stepId: string | null): void {
		if (disposed || !run.id) return;
		if (entries.has(run.id)) {
			patch(run.id, { stepId });
			return;
		}
		patch(run.id, { stepId, status: run.status, progress: run.progress, run, error: run.error, preview: null });
		if (isTerminal(run.status)) {
			if (!settled.has(run.id)) {
				settled.add(run.id);
				options.onSettled?.(viewOf(run.id)!);
			}
			return;
		}
		settled.delete(run.id);
		const entry: Entry = { id: run.id, generationId: null, unsubscribe: null, timer: null, busy: false };
		entries.set(run.id, entry);
		if (run.generation_id) listen(entry, run.generation_id);
		schedule(entry);
	}

	async function cancel(runId: string): Promise<CancelOutcome> {
		try {
			const run = await ports.cancel(runId);
			if (entries.has(runId)) apply(run);
			else patch(runId, { status: run.status, run });
			return { ok: true };
		} catch (error) {
			const failure = describeEditorTaskFailure(error);
			if (failure.kind === 'cancel_failed') {
				const entry = entries.get(runId);
				if (entry) schedule(entry);
				return { ok: false, reason: 'refused' };
			}
			return { ok: false, reason: 'failed' };
		}
	}

	function release(runId: string): void {
		const entry = entries.get(runId);
		if (entry) stop(entry);
		settled.delete(runId);
		views.update((all) => {
			const { [runId]: _gone, ...rest } = all;
			return rest;
		});
	}

	async function remove(runId: string): Promise<CancelOutcome> {
		const view = viewOf(runId);
		const live = !!view && !isTerminal(view.status);
		const outcome: CancelOutcome = live ? await cancel(runId) : { ok: true };
		release(runId);
		return outcome;
	}

	return {
		views,

		async start(task: string, body: StartRunBody, stepId: string | null): Promise<EditorRun> {
			const run = await ports.start(task, body);
			adopt(run, stepId);
			return run;
		},

		adopt,

		async attach(runId: string, stepId: string | null, known: RunStatus = 'running'): Promise<void> {
			if (disposed || entries.has(runId) || attaching.has(runId)) return;
			attaching.add(runId);
			const placeholder: EditorRun = { id: runId, task: '', status: known === 'queued' ? 'queued' : 'running', edit_id: null, step_id: stepId, generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [] };
			try {
				adopt(await ports.get(runId), stepId);
			} catch (error) {
				if (describeEditorTaskFailure(error).kind === 'not_found') {
					settled.add(runId);
					const view = patch(runId, { stepId, status: 'failed', error: LOST_RUN_MESSAGE });
					options.onSettled?.(view);
					return;
				}
				adopt(placeholder, stepId);
			} finally {
				attaching.delete(runId);
			}
		},

		cancel,
		release,
		remove,

		tracking(runId: string): boolean {
			return entries.has(runId);
		},

		count(): number {
			return entries.size;
		},

		dispose(): void {
			disposed = true;
			for (const entry of [...entries.values()]) stop(entry);
			unwatch?.();
			unwatch = null;
			socket?.close();
			socket = null;
		}
	};
}

export type RunEngine = ReturnType<typeof createRunEngine>;
