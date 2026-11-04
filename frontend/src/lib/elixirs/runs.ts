import type { NodeLike, Invocation, OutputEntry, Run, RunCheckpoint, RunEdge } from './types';

export const ACTIVE_RUN_STATUSES = ['pending', 'running', 'awaiting_selection', 'needs_attention', 'cancelling'];

const RUN_LABELS: Record<string, string> = {
	pending: 'Starting',
	running: 'Brewing',
	awaiting_selection: 'Waiting for your choice',
	needs_attention: 'Needs attention',
	cancelling: 'Stopping',
	completed: 'Done',
	partial: 'Partly done',
	cancelled: 'Stopped',
	failed: 'Failed',
	interrupted: 'Interrupted'
};

type MaybeRun = Run | null | undefined;

export interface ErrorGroup {
	message: string;
	count: number;
}

export interface NodeRunView {
	status: string;
	label: string;
	writer: boolean;
	startedAt: string | null;
	finishedAt: string | null;
	tone: string;
	progress: number | null;
	done: number;
	total: number;
	errors: ErrorGroup[];
	failedCount: number;
	pending: number;
	outputs: OutputEntry[];
	seeds: number[];
}

export interface StatusLabelOptions {
	done?: number;
	total?: number;
	failed?: number;
	outputs?: number;
	source?: string | null;
	writer?: boolean;
	noun?: string;
}

export interface NodeRunViewOptions {
	invocations?: Invocation[];
	source?: string | null;
}

export function isActive(run: MaybeRun): boolean {
	return !!run && ACTIVE_RUN_STATUSES.includes(run.status);
}

export function isPolling(run: MaybeRun): boolean {
	return !!run && ['pending', 'running', 'cancelling'].includes(run.status);
}

export function runLabel(run: MaybeRun): string {
	return run ? RUN_LABELS[run.status] ?? run.status : '';
}

export function runTone(run: MaybeRun): string {
	if (!run) return 'neutral';
	if (['needs_attention', 'failed', 'interrupted'].includes(run.status)) return 'danger';
	if (run.status === 'awaiting_selection') return 'signal';
	if (run.status === 'completed') return 'success';
	return 'neutral';
}

function latest(invocations: Invocation[]): Invocation[] {
	const bySlot = new Map<string, Invocation>();
	for (const item of invocations) {
		if (item.status === 'superseded') continue;
		const slot = `${item.edge_id || ''}|${item.input_key}`;
		const current = bySlot.get(slot);
		if (!current || item.attempt > current.attempt) bySlot.set(slot, item);
	}
	return [...bySlot.values()].sort((a, b) => a.ordinal - b.ordinal);
}

export function outgoingCheckpoints(run: MaybeRun, nodeId: string): Array<{ edge: RunEdge; checkpoint: RunCheckpoint }> {
	if (!run) return [];
	const edges = run.graph_edges ?? [];
	return edges
		.filter((edge) => edge.source === nodeId && run.checkpoints?.[edge.id])
		.map((edge) => ({ edge, checkpoint: (run.checkpoints as Record<string, RunCheckpoint>)[edge.id] }));
}

function groupErrors(failed: Invocation[]): ErrorGroup[] {
	const counts = new Map<string, number>();
	for (const item of failed) {
		if (item.error) counts.set(item.error, (counts.get(item.error) ?? 0) + 1);
	}
	return [...counts].map(([message, count]) => ({ message, count }));
}

export function nodeRunView(run: MaybeRun, node: NodeLike, { invocations = run?.invocations ?? [], source = null }: NodeRunViewOptions = {}): NodeRunView | null {
	const writer = node.kind === 'llm';
	if (!run) return null;
	const nodeId = node.id as string;
	const state = run.node_states?.[nodeId];
	if (!state) return null;
	const items = latest(invocations.filter((item) => item.node_id === nodeId));
	const running = items.filter((item) => ['submitted', 'running', 'claimed'].includes(item.status));
	const progressValues = running.map((item) => item.progress).filter((value): value is number => typeof value === 'number');
	const done = items.filter((item) => item.status === 'completed').length;
	const failed = items.filter((item) => ['failed', 'interrupted'].includes(item.status));
	const manifestOutputs = Object.values(state.manifest || {}).flat();
	const outputs = manifestOutputs.length > 0 ? manifestOutputs : items.flatMap((item) => item.outputs || []);
	const waiting = outgoingCheckpoints(run, nodeId).some(
		({ checkpoint }) => checkpoint.confirmed === null && (checkpoint.eligible || []).length > 0
	);
	let status = state.status;
	if (status === 'done' && waiting && isActive(run)) status = 'choose';
	const progress = progressValues.length ? Math.round((progressValues.reduce((a, b) => a + b, 0) / progressValues.length) * 100) : null;
	const started = items.map((item) => item.submitted_at).filter((value): value is string => !!value).sort();
	const finished = items.map((item) => item.finished_at).filter((value): value is string => !!value).sort();
	return {
		status,
		label: nodeStatusLabel(status, { done, total: items.length, failed: failed.length, outputs: outputs.length, source, writer, noun: outputNoun(outputs) }),
		writer,
		startedAt: started[0] ?? null,
		finishedAt: running.length === 0 && finished.length > 0 ? finished[finished.length - 1] : null,
		tone: status === 'failed' || status === 'no_output' ? 'danger' : status === 'choose' ? 'signal' : status === 'done' ? 'success' : 'neutral',
		progress,
		done,
		total: items.length,
		errors: groupErrors(failed),
		failedCount: failed.length,
		pending: items.filter((item) => ['pending', 'claimed', 'submitted', 'running'].includes(item.status)).length,
		outputs,
		seeds: items.map((item) => item.effective_seed).filter((seed): seed is number => seed !== null && seed !== undefined)
	};
}

