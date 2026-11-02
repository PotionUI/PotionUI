import { describe, expect, it } from 'vitest';
import {
	apply,
	appliedCount,
	appliedSteps,
	beforeSteps,
	beginEdit,
	canMove,
	canRedo,
	canUndo,
	createStack,
	deserialize,
	discardPending,
	effectiveSteps,
	move,
	previewSteps,
	redo,
	redoLabel,
	remove,
	reopen,
	seek,
	serialize,
	stackRows,
	toggleHidden,
	undo,
	undoLabel,
	undoneCount,
	updatePending,
	type EditStackState,
	type StepDraft
} from './editStack';

type Params = Record<string, number | string>;

const crop: StepDraft<Params> = { tool: 'crop', icon: 'crop', label: 'Crop', summary: '4:3 · 896×672', params: { x: 0.1 }, lane: 'geometry' };
const resize: StepDraft<Params> = { tool: 'resize', icon: 'shrink', label: 'Resize', summary: '1536×1152', params: { w: 1536 }, lane: 'geometry' };
const look: StepDraft<Params> = { tool: 'effects', icon: 'sliders', label: 'Reel', summary: 'Intensity 60', params: { look: 'reel' }, lane: 'look' };
const paint: StepDraft<Params> = { tool: 'paint', icon: 'brush', label: 'Paint', summary: '2 layers', params: { layers: 2 }, lane: 'paint' };

function applyAll(...drafts: StepDraft<Params>[]): EditStackState<Params> {
	return drafts.reduce((state, draft) => apply(beginEdit(state, draft)), createStack<Params>());
}

function labels(state: EditStackState<Params>): string[] {
	return state.steps.map((step) => step.label);
}

describe('pending edits and apply', () => {
	it('starts empty with nothing to undo or redo', () => {
		const state = createStack();
		expect(state).toEqual({ steps: [], cursor: 0, pending: null, seq: 0 });
		expect(canUndo(state)).toBe(false);
		expect(canRedo(state)).toBe(false);
		expect(undoLabel(state)).toBeNull();
	});

	it('holds a new edit as pending until Apply appends it at the cursor', () => {
		const pending = beginEdit(createStack<Params>(), crop);
		expect(pending.steps).toEqual([]);
		expect(pending.pending).toMatchObject({ label: 'Crop', target: null });
		const applied = apply(pending);
		expect(applied.pending).toBeNull();
		expect(applied.cursor).toBe(1);
		expect(applied.steps[0]).toMatchObject({ id: 's1', tool: 'crop', hidden: false, lane: 'geometry', params: { x: 0.1 } });
	});

	it('mints sequential ids that never repeat after removal', () => {
		let state = applyAll(crop, resize);
		state = remove(state, 's2');
		state = apply(beginEdit(state, look));
		expect(state.steps.map((step) => step.id)).toEqual(['s1', 's3']);
	});

	it('updates the pending draft and ignores updates with nothing pending', () => {
		const state = updatePending(beginEdit(createStack<Params>(), look), { summary: 'Intensity 80', params: { look: 'reel', i: 80 } });
		expect(state.pending).toMatchObject({ summary: 'Intensity 80', params: { i: 80 } });
		const idle = createStack<Params>();
		expect(updatePending(idle, { summary: 'x' })).toBe(idle);
	});

	it('apply with nothing pending is a no-op', () => {
		const state = applyAll(crop);
		expect(apply(state)).toBe(state);
	});

	it('discards a pending edit without touching the steps', () => {
		const base = applyAll(crop);
		const state = discardPending(beginEdit(base, resize));
		expect(state.pending).toBeNull();
		expect(state.steps).toEqual(base.steps);
		expect(discardPending(base)).toBe(base);
	});
});

