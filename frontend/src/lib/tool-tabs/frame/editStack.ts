export interface EditStep<P = unknown> {
	id: string;
	tool: string;
	icon: string;
	label: string;
	summary: string;
	params: P;
	hidden: boolean;
	lane?: string;
}

export type StepDraft<P = unknown> = Omit<EditStep<P>, 'id' | 'hidden'>;

export interface PendingEdit<P = unknown> extends StepDraft<P> {
	target: string | null;
}

export interface StepRevision<P = unknown> {
	id: string;
	at: number;
	before: StepDraft<P>;
	after: StepDraft<P>;
}

export interface LiveSession<P = unknown> {
	id: string;
	before: StepDraft<P> | null;
}

export interface EditStackState<P = unknown> {
	steps: EditStep<P>[];
	cursor: number;
	pending: PendingEdit<P> | null;
	seq: number;
	revisions?: StepRevision<P>[];
	redoRevisions?: StepRevision<P>[];
	live?: LiveSession<P> | null;
}

export type LanePair = readonly [string, string];

export const DEFAULT_PINNED_LANES: readonly LanePair[] = [
	['paint', 'geometry'],
	['mask', 'geometry']
];

export type StackRowStatus = 'original' | 'applied' | 'current' | 'undone' | 'pending';

export interface StackRow<P = unknown> {
	key: string;
	index: number;
	status: StackRowStatus;
	step: EditStep<P> | null;
	pending: PendingEdit<P> | null;
	hidden: boolean;
	reopened: boolean;
}

export const STACK_VERSION = 1;

export function createStack<P = unknown>(): EditStackState<P> {
	return { steps: [], cursor: 0, pending: null, seq: 0 };
}

export function appliedSteps<P>(state: EditStackState<P>): EditStep<P>[] {
	return state.steps.slice(0, state.cursor);
}

export function undoneSteps<P>(state: EditStackState<P>): EditStep<P>[] {
	return state.steps.slice(state.cursor);
}

export function effectiveSteps<P>(state: EditStackState<P>): EditStep<P>[] {
	return appliedSteps(state).filter((step) => !step.hidden);
}

export function appliedCount(state: EditStackState<unknown>): number {
	return state.cursor;
}

export function undoneCount(state: EditStackState<unknown>): number {
	return state.steps.length - state.cursor;
}

export function beginEdit<P>(state: EditStackState<P>, draft: StepDraft<P>): EditStackState<P> {
	return { ...commitLive(state), pending: { ...draft, target: null } };
}

export function updatePending<P>(state: EditStackState<P>, patch: Partial<StepDraft<P>>): EditStackState<P> {
	if (!state.pending) return state;
	return { ...state, pending: { ...state.pending, ...patch } };
}

export function reopen<P>(input: EditStackState<P>, id: string): EditStackState<P> {
	const state = commitLive(input);
	const index = state.steps.findIndex((step) => step.id === id);
	if (index < 0 || index >= state.cursor) return input;
	const step = state.steps[index];
	const pending: PendingEdit<P> = {
		tool: step.tool,
		icon: step.icon,
		label: step.label,
		summary: step.summary,
		params: step.params,
		target: step.id
	};
	if (step.lane) pending.lane = step.lane;
	const steps = step.hidden ? state.steps.map((s) => (s.id === id ? { ...s, hidden: false } : s)) : state.steps;
	return { ...state, steps, pending };
}

export function reopenLatest<P>(state: EditStackState<P>, match: (step: EditStep<P>) => boolean): EditStackState<P> {
	const step = appliedSteps(state).findLast(match);
	return step ? reopen(state, step.id) : state;
}

export function discardPending<P>(state: EditStackState<P>): EditStackState<P> {
	if (!state.pending) return state;
	return { ...state, pending: null };
}

export function apply<P>(input: EditStackState<P>): EditStackState<P> {
	const state = commitLive(input);
	const pending = state.pending;
	if (!pending) return state;
	const { target, ...draft } = pending;
	if (target) {
		const before = state.steps.find((step) => step.id === target);
		if (before && JSON.stringify(draftOf(before)) === JSON.stringify(draft)) return { ...state, pending: null };
		const steps = state.steps.map((step) => (step.id === target ? { ...step, ...draft } : step));
		const revisions = before ? [...(state.revisions ?? []), { id: target, at: state.cursor, before: draftOf(before), after: draft }] : (state.revisions ?? []);
		return { ...state, steps, pending: null, revisions, redoRevisions: [] };
	}
	const seq = state.seq + 1;
	const step: EditStep<P> = { ...draft, id: `s${seq}`, hidden: false };
	const steps = [...appliedSteps(state), step];
	const next: EditStackState<P> = { steps, cursor: steps.length, pending: null, seq };
	if (state.revisions?.length) next.revisions = state.revisions;
	return next;
}

