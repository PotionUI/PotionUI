import { describe, expect, it } from 'vitest';
import { OPS_BY_ID } from '$lib/filters/engine';
import { appliedSteps, commitLive, deserialize, serialize, undo, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { adjustStepFor, setAdjustToggle } from './adjustSections';
import {
	GRADE_ENTRIES,
	LIVE_EFFECTS,
	appliedEntries,
	bundleStep,
	entryOfStep,
	gradeValue,
	liveEffectState,
	resetGrade,
	setGradeValue
} from './entries';
import { runSteps, payloadGaps } from './payload';
import { adjustRecipeSteps, type AdjustParams } from './steps';
import { readImageState, writeImageState } from './state';
import { OLD_ADJUST_EDIT, OLD_EFFECT_EDIT, OLD_LOOK_EDIT, OLD_MASK_STATE } from './fixtures/oldEdits';

const lookup = (op: string) => OPS_BY_ID[op];
const light = GRADE_ENTRIES.light;
const colour = GRADE_ENTRIES.colour;
const control = (entry: typeof light, op: string, param: string) => entry.controls.find((c) => c.op === op && c.param === param)!;

function empty(): EditStackState<unknown> {
	return deserialize({ v: 1, steps: [], cursor: 0, pending: null, seq: 0 });
}

function paramsOf(stack: EditStackState<unknown>, bundle: string): AdjustParams {
	return bundleStep(stack, bundle)!.params as AdjustParams;
}

describe('Light and Colour independence on the shared tone op', () => {
	it('editing Light never changes Colour and the reverse', () => {
		let stack = setGradeValue(empty(), light, control(light, 'tone', 'brightness'), 30, lookup);
		stack = setGradeValue(commitLive(stack), colour, control(colour, 'tone', 'saturation'), -40, lookup);
		stack = commitLive(stack);
		expect(gradeValue(stack, light, control(light, 'tone', 'brightness'), lookup)).toBe(30);
		expect(gradeValue(stack, light, control(light, 'tone', 'contrast'), lookup)).toBe(0);
		expect(gradeValue(stack, colour, control(colour, 'tone', 'saturation'), lookup)).toBe(-40);
		expect(gradeValue(stack, colour, control(colour, 'tone', 'hue'), lookup)).toBe(0);
		expect(paramsOf(stack, 'light').values['tone.saturation']).toBeUndefined();
		expect(paramsOf(stack, 'colour').values['tone.brightness']).toBeUndefined();
		const before = JSON.stringify(paramsOf(stack, 'colour'));
		stack = setGradeValue(stack, light, control(light, 'exposure', 'stops'), 0.5, lookup);
		expect(JSON.stringify(paramsOf(commitLive(stack), 'colour'))).toBe(before);
	});

	it('writes one step named after the entry and bundles the ops behind it', () => {
		let stack = setGradeValue(empty(), light, control(light, 'exposure', 'stops'), 0.3, lookup);
		stack = setGradeValue(stack, light, control(light, 'tone', 'contrast'), 12, lookup);
		stack = commitLive(stack);
		expect(appliedSteps(stack)).toHaveLength(1);
		const step = appliedSteps(stack)[0];
		expect(step.label).toBe('Light');
		expect(step.summary).toBe('Exposure +0.3 · Contrast +12');
		expect(adjustRecipeSteps(step.params as AdjustParams)).toEqual([
			{ op: 'exposure', stops: 0.3 },
			{ op: 'tone', brightness: 0, contrast: 12 },
			{ op: 'fade', black_lift: 0, white_cap: 0 }
		]);
	});

	it('the colour bundle expands to white balance, tone and vibrance', () => {
		const stack = commitLive(setGradeValue(empty(), colour, control(colour, 'tone', 'hue'), 20, lookup));
		expect(adjustRecipeSteps(appliedSteps(stack)[0].params as AdjustParams)).toEqual([
			{ op: 'white_balance', temperature: 0, tint: 0 },
			{ op: 'tone', saturation: 0, hue: 20 },
			{ op: 'vibrance', amount: 0 }
		]);
	});

	it('removes a bundle step that comes back to its defaults in the same edit', () => {
		let stack = setGradeValue(empty(), light, control(light, 'tone', 'brightness'), 10, lookup);
		stack = setGradeValue(stack, light, control(light, 'tone', 'brightness'), 0, lookup);
		expect(appliedSteps(commitLive(stack))).toHaveLength(0);
	});

	it('reset puts the entry back to defaults in one undoable change', () => {
		let stack = commitLive(setGradeValue(empty(), light, control(light, 'tone', 'brightness'), 10, lookup));
		stack = resetGrade(stack, light, lookup);
		expect(gradeValue(stack, light, control(light, 'tone', 'brightness'), lookup)).toBe(0);
		stack = undo(stack);
		expect(gradeValue(stack, light, control(light, 'tone', 'brightness'), lookup)).toBe(10);
	});
});

describe('old Adjust steps open in the entry that owns them without being rewritten', () => {
	const stack = deserialize(OLD_ADJUST_EDIT);

	it('a stored tone step shows brightness and contrast in Light and saturation and hue in Colour', () => {
		expect(gradeValue(stack, light, control(light, 'tone', 'brightness'), lookup)).toBe(20);
		expect(gradeValue(stack, light, control(light, 'tone', 'contrast'), lookup)).toBe(10);
		expect(gradeValue(stack, colour, control(colour, 'tone', 'saturation'), lookup)).toBe(-15);
		expect(gradeValue(stack, colour, control(colour, 'tone', 'hue'), lookup)).toBe(0);
		expect(gradeValue(stack, light, control(light, 'exposure', 'stops'), lookup)).toBe(0.5);
		expect(gradeValue(stack, colour, control(colour, 'white_balance', 'temperature'), lookup)).toBe(12);
	});

	it('maps every old step to an entry', () => {
		expect(appliedSteps(stack).map((step) => entryOfStep(step))).toEqual(['light', 'light', 'colour', 'vignette', 'film_grain']);
	});

	it('shows the applied dots for the entries that have a step', () => {
		expect([...appliedEntries(stack, lookup)].sort()).toEqual(['colour', 'film_grain', 'light', 'vignette']);
	});

	it('editing Light edits the stored tone step in place and leaves saturation alone', () => {
		const next = commitLive(setGradeValue(stack, light, control(light, 'tone', 'brightness'), 55, lookup));
		const tone = adjustStepFor(next, 'tone')!.params as AdjustParams;
		expect(tone.values).toEqual({ brightness: 55, contrast: 10, saturation: -15, hue: 0 });
		expect(appliedSteps(next)).toHaveLength(5);
		expect(bundleStep(next, 'light')).toBeUndefined();
	});

	it('opening and serialising an untouched old edit changes nothing', () => {
		expect(serialize(stack)).toEqual(OLD_ADJUST_EDIT);
	});

	it('a toggle written by an entry keeps the old op shape', () => {
		const next = commitLive(setAdjustToggle(empty(), OPS_BY_ID.grayscale, true));
		expect((appliedSteps(next)[0].params as AdjustParams).op).toBe('grayscale');
		expect(entryOfStep(appliedSteps(next)[0])).toBe('grayscale');
	});
});

describe('Film grain and Vignette keep both stored shapes readable', () => {
	it('an old grain op step is the Film grain entry step and an effect card step is too', () => {
		const old = deserialize(OLD_ADJUST_EDIT);
		expect(liveEffectState(old, LIVE_EFFECTS.film_grain).shape).toBe('legacy');
		expect(liveEffectState(old, LIVE_EFFECTS.vignette).shape).toBe('legacy');
		const card = deserialize(OLD_EFFECT_EDIT);
		expect(liveEffectState(card, LIVE_EFFECTS.film_grain).shape).toBe('effect');
		expect(liveEffectState(card, LIVE_EFFECTS.vignette).shape).toBe('none');
		expect([...appliedEntries(card, lookup)]).toEqual(['film_grain']);
	});
});

describe('old edits produce the same payload', () => {
	const SOURCE = { width: 1024, height: 768 };

	it('adjust steps bake as before and the recipe is the stored op', () => {
		const steps = appliedSteps(deserialize(OLD_ADJUST_EDIT));
		expect(payloadGaps(steps, { source: SOURCE })).toEqual(steps.map((step) => ({ kind: 'bake', step: step.id })));
		expect(runSteps(steps, { source: SOURCE, baked: Object.fromEntries(steps.map((step) => [step.id, `art-${step.id}`])) })).toEqual(
			steps.map((step) => ({ tool: 'paint_layer', params: { layer: { item_id: `art-${step.id}` } } }))
		);
		expect(adjustRecipeSteps(steps[0].params as AdjustParams)).toEqual([{ op: 'tone', brightness: 20, contrast: 10, saturation: -15, hue: 0 }]);
		expect(adjustRecipeSteps(steps[3].params as AdjustParams)).toEqual([{ op: 'vignette', amount: 30, midpoint: 50, feather: 60 }]);
	});

	it('a look keeps its bake and its intensity', () => {
		const steps = appliedSteps(deserialize(OLD_LOOK_EDIT));
		expect(payloadGaps(steps, { source: SOURCE })).toEqual([{ kind: 'bake', step: 's1' }]);
		expect((steps[0].params as { intensity: number }).intensity).toBe(65);
		expect(entryOfStep(steps[0])).toBe('effects');
	});

	it('an effect card goes to the server as the effect tool with its values', () => {
		const steps = appliedSteps(deserialize(OLD_EFFECT_EDIT));
		expect(payloadGaps(steps, { source: SOURCE })).toEqual([]);
		expect(runSteps(steps, { source: SOURCE })).toEqual([{ tool: 'film_grain', params: { amount: 42, size: 1.8, color: false, seed: 3 } }]);
	});

	it('a saved mask reads and writes back unchanged', () => {
		const state = readImageState(OLD_MASK_STATE);
		expect(state.mask).toEqual(OLD_MASK_STATE.mask);
		expect(writeImageState(state)).toEqual(OLD_MASK_STATE);
	});
});