describe('cursor undo and redo', () => {
	it('moves the cursor back and forward over steps', () => {
		const state = applyAll(crop, resize, look);
		const back = undo(state);
		expect(back.cursor).toBe(2);
		expect(appliedCount(back)).toBe(2);
		expect(undoneCount(back)).toBe(1);
		expect(labels(back)).toEqual(['Crop', 'Resize', 'Reel']);
		expect(redo(back).cursor).toBe(3);
	});

	it('stops at both ends', () => {
		const state = applyAll(crop);
		const start = undo(state);
		expect(undo(start)).toBe(start);
		expect(redo(state)).toBe(state);
		expect(canRedo(state)).toBe(false);
		expect(canUndo(start)).toBe(false);
	});

	it('undo first drops a pending edit and keeps the cursor', () => {
		const state = undo(beginEdit(applyAll(crop), resize));
		expect(state.pending).toBeNull();
		expect(state.cursor).toBe(1);
		expect(canUndo(beginEdit(createStack<Params>(), crop))).toBe(true);
	});

	it('redo is unavailable while an edit is pending', () => {
		const state = beginEdit(undo(applyAll(crop, resize)), look);
		expect(canRedo(state)).toBe(false);
		expect(redo(state)).toBe(state);
	});

	it('names the step undo and redo would move over', () => {
		const state = undo(applyAll(crop, resize, look));
		expect(undoLabel(state)).toBe('Resize');
		expect(redoLabel(state)).toBe('Reel');
		expect(undoLabel(beginEdit(state, paint))).toBe('Paint');
		expect(redoLabel(applyAll(crop))).toBeNull();
	});

	it('keeps undone steps until a new edit truncates the redo tail', () => {
		const undone = undo(undo(applyAll(crop, resize, look)));
		expect(labels(undone)).toEqual(['Crop', 'Resize', 'Reel']);
		const replaced = apply(beginEdit(undone, paint));
		expect(labels(replaced)).toEqual(['Crop', 'Paint']);
		expect(replaced.cursor).toBe(2);
		expect(canRedo(replaced)).toBe(false);
	});

	it('seeks the cursor within bounds and drops a pending edit', () => {
		const state = applyAll(crop, resize, look);
		expect(seek(state, 1).cursor).toBe(1);
		expect(seek(state, -4).cursor).toBe(0);
		expect(seek(state, 99).cursor).toBe(3);
		expect(seek(beginEdit(state, paint), 3).pending).toBeNull();
		expect(seek(state, 3)).toBe(state);
	});
});

describe('reopening a step', () => {
	it('loads an applied step as a pending edit targeting it', () => {
		const state = reopen(applyAll(crop, resize), 's1');
		expect(state.pending).toEqual({ ...crop, target: 's1' });
	});

	it('updates the step in place on Apply and keeps order, cursor and redo tail', () => {
		const base = undo(applyAll(crop, resize, look));
		const reopened = updatePending(reopen(base, 's1'), { summary: '1:1', params: { x: 0.2 } });
		const state = apply(reopened);
		expect(labels(state)).toEqual(['Crop', 'Resize', 'Reel']);
		expect(state.steps[0]).toMatchObject({ id: 's1', summary: '1:1', params: { x: 0.2 } });
		expect(state.cursor).toBe(2);
		expect(state.seq).toBe(base.seq);
	});

	it('unhides a hidden step it reopens so the preview shows the draft', () => {
		const reopened = updatePending(reopen(toggleHidden(applyAll(crop, resize), 's1'), 's1'), { summary: '1:1' });
		expect(reopened.steps[0].hidden).toBe(false);
		expect(previewSteps(reopened).map((step) => step.summary)).toEqual(['1:1', '1536×1152']);
		expect(apply(reopened).steps[0]).toMatchObject({ hidden: false, summary: '1:1' });
	});

	it('keeps the steps untouched when reopening a visible step', () => {
		const state = applyAll(crop);
		expect(reopen(state, 's1').steps).toBe(state.steps);
	});

	it('refuses undone or unknown steps', () => {
		const state = undo(applyAll(crop, resize));
		expect(reopen(state, 's2')).toBe(state);
		expect(reopen(state, 'nope')).toBe(state);
	});
});

describe('hide and remove', () => {
	it('hides a step from the effective list and shows it again', () => {
		const hidden = toggleHidden(applyAll(crop, resize), 's1');
		expect(hidden.steps[0].hidden).toBe(true);
		expect(effectiveSteps(hidden).map((step) => step.label)).toEqual(['Resize']);
		expect(appliedSteps(hidden)).toHaveLength(2);
		expect(toggleHidden(hidden, 's1').steps[0].hidden).toBe(false);
		expect(toggleHidden(hidden, 'nope')).toBe(hidden);
	});

	it('removes an applied step and pulls the cursor back', () => {
		const state = remove(applyAll(crop, resize, look), 's2');
		expect(labels(state)).toEqual(['Crop', 'Reel']);
		expect(state.cursor).toBe(2);
	});

	it('removes an undone step without moving the cursor', () => {
		const state = remove(undo(applyAll(crop, resize, look)), 's3');
		expect(labels(state)).toEqual(['Crop', 'Resize']);
		expect(state.cursor).toBe(2);
	});

	it('drops a pending edit that targets the removed step only', () => {
		const reopened = reopen(applyAll(crop, resize), 's1');
		expect(remove(reopened, 's1').pending).toBeNull();
		expect(remove(reopened, 's2').pending?.target).toBe('s1');
		expect(remove(reopened, 'nope')).toBe(reopened);
	});
});

