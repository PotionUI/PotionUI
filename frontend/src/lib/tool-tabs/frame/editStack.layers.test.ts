import { describe, expect, it } from 'vitest';
import {
	apply,
	beginEdit,
	commitLive,
	createStack,
	deserialize,
	duplicateStep,
	insertSteps,
	mergeInto,
	opacityOf,
	redo,
	renameStep,
	reopen,
	serialize,
	setOpacity,
	toggleHiddenUndoable,
	undo,
	type EditStackState,
	type StepDraft
} from './editStack';

function draft(tool: string, label = tool): StepDraft<unknown> {
	return { tool, icon: 'brush', label, summary: '', params: { layer: 'p1', url: 'u', layers: 1 } };
}

function stackOf(...tools: string[]): EditStackState<unknown> {
	return tools.reduce((state, tool) => apply(beginEdit(state, draft(tool))), createStack<unknown>());
}

const V1 = {
	v: 1,
	seq: 2,
	cursor: 2,
	pending: null,
	steps: [
		{ id: 's1', tool: 'paint', icon: 'brush', label: 'Paint', summary: '', params: { layer: 'p1' }, hidden: false, lane: 'paint' },
		{ id: 's2', tool: 'crop', icon: 'crop', label: 'Crop', summary: '', params: {}, hidden: true, lane: 'geometry' }
	]
};

describe('layer opacity on the edit stack', () => {
	it('opens a v1 stack unchanged: every step is at full strength', () => {
		const stack = deserialize(V1);
		expect(stack.steps.map((step) => opacityOf(step))).toEqual([1, 1]);
		expect(stack.steps.every((step) => step.layer === undefined)).toBe(true);
		expect(stack.steps[1].hidden).toBe(true);
	});

	it('opens a stack with no version as v1', () => {
		const { v: _v, ...bare } = V1;
		expect(deserialize(bare).steps).toHaveLength(2);
	});

	it('writes v1, byte for byte, while nothing needs v2', () => {
		const stack = deserialize(V1);
		expect(serialize(stack)).toEqual(V1);
		expect(serialize(setOpacity(stack, 's1', 1))).toEqual(V1);
	});

	it('writes v2 only once a layer is below full strength, and reads it back', () => {
		const faded = setOpacity(deserialize(V1), 's1', 0.5);
		const written = serialize(faded);
		expect(written.v).toBe(2);
		expect((written.steps[0] as { layer?: unknown }).layer).toEqual({ opacity: 0.5, blend: 'normal' });
		expect((written.steps[1] as { layer?: unknown }).layer).toBeUndefined();
		const reopened = deserialize(written);
		expect(opacityOf(reopened.steps[0])).toBe(0.5);
		expect(serialize(reopened)).toEqual(written);
	});

	it('writes v1 again when the opacity returns to 100', () => {
		const faded = setOpacity(deserialize(V1), 's1', 0.5);
		expect(serialize(setOpacity(faded, 's1', 1)).v).toBe(1);
	});

	it('clamps hostile stored opacities and drops unknown blends to normal', () => {
		const stored = { ...V1, v: 2, steps: [{ ...V1.steps[0], layer: { opacity: -3, blend: 'multiply' } }, { ...V1.steps[1], layer: { opacity: 'x' } }] };
		const stack = deserialize(stored);
		expect(stack.steps[0].layer).toEqual({ opacity: 0, blend: 'normal' });
		expect(stack.steps[1].layer).toBeUndefined();
	});

	it('refuses a version it does not know', () => {
		expect(deserialize({ ...V1, v: 3 }).steps).toEqual([]);
	});

	it('records one undo step per drag, however many live values came before it', () => {
		let stack = stackOf('paint');
		const id = stack.steps[0].id;
		for (const value of [0.9, 0.7, 0.5, 0.4]) stack = setOpacity(stack, id, value, true);
		stack = setOpacity(stack, id, 0.4, false);
		expect(opacityOf(stack.steps[0])).toBe(0.4);
		expect(stack.revisions).toHaveLength(1);
		stack = undo(stack);
		expect(opacityOf(stack.steps[0])).toBe(1);
		expect(stack.steps[0].layer).toBeUndefined();
		expect(stack.cursor).toBe(1);
		stack = redo(stack);
		expect(opacityOf(stack.steps[0])).toBe(0.4);
	});

	it('settles an unfinished drag when the stack is saved', () => {
		const stack = setOpacity(stackOf('paint'), 's1', 0.3, true);
		expect(serialize(stack).v).toBe(2);
		expect(commitLive(stack).revisions).toHaveLength(1);
	});

	it('makes hiding a layer undoable', () => {
		let stack = stackOf('paint');
		stack = toggleHiddenUndoable(stack, 's1');
		expect(stack.steps[0].hidden).toBe(true);
		stack = undo(stack);
		expect(stack.steps[0].hidden).toBe(false);
		expect(stack.cursor).toBe(1);
		stack = redo(stack);
		expect(stack.steps[0].hidden).toBe(true);
	});

	it('keeps the opacity when the layer is reopened and applied untouched', () => {
		let stack = setOpacity(stackOf('paint'), 's1', 0.5);
		const revisions = stack.revisions?.length ?? 0;
		stack = apply(reopen(stack, 's1'));
		expect(opacityOf(stack.steps[0])).toBe(0.5);
		expect(stack.revisions?.length ?? 0).toBe(revisions);
	});
});