function outputNoun(outputs: OutputEntry[]): string {
	const types = new Set(outputs.map((entry) => entry?.media_type));
	if (types.size !== 1) return 'file';
	const [type] = types;
	return type === 'image' || type === 'video' ? type : 'file';
}

export function nodeStatusLabel(status: string, { done = 0, total = 0, failed = 0, outputs = 0, source = null, writer = false, noun = 'file' }: StatusLabelOptions = {}): string {
	switch (status) {
		case 'waiting':
			return source ? `Waiting for ${source}` : 'Waiting for its input';
		case 'queued':
			return total > 1 ? `Queued · ${total} ${writer ? 'prompts' : 'generations'}` : 'Queued';
		case 'running':
			return total > 1 ? `${writer ? 'Writing' : 'Generating'} · ${done} of ${total}` : writer ? 'Writing' : 'Generating';
		case 'done':
			return writer ? `Wrote ${outputs} ${outputs === 1 ? 'prompt' : 'prompts'}` : `Done · ${outputs} ${outputs === 1 ? noun : `${noun}s`}`;
		case 'choose':
			return 'Waiting for your choice';
		case 'failed':
			return total > 1 ? `Failed · ${done} of ${total} made` : 'Failed';
		case 'no_output':
			return 'No usable output';
		case 'cancelled':
			return 'Stopped';
		default:
			return status;
	}
}

export function formatElapsed(milliseconds: unknown): string {
	if (typeof milliseconds !== 'number' || !Number.isFinite(milliseconds) || milliseconds < 0) return '';
	const seconds = Math.floor(milliseconds / 1000);
	const minutes = Math.floor(seconds / 60);
	const rest = String(seconds % 60).padStart(2, '0');
	if (minutes < 60) return `${String(minutes).padStart(2, '0')}:${rest}`;
	return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}:${rest}`;
}

export function elapsedOf(view: Pick<NodeRunView, 'startedAt' | 'finishedAt'> | null | undefined, now: number): string {
	if (!view?.startedAt) return '';
	const start = Date.parse(view.startedAt);
	const end = view.finishedAt ? Date.parse(view.finishedAt) : now;
	if (Number.isNaN(start) || Number.isNaN(end)) return '';
	return formatElapsed(end - start);
}

export function toggleKey(keys: string[], key: string): string[] {
	return keys.includes(key) ? keys.filter((item) => item !== key) : [...keys, key];
}

export function canonical<T>(keys: Iterable<string>, eligible: T[], keyOf: (item: T) => string): string[] {
	const wanted = new Set(keys);
	return eligible.map(keyOf).filter((key) => wanted.has(key));
}

export function newIdempotencyKey(prefix: string): string {
	const random = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
	return `${prefix}-${random}`;
}

export function laneStatus(run: MaybeRun, edgeId: string, estimate = ''): { label: string; tone: string } {
	const checkpoint = run?.checkpoints?.[edgeId];
	if (checkpoint?.confirmed) return { label: `${checkpoint.confirmed.length} sent`, tone: 'success' };
	if (checkpoint) {
		const ready = (checkpoint.eligible || []).length;
		return { label: `${ready} ready`, tone: isActive(run) ? 'signal' : 'neutral' };
	}
	return { label: estimate, tone: 'neutral' };
}

const STEP_WORDS: Record<string, string> = {
	waiting: 'Waiting',
	queued: 'Queued',
	running: 'Generating',
	done: 'Done',
	choose: 'Your choice',
	failed: 'Failed',
	no_output: 'No output',
	cancelled: 'Stopped'
};

const STEP_TONES: Record<string, string> = {
	running: 'signal',
	choose: 'signal',
	done: 'success',
	failed: 'danger',
	no_output: 'danger',
	cancelled: 'warning'
};

export function stepState(view: (Pick<NodeRunView, 'status'> & { writer?: boolean }) | null | undefined): { word: string; tone: string } {
	if (!view) return { word: 'Not brewed', tone: 'neutral' };
	const word = view.writer && view.status === 'running' ? 'Writing' : STEP_WORDS[view.status] ?? view.status;
	return { word, tone: STEP_TONES[view.status] ?? 'neutral' };
}

export function brewNumber(runs: Array<Pick<Run, 'id'>> | null | undefined, runId: string | null | undefined): number | null {
	const index = (runs ?? []).findIndex((item) => item.id === runId);
	return index === -1 ? null : (runs as unknown[]).length - index;
}

export function brewName(runs: Array<Pick<Run, 'id' | 'scope'>> | null | undefined, runId: string | null | undefined): string {
	const number = brewNumber(runs, runId);
	if (number === null) return '';
	const item = (runs as Array<Pick<Run, 'id' | 'scope'>>).find((entry) => entry.id === runId);
	return item?.scope === 'node' ? `Node run ${number}` : `Brew ${number}`;
}

export function runElapsed(run: Pick<Run, 'created_at' | 'finished_at'> | null | undefined, now: number): string {
	if (!run?.created_at) return '';
	const start = Date.parse(run.created_at);
	const end = run.finished_at ? Date.parse(run.finished_at) : now;
	if (Number.isNaN(start) || Number.isNaN(end)) return '';
	return formatElapsed(end - start);
}

export function isCurrentRun(run: Pick<Run, 'id'> | null | undefined, runId: string | null | undefined): boolean {
	return !!run && !!runId && run.id === runId;
}
