import { describe, expect, it } from 'vitest';
import {
	apply,
	beginEdit,
	canRedo,
	canUndo,
	commitLive,
	createStack,
	deserialize,
	effectiveSteps,
	isLive,
	liveEdit,
	move,
	redo,
	redoLabel,
	remove,
	reopen,
	seek,
	serialize,
	toggleHidden,
	undo,
	undoLabel,
	type EditStackState,
	type StepDraft
} from './editStack';

type Params = Record<string, number | string>;

const crop: StepDraft<Params> = { tool: 'crop', icon: 'crop', label: 'Crop', summary: '4:3', params: { x: 0.1 }, lane: 'geometry' };
const resize: StepDraft<Params> = { tool: 'resize', icon: 'shrink', label: 'Resize', summary: '1536', params: { w: 1536 }, lane: 'geometry' };

const tone = (contrast: number): StepDraft<Params> => ({ tool: 'effects', icon: 'sliders', label: 'Tone', summary: `Contrast ${contrast}`, params: { op: 'tone', contrast } });
const exposure = (stops: number): StepDraft<Params> => ({ tool: 'effects', icon: 'sun', label: 'Exposure', summary: `Stops ${stops}`, params: { op: 'exposure', stops } });
const isOp = (op: string) => (step: { params: Params }) => step.params.op === op;

function live(state: EditStackState<Params>, draft: StepDraft<Params>, op: string, identity = false): EditStackState<Params> {
	return liveEdit(state, draft, isOp(op), { identity });
}

function applyAll(...drafts: StepDraft<Params>[]): EditStackState<Params> {
	return drafts.reduce((state, draft) => apply(beginEdit(state, draft)), createStack<Params>());
}

function labels(state: EditStackState<Params>): string[] {
	return state.steps.map((step) => step.label);
}