describe('reordering', () => {
	it('moves an applied step to a new index', () => {
		const state = move(applyAll(crop, look, resize), 's2', 0);
		expect(labels(state)).toEqual(['Reel', 'Crop', 'Resize']);
		expect(state.cursor).toBe(3);
	});

	it('lets geometry steps swap with each other', () => {
		const state = applyAll(crop, resize);
		expect(canMove(state, 's2', 0)).toBe(true);
		expect(labels(move(state, 's2', 0))).toEqual(['Resize', 'Crop']);
	});

	it('keeps paint pinned relative to geometry', () => {
		const state = applyAll(crop, paint, look);
		expect(canMove(state, 's2', 0)).toBe(false);
		expect(move(state, 's2', 0)).toBe(state);
		expect(canMove(state, 's1', 1)).toBe(false);
		expect(canMove(state, 's3', 1)).toBe(true);
		expect(labels(move(state, 's3', 0))).toEqual(['Reel', 'Crop', 'Paint']);
	});

	it('accepts custom pinned lanes', () => {
		const state = applyAll(crop, paint);
		expect(canMove(state, 's2', 0, [])).toBe(true);
		expect(canMove(applyAll(crop, look), 's2', 0, [['look', 'geometry']])).toBe(false);
	});

	it('never moves into or out of the undone tail', () => {
		const state = undo(applyAll(crop, look, resize));
		expect(canMove(state, 's1', 2)).toBe(false);
		expect(canMove(state, 's3', 0)).toBe(false);
		const moved = move(state, 's2', 0);
		expect(labels(moved)).toEqual(['Reel', 'Crop', 'Resize']);
		expect(moved.cursor).toBe(2);
	});

	it('rejects unknown ids, same index, fractional and out-of-range targets', () => {
		const state = applyAll(crop, look);
		expect(canMove(state, 'nope', 0)).toBe(false);
		expect(canMove(state, 's1', 0)).toBe(false);
		expect(canMove(state, 's1', 0.5)).toBe(false);
		expect(canMove(state, 's1', -1)).toBe(false);
	});

	it('locks reordering while a step is reopened', () => {
		const state = reopen(applyAll(crop, look), 's1');
		expect(canMove(state, 's2', 0)).toBe(false);
	});
});

describe('before and after', () => {
	it('shows the original as before and the current stack as after with nothing pending', () => {
		const state = applyAll(crop, resize);
		expect(beforeSteps(state)).toEqual([]);
		expect(previewSteps(state).map((step) => step.label)).toEqual(['Crop', 'Resize']);
	});

	it('compares a new pending edit against the current stack', () => {
		const state = beginEdit(toggleHidden(applyAll(crop, resize), 's2'), look);
		expect(beforeSteps(state).map((step) => step.label)).toEqual(['Crop']);
		expect(previewSteps(state).map((step) => step.label)).toEqual(['Crop', 'Reel']);
	});

	it('compares a reopened step against the stack without it', () => {
		const state = updatePending(reopen(applyAll(crop, resize, look), 's2'), { summary: '768×576' });
		expect(beforeSteps(state).map((step) => step.label)).toEqual(['Crop', 'Reel']);
		const after = previewSteps(state);
		expect(after.map((step) => step.summary)).toEqual(['4:3 · 896×672', '768×576', 'Intensity 60']);
	});
});

