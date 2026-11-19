import { describe, expect, it } from 'vitest';
import { apply, appliedSteps, beginEdit, canRedo, createStack, deserialize, insertSteps, redo, remove, serialize, setOpacity, undo, undoLabel, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { EMPTY_IMAGE_PLAN } from '$lib/media/imageEditPlan';
import { renderRecipe } from '$lib/filters/render';
import golden from './layerParity.golden.json';
import { SWITCH_TO_LAYERS, arrangeLayers, compositeOver, isLayerStep, layerOrdered, settledLayered, switchToLayers, type Rgba } from './layers';
import { runSteps } from './payload';
import { cropDraft, lookDraft, paintDraft } from './steps';

const size = { width: 4, height: 3 };
const paintA = paintDraft({ layer: 'pa', url: 'ua', layers: 1, name: 'Sky' }, size) as StepDraft;
const paintB = paintDraft({ layer: 'pb', url: 'ub', layers: 1, name: 'Mist' }, size) as StepDraft;
const look = lookDraft({ kind: 'look', filter: 'punch', name: 'Punch', intensity: 100 }) as StepDraft;
const crop = cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rect: golden.composed.crop }, aspect: 'free' }, 'Free', { width: 2, height: 2 }) as StepDraft;

function addLayered(state: EditStackState, draft: StepDraft): EditStackState {
	return arrangeLayers(apply(beginEdit(state, draft)));
}

const ids = (state: EditStackState) => appliedSteps(state).map((step) => step.label);

describe('layers first, then edits', () => {
	it('keeps every new layer under the edits, newest layer on top of the layers', () => {
		let state = createStack();
		for (const draft of [paintA, look, paintB, crop]) state = addLayered(state, draft);
		expect(ids(state)).toEqual(['Sky', 'Mist', 'Punch', 'Crop']);
		expect(layerOrdered(state)).toBe(true);
	});

	it('undoes a layer added under existing edits as its own step and redoes it', () => {
		let state = addLayered(addLayered(createStack(), paintA), look);
		state = addLayered(state, paintB);
		expect(undoLabel(state)).toBe('Mist');
		state = arrangeLayers(undo(state));
		expect(ids(state)).toEqual(['Sky', 'Punch']);
		expect(canRedo(state)).toBe(true);
		state = arrangeLayers(redo(state));
		expect(ids(state)).toEqual(['Sky', 'Mist', 'Punch']);
	});

	it('places a whole batch of layers once and undoes them together', () => {
		let state = addLayered(createStack(), look);
		state = arrangeLayers(insertSteps(state, [paintA, paintB], null));
		expect(ids(state)).toEqual(['Sky', 'Mist', 'Punch']);
		expect(ids(arrangeLayers(undo(state)))).toEqual(['Punch']);
	});

	it('treats an old stack whose layers already sit under its edits as layered, and an interleaved one as not', () => {
		const old = (steps: StepDraft[]) => steps.reduce((state, draft) => apply(beginEdit(state, draft)), createStack());
		expect(layerOrdered(old([paintA, look, crop]))).toBe(true);
		expect(layerOrdered(old([crop, paintA]))).toBe(false);
		expect(layerOrdered(old([paintA, look, paintB]))).toBe(false);
		expect(settledLayered(old([crop]))).toBe(true);
		expect(settledLayered(undo(old([paintA, crop])))).toBe(false);
	});

	it('switches an interleaved stack to layers in one undoable step and keeps the stored format', () => {
		const state = [crop, paintA, look, paintB].reduce((acc, draft) => apply(beginEdit(acc, draft)), createStack());
		const before = serialize(state);
		const switched = switchToLayers(state);
		expect(ids(switched)).toEqual(['Sky', 'Mist', 'Crop', 'Punch']);
		expect(undoLabel(switched)).toBe(SWITCH_TO_LAYERS);
		expect(serialize(undo(switched))).toEqual(before);
		expect(serialize(switched).v).toBe(1);
	});

	it('writes a layered stack as a plain v1 stack whose order alone gives the layered result', () => {
		let state = createStack();
		for (const draft of [paintA, look, paintB]) state = addLayered(state, draft);
		const stored = serialize(state);
		expect(stored.v).toBe(1);
		expect(Object.keys(stored).sort()).toEqual(['cursor', 'pending', 'seq', 'steps', 'v']);
		expect(ids(deserialize(stored))).toEqual(['Sky', 'Mist', 'Punch']);
	});

	it('removing the step that interleaved an old stack makes it layered', () => {
		const state = [paintA, look, paintB].reduce((acc, draft) => apply(beginEdit(acc, draft)), createStack());
		expect(layerOrdered(state)).toBe(false);
		expect(settledLayered(remove(state, 's2'))).toBe(true);
	});

	it('keeps an Expand result with the edits and a region result with the layers', () => {
		const run = { v: 2, task: 'expand', prompt: '', setup: { preset_id: 'p', mode: 'm', form_data: {} }, area: { region: null, size: null }, candidates: 1, results: [], chosen: 0, seed: null, rect: null, soft: false, run: { id: 'r', status: 'done', error: null } };
		expect(isLayerStep({ tool: 'generate', params: { ...run, expand: { width: 800, height: 600, x: 80, y: 60 } } })).toBe(false);
		expect(isLayerStep({ tool: 'generate', params: { ...run, task: 'edit_region' } })).toBe(true);
	});
});

describe('layers, a look and a crop render the same in the browser and the saved file', () => {
	const data = golden.composed;
	const pixel = (list: number[][], index: number) => list[index] as unknown as Rgba;

	function layeredStack(): EditStackState {
		let state = createStack();
		for (const draft of [paintA, look, paintB, crop]) state = addLayered(state, draft);
		state = setOpacity(state, 's1', data.opacity[0]);
		return setOpacity(state, 's3', data.opacity[1]);
	}

	function browserRender(): { baked: number[][]; out: number[][] } {
		const composed = golden.base.map((base, index) =>
			data.layers.reduce<Rgba>((under, layer, step) => compositeOver(under, pixel(layer, index), data.opacity[step]), base as unknown as Rgba)
		);
		const buffer = { width: size.width, height: size.height, data: new Uint8ClampedArray(composed.flat()) };
		const looked = renderRecipe(buffer, { steps: data.look as never, cube: null }, 100);
		const baked = Array.from({ length: size.width * size.height }, (_, i) => Array.from(looked.data.slice(i * 4, i * 4 + 4)));
		const { x, y, width, height } = data.cropPx;
		const out: number[][] = [];
		for (let row = y; row < y + height; row += 1) for (let col = x; col < x + width; col += 1) out.push(baked[row * size.width + col]);
		return { baked, out };
	}

	it('composes the layers, applies the look to the composite, then crops', () => {
		const { baked, out } = browserRender();
		baked.forEach((value, index) => value.forEach((channel, c) => expect(Math.abs(channel - data.baked[index][c])).toBeLessThanOrEqual(1)));
		out.forEach((value, index) => value.forEach((channel, c) => expect(Math.abs(channel - data.expected[index][c])).toBeLessThanOrEqual(1)));
	});

	it('sends the server the layers first, then the baked look, then the crop', () => {
		const steps = appliedSteps(layeredStack());
		const lookId = steps.find((step) => step.tool === 'effects')!.id;
		expect(runSteps(steps, { source: size, baked: { [lookId]: 'look' } })).toEqual(data.payload);
	});
});