describe('duplicating and merging layers', () => {
	it('puts a copy straight above the layer, with its fade, and undo takes it away', () => {
		let stack = stackOf('paint', 'paint', 'crop');
		stack = setOpacity(stack, 's1', 0.4);
		const next = duplicateStep(stack, 's1', { label: 'Sky copy' });
		expect(next.steps.map((step) => [step.id, step.label])).toEqual([['s1', 'paint'], ['s4', 'Sky copy'], ['s2', 'paint'], ['s3', 'crop']]);
		expect(opacityOf(next.steps[1])).toBe(0.4);
		expect(next.cursor).toBe(4);
		const back = undo(next);
		expect(back.steps.map((step) => step.id)).toEqual(['s1', 's2', 's3']);
		expect(back.cursor).toBe(3);
	});

	it('refuses to duplicate while a layer is open for painting', () => {
		const stack = reopen(stackOf('paint', 'paint'), 's1');
		expect(duplicateStep(stack, 's2')).toBe(stack);
	});

	it('merges a layer into the one below it as a single layer at full strength, and undo brings both back', () => {
		let stack = stackOf('paint', 'paint', 'crop');
		stack = setOpacity(stack, 's1', 0.5);
		const merged = mergeInto(stack, 's1', 's2', { layer: 'merged', url: 'm', layers: 1, name: 'Sky' }, 'Sky');
		expect(merged.steps.map((step) => step.id)).toEqual(['s1', 's3']);
		expect(merged.cursor).toBe(2);
		expect(merged.steps[0].params).toEqual({ layer: 'merged', url: 'm', layers: 1, name: 'Sky' });
		expect(opacityOf(merged.steps[0])).toBe(1);
		expect(serialize(merged).v).toBe(1);
		const back = undo(merged);
		expect(back.steps.map((step) => step.id)).toEqual(['s1', 's2', 's3']);
		expect(opacityOf(back.steps[0])).toBe(0.5);
	});

	it('only merges neighbours', () => {
		const stack = stackOf('paint', 'crop', 'paint');
		expect(mergeInto(stack, 's1', 's3', {})).toBe(stack);
	});
});

describe('adding layers at a position', () => {
	const labels = (state: EditStackState<unknown>) => state.steps.map((step) => step.label);

	it('puts new layers right above the anchor, in order, as one undo', () => {
		const stack = stackOf('paint', 'crop', 'effects');
		const next = insertSteps(stack, [draft('paint', 'A'), draft('paint', 'B')], 's1');
		expect(labels(next)).toEqual(['paint', 'A', 'B', 'crop', 'effects']);
		expect(next.cursor).toBe(5);
		expect(next.steps.map((step) => step.id)).toEqual(['s1', 's4', 's5', 's2', 's3']);
		expect(labels(undo(next))).toEqual(['paint', 'crop', 'effects']);
	});

	it('keeps undone steps behind the cursor and lands on top when the anchor is missing', () => {
		const stack = undo(stackOf('paint', 'crop'));
		expect(labels(insertSteps(stack, [draft('paint', 'A')], null))).toEqual(['paint', 'A', 'crop']);
		expect(labels(insertSteps(stack, [draft('paint', 'A')], 'gone'))).toEqual(['paint', 'A', 'crop']);
	});

	it('inserts right above the original for an empty anchor, and undo takes it out again', () => {
		const stack = stackOf('paint', 'crop');
		const next = insertSteps(stack, [draft('paint', 'A')], '');
		expect(labels(next)).toEqual(['A', 'paint', 'crop']);
		expect(labels(undo(next))).toEqual(['paint', 'crop']);
	});

	it('creates hidden layers when asked', () => {
		const next = insertSteps(stackOf('paint', 'crop'), [{ ...draft('paint', 'A'), hidden: true }], 's1');
		expect(next.steps[1].hidden).toBe(true);
	});

	it('renames a step undoably and refuses an empty name', () => {
		const stack = stackOf('paint');
		const renamed = renameStep(stack, 's1', ' Sky ', { params: { layer: 'p1', url: 'u', layers: 1, name: 'Sky' } });
		expect(renamed.steps[0].label).toBe('Sky');
		expect(labels(undo(renamed))).toEqual(['paint']);
		expect(renameStep(stack, 's1', '   ')).toBe(stack);
	});
});