function withoutHistory<P>(state: EditStackState<P>): EditStackState<P> {
	const { revisions: _revisions, redoRevisions: _redoRevisions, live: _live, ...rest } = state;
	return rest;
}

export function commitLive<P>(state: EditStackState<P>): EditStackState<P> {
	const live = state.live;
	if (!live) return state;
	const { live: _live, ...rest } = state;
	const step = state.steps.find((entry) => entry.id === live.id);
	if (!step || !live.before) return rest;
	const after = draftOf(step);
	if (JSON.stringify(live.before) === JSON.stringify(after)) return rest;
	const revision: StepRevision<P> = { id: live.id, at: state.cursor, before: live.before, after };
	return { ...rest, revisions: [...(state.revisions ?? []), revision], redoRevisions: [] };
}

export function isLive(state: EditStackState<unknown>): boolean {
	return !!state.live;
}

export function liveEdit<P>(
	input: EditStackState<P>,
	draft: StepDraft<P>,
	match: (step: EditStep<P>) => boolean,
	options: { identity?: boolean } = {}
): EditStackState<P> {
	const found = appliedSteps(input).findLast(match);
	let state = input.live && input.live.id !== found?.id ? commitLive(input) : input;
	if (!found) {
		if (options.identity) return state;
		const seq = state.seq + 1;
		const step: EditStep<P> = { ...draft, id: `s${seq}`, hidden: false };
		const steps = [...appliedSteps(state), step];
		const next: EditStackState<P> = { steps, cursor: steps.length, pending: state.pending, seq, live: { id: step.id, before: null } };
		if (state.revisions?.length) next.revisions = state.revisions;
		return next;
	}
	const pending = state.pending?.target === found.id ? null : state.pending;
	const session = state.live ?? { id: found.id, before: draftOf(found) };
	if (options.identity && session.before === null) {
		const index = state.steps.findIndex((step) => step.id === found.id);
		const { live: _live, ...rest } = state;
		return { ...rest, steps: state.steps.filter((step) => step.id !== found.id), cursor: index < state.cursor ? state.cursor - 1 : state.cursor, pending };
	}
	if (!found.hidden && pending === state.pending && JSON.stringify(draftOf(found)) === JSON.stringify(draft)) return state;
	const steps = restyle(state, found.id, draft).map((step) => (step.id === found.id ? { ...step, hidden: false } : step));
	return { ...state, steps, pending, live: session };
}

function settled<P>(state: EditStackState<P>): EditStackState<P> {
	return commitLive(state);
}

function draftOf<P>(step: EditStep<P>): StepDraft<P> {
	const draft: StepDraft<P> = { tool: step.tool, icon: step.icon, label: step.label, summary: step.summary, params: step.params };
	if (step.lane) draft.lane = step.lane;
	return draft;
}

function restyle<P>(state: EditStackState<P>, id: string, draft: StepDraft<P>): EditStep<P>[] {
	return state.steps.map((step) => {
		if (step.id !== id) return step;
		const next = { ...step, ...draft };
		if (!draft.lane) delete next.lane;
		return next;
	});
}

function lastRevision<P>(list: StepRevision<P>[] | undefined): StepRevision<P> | undefined {
	return list && list.length ? list[list.length - 1] : undefined;
}

export function canUndo(state: EditStackState<unknown>): boolean {
	return state.pending !== null || state.cursor > 0;
}

function undoRevision<P>(state: EditStackState<P>): StepRevision<P> | undefined {
	const last = lastRevision(state.revisions);
	return last && last.at >= state.cursor ? last : undefined;
}

function redoRevision<P>(state: EditStackState<P>): StepRevision<P> | undefined {
	const last = lastRevision(state.redoRevisions);
	return last && last.at === state.cursor ? last : undefined;
}

export function canRedo(state: EditStackState<unknown>): boolean {
	return state.pending === null && (state.cursor < state.steps.length || !!redoRevision(state));
}

function untouched<P>(state: EditStackState<P>): boolean {
	const pending = state.pending;
	const step = pending?.target ? state.steps.find((entry) => entry.id === pending.target) : undefined;
	if (!pending || !step) return false;
	const { target: _target, ...draft } = pending;
	return JSON.stringify(draftOf(step)) === JSON.stringify(draft);
}

