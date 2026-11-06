import { describe, expect, it } from 'vitest';
import { OPS_BY_ID } from '$lib/filters/engine';
import { apply, beginEdit, commitLive, createStack, effectiveSteps, redo, undo, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import {
	ADJUST_SECTIONS,
	adjustEditedCount,
	adjustStepFor,
	adjustSummaryOf,
	adjustValues,
	isAdjustStep,
	opDefaults,
	resetAdjustOp,
	sectionEdited,
	setAdjustToggle,
	setAdjustValue,
	toggleIsOn
} from './adjustSections';
import { ADJUST_OPS, cropDraft, lookDraft, type AdjustParams } from './steps';
import { EMPTY_IMAGE_PLAN } from '$lib/media/editors/imageEditPlan';

const tone = OPS_BY_ID.tone;
const exposure = OPS_BY_ID.exposure;
const grayscale = OPS_BY_ID.grayscale;
const lookup = (op: string) => OPS_BY_ID[op];

function params(stack: EditStackState<unknown>, op: string): AdjustParams {
	return adjustStepFor(stack, op)!.params as AdjustParams;
}

describe('adjust sections', () => {
	it('only reference known ops and params', () => {
		for (const section of ADJUST_SECTIONS) {
			for (const control of section.controls) {
				const spec = OPS_BY_ID[control.op];
				expect(spec, control.op).toBeDefined();
				if (control.kind === 'slider') expect(spec.params.map((param) => param.id)).toContain(control.param);
			}
		}
	});

	it('cover every adjustment op of the old picker, so nothing became unreachable', () => {
		const covered = new Set(ADJUST_SECTIONS.flatMap((section) => section.controls.map((control) => control.op)));
		expect([...covered].sort()).toEqual([...ADJUST_OPS].sort());
	});

	it('lists each slider param once', () => {
		const keys = ADJUST_SECTIONS.flatMap((section) => section.controls.filter((control) => control.kind === 'slider').map((control) => `${control.op}.${'param' in control ? control.param : ''}`));
		expect(new Set(keys).size).toBe(keys.length);
	});
});

describe('writing adjustments live', () => {
	it('defaults come from the op params and curves start flat', () => {
		expect(opDefaults('tone', tone)).toEqual({ brightness: 0, contrast: 0, saturation: 0, hue: 0 });
		expect(opDefaults('curves', OPS_BY_ID.curves)).toEqual({ shadows: 0, midtones: 0, highlights: 0 });
		expect(opDefaults('missing', undefined)).toEqual({});
	});

	it('creates one step on the first move and keeps updating that step', () => {
		let stack: EditStackState<unknown> = createStack();
		for (const value of [4, 9, 18]) stack = setAdjustValue(stack, tone, 'contrast', value);
		expect(stack.steps).toHaveLength(1);
		expect(params(stack, 'tone').values).toEqual({ brightness: 0, contrast: 18, saturation: 0, hue: 0 });
		expect(stack.steps[0].summary).toBe('Contrast 18');
		expect(stack.steps[0].label).toBe('Tone');
	});

	it('keeps the other params of the op when one slider moves', () => {
		let stack = setAdjustValue(createStack(), tone, 'contrast', 18);
		stack = commitLive(setAdjustValue(stack, tone, 'saturation', -6));
		expect(params(stack, 'tone').values).toEqual({ brightness: 0, contrast: 18, saturation: -6, hue: 0 });
		expect(stack.steps[0].summary).toBe('Contrast 18 · Saturation -6');
	});

	it('does not leave a step behind when a slider goes back to its default', () => {
		const dragged = setAdjustValue(createStack(), tone, 'contrast', 18);
		const back = setAdjustValue(dragged, tone, 'contrast', 0);
		expect(back.steps).toEqual([]);
		expect(setAdjustValue(createStack(), tone, 'contrast', 0).steps).toEqual([]);
	});

	it('undoes a whole drag as one step and redoes it', () => {
		let stack: EditStackState<unknown> = createStack();
		for (const value of [4, 9, 18]) stack = setAdjustValue(stack, exposure, 'stops', value / 10);
		expect(effectiveSteps(undo(stack))).toEqual([]);
		expect(params(redo(undo(stack)), 'exposure').values).toEqual({ stops: 1.8 });
	});

	it('groups two separate drags of one slider into two undo steps', () => {
		let stack = commitLive(setAdjustValue(setAdjustValue(createStack(), tone, 'contrast', 10), tone, 'contrast', 20));
		stack = commitLive(setAdjustValue(setAdjustValue(stack, tone, 'contrast', 30), tone, 'contrast', 40));
		expect(params(undo(stack), 'tone').values.contrast).toBe(20);
		expect(effectiveSteps(undo(undo(stack)))).toEqual([]);
	});

	it('keeps adjustments of different ops in separate steps, in the order first touched', () => {
		let stack = setAdjustValue(createStack(), exposure, 'stops', 0.5);
		stack = setAdjustValue(stack, tone, 'contrast', 10);
		stack = setAdjustValue(stack, exposure, 'stops', 0.9);
		expect(stack.steps.map((step) => (step.params as AdjustParams).op)).toEqual(['exposure', 'tone']);
		expect(adjustEditedCount(stack)).toBe(2);
	});

	it('leaves a pending geometry step alone and applies it after the live steps', () => {
		const crop = cropDraft({ plan: EMPTY_IMAGE_PLAN, aspect: 'free' }, 'Free', { width: 10, height: 10 }) as never;
		const pending = beginEdit(createStack<unknown>(), crop);
		const next = setAdjustValue(pending, tone, 'contrast', 12);
		expect(next.pending?.tool).toBe('crop');
		const applied = apply(next);
		expect(applied.steps.map((step) => step.tool)).toEqual(['effects', 'crop']);
	});

	it('does not touch look steps that share the effects tool', () => {
		const base = apply(beginEdit(createStack<unknown>(), lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 60 }) as never));
		const next = setAdjustValue(base, tone, 'contrast', 12);
		expect(next.steps).toHaveLength(2);
		expect(isAdjustStep(next.steps[0])).toBe(false);
		expect(isAdjustStep(next.steps[1])).toBe(true);
	});

	it('reads values back for the sliders', () => {
		const stack = setAdjustValue(createStack(), tone, 'hue', 30);
		expect(adjustValues(stack, 'tone', opDefaults('tone', tone)).hue).toBe(30);
		expect(adjustValues(stack, 'exposure', opDefaults('exposure', exposure))).toEqual({ stops: 0 });
	});

	it('reports which sections hold edits', () => {
		const stack = setAdjustValue(createStack(), tone, 'saturation', -6);
		const [light, colour, effects] = ADJUST_SECTIONS;
		expect(sectionEdited(light, stack, lookup)).toBe(true);
		expect(sectionEdited(colour, stack, lookup)).toBe(true);
		expect(sectionEdited(effects, stack, lookup)).toBe(false);
	});

	it('resets an op to its defaults as an undoable edit', () => {
		let stack = commitLive(setAdjustValue(createStack(), tone, 'contrast', 20));
		stack = resetAdjustOp(stack, tone);
		expect(params(stack, 'tone').values.contrast).toBe(0);
		expect(sectionEdited(ADJUST_SECTIONS[0], stack, lookup)).toBe(false);
		expect(params(undo(stack), 'tone').values.contrast).toBe(20);
		expect(resetAdjustOp(createStack(), tone).steps).toEqual([]);
	});

	it('summarises only values that differ from the defaults', () => {
		expect(adjustSummaryOf({ a: 0, b: 5 }, { a: 0, b: 0 }, { b: 'Bee' })).toBe('Bee 5');
		expect(adjustSummaryOf({ a: 0 }, { a: 0 }, {})).toBe('On');
	});
});

describe('toggles', () => {
	it('adds a step when switched on and hides it when switched off', () => {
		let stack = setAdjustToggle(createStack(), grayscale, true);
		expect(toggleIsOn(stack, 'grayscale')).toBe(true);
		expect(effectiveSteps(stack)).toHaveLength(1);
		stack = setAdjustToggle(stack, grayscale, false);
		expect(toggleIsOn(stack, 'grayscale')).toBe(false);
		expect(effectiveSteps(stack)).toHaveLength(0);
		expect(stack.steps).toHaveLength(1);
	});

	it('switches back on by unhiding the same step', () => {
		let stack = setAdjustToggle(createStack(), grayscale, true);
		stack = setAdjustToggle(setAdjustToggle(stack, grayscale, false), grayscale, true);
		expect(stack.steps).toHaveLength(1);
		expect(toggleIsOn(stack, 'grayscale')).toBe(true);
	});

	it('undoes switching on', () => {
		const stack = setAdjustToggle(createStack(), grayscale, true);
		expect(effectiveSteps(undo(stack))).toEqual([]);
	});

	it('does nothing when switching off something that is not on', () => {
		const stack = createStack<unknown>();
		expect(setAdjustToggle(stack, grayscale, false)).toBe(stack);
	});
});