describe('live edits', () => {
	it('creates one step for the first move of an op and updates it in place afterwards', () => {
		let state = createStack<Params>();
		for (const value of [5, 10, 15, 20]) state = live(state, tone(value), 'tone');
		expect(state.steps).toHaveLength(1);
		expect(state.cursor).toBe(1);
		expect(state.steps[0].params).toEqual({ op: 'tone', contrast: 20 });
		expect(state.steps[0].summary).toBe('Contrast 20');
		expect(state.pending).toBeNull();
	});

	it('keeps one step per op', () => {
		let state = live(createStack<Params>(), tone(10), 'tone');
		state = live(state, exposure(0.5), 'exposure');
		state = live(state, tone(30), 'tone');
		state = live(state, exposure(0.9), 'exposure');
		expect(state.steps.map((step) => step.params)).toEqual([
			{ op: 'tone', contrast: 30 },
			{ op: 'exposure', stops: 0.9 }
		]);
	});

	it('undoes a whole drag of a new step as one step', () => {
		let state = live(createStack<Params>(), tone(5), 'tone');
		for (const value of [10, 15, 20]) state = live(state, tone(value), 'tone');
		const undone = undo(state);
		expect(undone.cursor).toBe(0);
		expect(effectiveSteps(undone)).toEqual([]);
		expect(redo(undone).steps[0].params).toEqual({ op: 'tone', contrast: 20 });
	});

	it('undoes a drag over an existing step back to the committed value', () => {
		let state = commitLive(live(createStack<Params>(), tone(10), 'tone'));
		for (const value of [20, 30, 40]) state = live(state, tone(value), 'tone');
		expect(isLive(state)).toBe(true);
		expect(undoLabel(state)).toBe('Tone');
		const undone = undo(state);
		expect(isLive(undone)).toBe(false);
		expect(undone.steps[0].params).toEqual({ op: 'tone', contrast: 10 });
		expect(undone.cursor).toBe(1);
		expect(redo(undone).steps[0].params).toEqual({ op: 'tone', contrast: 40 });
	});

	it('records each committed drag as its own undo step', () => {
		let state = commitLive(live(createStack<Params>(), tone(10), 'tone'));
		state = commitLive(live(live(state, tone(20), 'tone'), tone(25), 'tone'));
		state = commitLive(live(live(state, tone(40), 'tone'), tone(45), 'tone'));
		expect(state.revisions).toHaveLength(2);
		const once = undo(state);
		expect(once.steps[0].params).toEqual({ op: 'tone', contrast: 25 });
		const twice = undo(once);
		expect(twice.steps[0].params).toEqual({ op: 'tone', contrast: 10 });
		expect(undo(twice).cursor).toBe(0);
	});

	it('commits nothing when a drag ends on the starting value', () => {
		let state = commitLive(live(createStack<Params>(), tone(10), 'tone'));
		state = commitLive(live(live(state, tone(30), 'tone'), tone(10), 'tone'));
		expect(state.revisions ?? []).toHaveLength(0);
		expect(undo(state).cursor).toBe(0);
	});

	it('ignores a repeated identical value', () => {
		const first = live(createStack<Params>(), tone(10), 'tone');
		expect(live(first, tone(10), 'tone')).toBe(first);
	});

	it('invalidates redo when a live edit creates a step after an undo', () => {
		const undone = undo(applyAll(crop, resize));
		expect(canRedo(undone)).toBe(true);
		const next = live(undone, tone(10), 'tone');
		expect(canRedo(next)).toBe(false);
		expect(labels(next)).toEqual(['Crop', 'Tone']);
	});

	it('invalidates a redoable revision once a new drag commits', () => {
		let state = commitLive(live(applyAll(crop), tone(10), 'tone'));
		state = commitLive(live(state, tone(30), 'tone'));
		const undone = undo(state);
		expect(canRedo(undone)).toBe(true);
		const next = commitLive(live(undone, tone(50), 'tone'));
		expect(canRedo(next)).toBe(false);
		expect(next.steps[1].params).toEqual({ op: 'tone', contrast: 50 });
	});

	it('leaves later steps redoable when an earlier step is edited in place', () => {
		const state = undo(apply(beginEdit(commitLive(live(createStack<Params>(), tone(10), 'tone')), crop)));
		expect(state.cursor).toBe(1);
		const edited = commitLive(live(state, tone(50), 'tone'));
		expect(edited.steps).toHaveLength(2);
		expect(edited.cursor).toBe(1);
		expect(canRedo(edited)).toBe(true);
	});

	it('only matches steps below the cursor', () => {
		const state = undo(commitLive(live(createStack<Params>(), tone(10), 'tone')));
		const next = live(state, tone(20), 'tone');
		expect(next.steps).toHaveLength(1);
		expect(next.steps[0].params).toEqual({ op: 'tone', contrast: 20 });
		expect(next.cursor).toBe(1);
	});

	it('switching to another op closes the previous drag', () => {
		let state = commitLive(live(createStack<Params>(), tone(10), 'tone'));
		state = live(live(state, tone(30), 'tone'), exposure(0.5), 'exposure');
		expect(state.revisions).toHaveLength(1);
		expect(state.live?.id).toBe(state.steps[1].id);
		const once = undo(state);
		expect(once.steps).toHaveLength(2);
		expect(once.steps[0].params).toEqual({ op: 'tone', contrast: 30 });
		expect(undo(once).cursor).toBe(1);
	});

	it('does not create a step for an identity value and drops a step created in the same drag', () => {
		const empty = createStack<Params>();
		expect(live(empty, tone(0), 'tone', true)).toBe(empty);
		const back = live(live(empty, tone(20), 'tone'), tone(0), 'tone', true);
		expect(back.steps).toEqual([]);
		expect(back.cursor).toBe(0);
		expect(isLive(back)).toBe(false);
	});

	it('keeps an existing step when a later drag returns to the identity value', () => {
		let state = commitLive(live(createStack<Params>(), tone(20), 'tone'));
		state = commitLive(live(state, tone(0), 'tone', true));
		expect(state.steps).toHaveLength(1);
		expect(state.steps[0].params).toEqual({ op: 'tone', contrast: 0 });
		expect(undo(state).steps[0].params).toEqual({ op: 'tone', contrast: 20 });
	});

	it('unhides a hidden step it edits', () => {
		const base = toggleHidden(commitLive(live(createStack<Params>(), tone(10), 'tone')), 's1');
		expect(base.steps[0].hidden).toBe(true);
		expect(live(base, tone(20), 'tone').steps[0].hidden).toBe(false);
	});

	it('leaves a pending geometry step untouched', () => {
		const next = live(beginEdit(createStack<Params>(), crop), tone(10), 'tone');
		expect(next.pending).toEqual({ ...crop, target: null });
		expect(next.steps).toHaveLength(1);
		const applied = apply(next);
		expect(labels(applied)).toEqual(['Tone', 'Crop']);
		expect(applied.cursor).toBe(2);
	});

	it('applying a pending step settles an open drag first', () => {
		const state = live(commitLive(live(createStack<Params>(), tone(10), 'tone')), tone(30), 'tone');
		const applied = apply(beginEdit(state, crop));
		expect(isLive(applied)).toBe(false);
		expect(undo(applied).cursor).toBe(1);
		expect(undo(undo(applied)).steps[0].params).toEqual({ op: 'tone', contrast: 10 });
	});

	it('supersedes a pending reopen of the same step', () => {
		const reopened = reopen(commitLive(live(createStack<Params>(), tone(10), 'tone')), 's1');
		expect(reopened.pending?.target).toBe('s1');
		const next = live(reopened, tone(30), 'tone');
		expect(next.pending).toBeNull();
		expect(next.steps[0].params).toEqual({ op: 'tone', contrast: 30 });
	});

	it('keeps a pending reopen of a different step', () => {
		const base = reopen(commitLive(live(applyAll(crop), tone(10), 'tone')), 's1');
		expect(live(base, tone(30), 'tone').pending?.target).toBe('s1');
	});

	it('serializes a live edit as settled state', () => {
		const state = live(live(createStack<Params>(), tone(10), 'tone'), tone(20), 'tone');
		const saved = serialize(state);
		expect(saved.steps[0].params).toEqual({ op: 'tone', contrast: 20 });
		expect(JSON.stringify(saved)).not.toContain('live');
		expect(deserialize<Params>(saved).steps).toHaveLength(1);
	});

	it('seeking and removing settle the drag', () => {
		const state = live(commitLive(live(createStack<Params>(), tone(10), 'tone')), tone(30), 'tone');
		expect(isLive(seek(state, 0))).toBe(false);
		const removed = remove(state, 's1');
		expect(removed.steps).toEqual([]);
		expect(isLive(removed)).toBe(false);
	});

	it('moving a step settles the drag and keeps order rules', () => {
		const state = live(live(applyAll(crop), tone(10), 'tone'), tone(20), 'tone');
		const moved = move(state, 's2', 0);
		expect(isLive(moved)).toBe(false);
		expect(labels(moved)).toEqual(['Tone', 'Crop']);
	});

	it('reports undo and redo labels through an open drag', () => {
		const state = live(commitLive(live(createStack<Params>(), tone(10), 'tone')), tone(30), 'tone');
		expect(canUndo(state)).toBe(true);
		expect(undoLabel(state)).toBe('Tone');
		expect(redoLabel(undo(state))).toBe('Tone');
	});
});