export function undo<P>(input: EditStackState<P>): EditStackState<P> {
	const state = settled(input);
	if (state.pending && !untouched(state)) return { ...state, pending: null };
	if (state.pending) return undo({ ...state, pending: null });
	const revision = undoRevision(state);
	if (revision) {
		return {
			...state,
			steps: restyle(state, revision.id, revision.before),
			revisions: (state.revisions ?? []).slice(0, -1),
			redoRevisions: [...(state.redoRevisions ?? []), revision]
		};
	}
	if (state.cursor === 0) return state;
	return { ...state, cursor: state.cursor - 1 };
}

export function redo<P>(input: EditStackState<P>): EditStackState<P> {
	const state = settled(input);
	if (!canRedo(state)) return input;
	const revision = redoRevision(state);
	if (revision) {
		return {
			...state,
			steps: restyle(state, revision.id, revision.after),
			revisions: [...(state.revisions ?? []), revision],
			redoRevisions: (state.redoRevisions ?? []).slice(0, -1)
		};
	}
	return { ...state, cursor: state.cursor + 1 };
}

export function undoLabel(input: EditStackState<unknown>): string | null {
	const state = settled(input);
	if (state.pending) return state.pending.label;
	const revision = undoRevision(state);
	if (revision) return revision.after.label;
	return state.cursor > 0 ? state.steps[state.cursor - 1].label : null;
}

export function redoLabel(state: EditStackState<unknown>): string | null {
	if (state.pending !== null) return null;
	const revision = redoRevision(state);
	if (revision) return revision.after.label;
	return canRedo(state) ? state.steps[state.cursor].label : null;
}

export function seek<P>(input: EditStackState<P>, cursor: number): EditStackState<P> {
	const state = settled(input);
	const next = Math.max(0, Math.min(state.steps.length, Math.trunc(cursor)));
	if (next === state.cursor && !state.pending) return state;
	return { ...withoutHistory(state), cursor: next, pending: null };
}

export function toggleHidden<P>(state: EditStackState<P>, id: string): EditStackState<P> {
	if (!state.steps.some((step) => step.id === id)) return state;
	const steps = state.steps.map((step) => (step.id === id ? { ...step, hidden: !step.hidden } : step));
	return { ...state, steps };
}

export function remove<P>(input: EditStackState<P>, id: string): EditStackState<P> {
	const state = settled(input);
	const index = state.steps.findIndex((step) => step.id === id);
	if (index < 0) return input;
	const steps = state.steps.filter((step) => step.id !== id);
	const cursor = index < state.cursor ? state.cursor - 1 : state.cursor;
	const pending = state.pending?.target === id ? null : state.pending;
	return { ...withoutHistory(state), steps, cursor, pending };
}

function laneOrder(steps: EditStep<unknown>[], pair: LanePair): string {
	return steps
		.map((step) => step.lane)
		.filter((lane): lane is string => lane === pair[0] || lane === pair[1])
		.join('|');
}

function moved<P>(steps: EditStep<P>[], from: number, to: number): EditStep<P>[] {
	const next = steps.slice();
	const [step] = next.splice(from, 1);
	next.splice(to, 0, step);
	return next;
}

export function canMove(
	state: EditStackState<unknown>,
	id: string,
	to: number,
	pinned: readonly LanePair[] = DEFAULT_PINNED_LANES
): boolean {
	const from = state.steps.findIndex((step) => step.id === id);
	if (from < 0 || from >= state.cursor) return false;
	if (!Number.isInteger(to) || to < 0 || to >= state.cursor || to === from) return false;
	if (state.pending?.target) return false;
	const applied = appliedSteps(state);
	const next = moved(applied, from, to);
	return pinned.every((pair) => laneOrder(applied, pair) === laneOrder(next, pair));
}

export function move<P>(
	input: EditStackState<P>,
	id: string,
	to: number,
	pinned: readonly LanePair[] = DEFAULT_PINNED_LANES
): EditStackState<P> {
	if (!canMove(input, id, to, pinned)) return input;
	const state = settled(input);
	const from = state.steps.findIndex((step) => step.id === id);
	const steps = [...moved(appliedSteps(state), from, to), ...undoneSteps(state)];
	return { ...withoutHistory(state), steps };
}

export function previewSteps<P>(state: EditStackState<P>): EditStep<P>[] {
	const pending = state.pending;
	const current = effectiveSteps(state);
	if (!pending) return current;
	const { target, ...draft } = pending;
	if (target) return current.map((step) => (step.id === target ? { ...step, ...draft } : step));
	return [...current, { ...draft, id: 'pending', hidden: false }];
}