describe('rows', () => {
	it('pins the original as row 00 and marks it current with no edits', () => {
		const [row] = stackRows(createStack());
		expect(row).toMatchObject({ key: 'original', index: 0, status: 'current', step: null });
	});

	it('marks the step at the cursor current, the tail undone and hidden steps', () => {
		const rows = stackRows(toggleHidden(undo(applyAll(crop, resize, look)), 's1'));
		expect(rows.map((row) => [row.index, row.status, row.hidden])).toEqual([
			[0, 'original', false],
			[1, 'applied', true],
			[2, 'current', false],
			[3, 'undone', false]
		]);
	});

	it('shows a new pending edit between the applied steps and the undone tail', () => {
		const rows = stackRows(beginEdit(undo(applyAll(crop, resize)), look));
		expect(rows.map((row) => [row.key, row.index, row.status])).toEqual([
			['original', 0, 'original'],
			['s1', 1, 'applied'],
			['pending', 2, 'pending'],
			['s2', 2, 'undone']
		]);
		expect(rows[2].pending?.label).toBe('Reel');
	});

	it('shows a reopened step as pending in place', () => {
		const rows = stackRows(reopen(applyAll(crop, resize), 's1'));
		expect(rows.map((row) => [row.key, row.status, row.reopened])).toEqual([
			['original', 'original', false],
			['s1', 'pending', true],
			['s2', 'applied', false]
		]);
		expect(rows[1].pending?.target).toBe('s1');
	});
});

describe('persistence', () => {
	it('round-trips a stack with an undone tail, hidden step and pending edit', () => {
		const state = beginEdit(toggleHidden(undo(applyAll(crop, resize, look)), 's1'), paint);
		const restored = deserialize<Params>(JSON.parse(JSON.stringify(serialize(state))));
		expect(restored).toEqual(state);
		expect(serialize(state).v).toBe(1);
	});

	it('round-trips a reopened step', () => {
		const state = reopen(applyAll(crop, resize), 's2');
		expect(deserialize(serialize(state))).toEqual(state);
	});

	it('serializes to plain JSON detached from the live state', () => {
		const state = applyAll(crop);
		const data = serialize(state);
		(data.steps[0].params as Params).x = 9;
		expect(state.steps[0].params.x).toBe(0.1);
	});

	it('returns an empty stack for garbage or an unknown version', () => {
		for (const input of [null, 7, 'x', [], {}, { v: 2, steps: [] }, { v: 1, steps: 'no' }]) {
			expect(deserialize(input)).toEqual(createStack());
		}
	});

	it('drops malformed and duplicate steps and keeps the cursor on the same applied steps', () => {
		const restored = deserialize({
			v: 1,
			cursor: 3,
			seq: 0,
			pending: null,
			steps: [
				{ id: 's1', tool: 'crop', icon: 'crop', label: 'Crop', summary: '', params: {} },
				{ id: 's1', tool: 'crop', icon: 'crop', label: 'Again', summary: '', params: {} },
				{ id: 's4', tool: 'resize', icon: 'shrink', label: 'Resize', summary: '', params: {}, hidden: true },
				{ tool: 'look', label: 'No id' },
				{ id: 's5', label: 'No tool' },
				{ id: 's6', tool: 'effects', label: 'Reel' }
			]
		});
		expect(restored.steps.map((step) => step.id)).toEqual(['s1', 's4', 's6']);
		expect(restored.cursor).toBe(2);
		expect(restored.steps[1].hidden).toBe(true);
		expect(restored.steps[2]).toMatchObject({ icon: '', summary: '', params: null });
		expect(restored.seq).toBe(6);
	});

	it('clamps the cursor and drops a pending edit whose target is gone or undone', () => {
		const base = { v: 1, seq: 2, steps: [{ id: 's1', tool: 'crop', label: 'Crop' }, { id: 's2', tool: 'resize', label: 'Resize' }] };
		expect(deserialize({ ...base, cursor: 40 }).cursor).toBe(2);
		expect(deserialize({ ...base, cursor: -3 }).cursor).toBe(0);
		expect(deserialize({ ...base, cursor: 'x' }).cursor).toBe(0);
		const pending = { tool: 'crop', label: 'Crop', target: 's2' };
		expect(deserialize({ ...base, cursor: 1, pending }).pending).toBeNull();
		expect(deserialize({ ...base, cursor: 2, pending }).pending?.target).toBe('s2');
		expect(deserialize({ ...base, cursor: 2, pending: { ...pending, target: 'gone' } }).pending).toBeNull();
		expect(deserialize({ ...base, cursor: 2, pending: { label: 'No tool' } }).pending).toBeNull();
	});

	it('keeps minting ids after the highest restored one', () => {
		const restored = deserialize({ v: 1, cursor: 1, seq: 1, steps: [{ id: 's9', tool: 'crop', label: 'Crop' }] });
		expect(apply(beginEdit(restored, resize)).steps[1].id).toBe('s10');
	});
});