export function previewWith<P>(state: EditStackState<P>, draft: StepDraft<P> | null): EditStep<P>[] {
	if (!draft) return previewSteps(state);
	const pending = state.pending;
	const current = effectiveSteps(state);
	if (pending?.target) return current.map((step) => (step.id === pending.target ? { ...step, ...draft } : step));
	return [...current, { ...draft, id: 'pending', hidden: false }];
}

export function beforeSteps<P>(state: EditStackState<P>): EditStep<P>[] {
	const pending = state.pending;
	if (!pending) return [];
	const current = effectiveSteps(state);
	return pending.target ? current.filter((step) => step.id !== pending.target) : current;
}

export function stackRows<P>(state: EditStackState<P>): StackRow<P>[] {
	const reopened = state.pending?.target ?? null;
	const rows: StackRow<P>[] = [
		{
			key: 'original',
			index: 0,
			status: state.cursor === 0 && !state.pending ? 'current' : 'original',
			step: null,
			pending: null,
			hidden: false,
			reopened: false
		}
	];
	const row = (step: EditStep<P>, index: number, status: StackRowStatus): StackRow<P> => ({
		key: step.id,
		index,
		status,
		step,
		pending: step.id === reopened ? state.pending : null,
		hidden: step.hidden,
		reopened: step.id === reopened
	});
	appliedSteps(state).forEach((step, i) => {
		const current = i === state.cursor - 1 && !state.pending;
		rows.push(row(step, i + 1, step.id === reopened ? 'pending' : current ? 'current' : 'applied'));
	});
	if (state.pending && !reopened) {
		rows.push({
			key: 'pending',
			index: state.cursor + 1,
			status: 'pending',
			step: null,
			pending: state.pending,
			hidden: false,
			reopened: false
		});
	}
	undoneSteps(state).forEach((step, i) => rows.push(row(step, state.cursor + i + 1, 'undone')));
	return rows;
}

export interface SerializedStack {
	v: number;
	steps: EditStep<unknown>[];
	cursor: number;
	pending: PendingEdit<unknown> | null;
	seq: number;
}

export function serialize(input: EditStackState<unknown>): SerializedStack {
	const state = settled(input);
	return JSON.parse(
		JSON.stringify({ v: STACK_VERSION, steps: state.steps, cursor: state.cursor, pending: state.pending, seq: state.seq })
	);
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readDraft(value: Record<string, unknown>): StepDraft<unknown> | null {
	const { tool, icon, label, summary, lane } = value;
	if (typeof tool !== 'string' || !tool || typeof label !== 'string') return null;
	const draft: StepDraft<unknown> = {
		tool,
		icon: typeof icon === 'string' ? icon : '',
		label,
		summary: typeof summary === 'string' ? summary : '',
		params: value.params ?? null
	};
	if (typeof lane === 'string' && lane) draft.lane = lane;
	return draft;
}

function seqOf(id: string): number {
	const match = /^s(\d+)$/.exec(id);
	return match ? Number(match[1]) : 0;
}

export function deserialize<P = unknown>(input: unknown): EditStackState<P> {
	const empty = createStack<P>();
	if (!isRecord(input) || input.v !== STACK_VERSION || !Array.isArray(input.steps)) return empty;
	const seen = new Set<string>();
	const steps: EditStep<unknown>[] = [];
	const rawCursor = typeof input.cursor === 'number' && Number.isFinite(input.cursor) ? Math.trunc(input.cursor) : 0;
	let cursor = Math.max(0, rawCursor);
	input.steps.forEach((raw, i) => {
		const draft = isRecord(raw) ? readDraft(raw) : null;
		const id = isRecord(raw) && typeof raw.id === 'string' ? raw.id : '';
		if (!draft || !id || seen.has(id)) {
			if (i < rawCursor) cursor -= 1;
			return;
		}
		seen.add(id);
		steps.push({ ...draft, id, hidden: (raw as Record<string, unknown>).hidden === true });
	});
	cursor = Math.max(0, Math.min(steps.length, cursor));
	let pending: PendingEdit<unknown> | null = null;
	if (isRecord(input.pending)) {
		const draft = readDraft(input.pending);
		const target = typeof input.pending.target === 'string' ? input.pending.target : null;
		const valid = target === null || steps.slice(0, cursor).some((step) => step.id === target);
		if (draft && valid) pending = { ...draft, target };
	}
	const maxSeq = steps.reduce((max, step) => Math.max(max, seqOf(step.id)), 0);
	const seq = Math.max(maxSeq, typeof input.seq === 'number' && Number.isFinite(input.seq) ? Math.trunc(input.seq) : 0);
	return { steps, cursor, pending, seq } as EditStackState<P>;
}
