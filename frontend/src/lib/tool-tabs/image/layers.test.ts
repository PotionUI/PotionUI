import { describe, expect, it } from 'vitest';
import { apply, beginEdit, createStack, deserialize, serialize, setOpacity, type EditStep } from '$lib/tool-tabs/frame/editStack';
import golden from './layerParity.golden.json';
import { appliesBeneath, compositeOver, isFlattenedPaint, isLayerStep, layerThumb, mergeLayerPixels, withLayerOpacity, type Rgba } from './layers';
import { runSteps } from './payload';
import { paintDraft } from './steps';

describe('layer compositing parity with the server', () => {
	for (const { opacity, expected } of golden.cases) {
		it(`composes the fixture at ${Math.round(opacity * 100)}% like the saved file`, () => {
			golden.base.forEach((base, index) => {
				const got = compositeOver(base as unknown as Rgba, golden.layer[index] as unknown as Rgba, opacity);
				got.forEach((value, channel) => expect(Math.abs(value - expected[index][channel])).toBeLessThanOrEqual(1));
			});
		});
	}

	it('leaves the picture alone at 0% and replaces it under an opaque layer at 100%', () => {
		expect(compositeOver([10, 20, 30, 255], [200, 100, 50, 255], 0)).toEqual([10, 20, 30, 255]);
		expect(compositeOver([10, 20, 30, 255], [200, 100, 50, 255], 1)).toEqual([200, 100, 50, 255]);
		expect(compositeOver([10, 20, 30, 255], [200, 100, 50, 255], 0.5)).toEqual([105, 60, 40, 255]);
	});
});

describe('layer steps in the payload', () => {
	const source = { width: 100, height: 100 };

	function paintStack(opacity: number): EditStep[] {
		let stack = apply(beginEdit(createStack<unknown>(), paintDraft({ layer: 'p1', url: 'u', layers: 1 }, source)));
		stack = setOpacity(stack, stack.steps[0].id, opacity);
		return stack.steps;
	}

	it('sends no opacity for a full-strength layer, exactly as before', () => {
		expect(runSteps(paintStack(1), { source })).toEqual([{ tool: 'paint_layer', params: { layer: { item_id: 'p1' } } }]);
	});

	it('sends the opacity to the server for a faded layer', () => {
		expect(runSteps(paintStack(0.5), { source })).toEqual([{ tool: 'paint_layer', params: { layer: { item_id: 'p1' }, opacity: 0.5 } }]);
	});

	it('keeps a hidden layer hidden and leaves empty placeholders alone', () => {
		const steps = paintStack(0.5).map((step) => ({ ...step, hidden: true }));
		expect(runSteps(steps, { source })[0]).toMatchObject({ hidden: true });
		expect(withLayerOpacity({ layer: { opacity: 0.5, blend: 'normal' } }, [{ tool: 'place_patch', params: {}, hidden: true }])).toEqual([{ tool: 'place_patch', params: {}, hidden: true }]);
	});

	it('marks paint and generated steps as layers and looks as applying beneath', () => {
		expect(isLayerStep({ tool: 'paint' })).toBe(true);
		expect(isLayerStep({ tool: 'generate' })).toBe(true);
		expect(isLayerStep({ tool: 'crop' })).toBe(false);
		expect(appliesBeneath({ tool: 'effects' })).toBe(true);
		expect(appliesBeneath({ tool: 'paint' })).toBe(false);
		expect(layerThumb({ tool: 'paint', params: { url: 'u' } })).toBe('u');
		expect(layerThumb({ tool: 'crop', params: {} })).toBeNull();
	});
});

describe('three stacked paint layers', () => {
	const stack = golden.stack;
	const pixel = (list: number[][], index: number) => list[index] as unknown as Rgba;
	const flat = (list: number[][]) => new Uint8ClampedArray(list.flat());

	it('compose in order like the saved file', () => {
		golden.base.forEach((base, index) => {
			let got = base as unknown as Rgba;
			stack.layers.forEach((layer, step) => {
				got = compositeOver(got, pixel(layer, index), stack.opacity[step]);
			});
			got.forEach((value, channel) => expect(Math.abs(value - stack.expected[index][channel])).toBeLessThanOrEqual(2));
		});
	});

	it('merging the top two layers down looks the same as keeping them apart', () => {
		const merged = mergeLayerPixels(flat(stack.layers[1]), stack.opacity[1], flat(stack.layers[2]), stack.opacity[2]);
		golden.base.forEach((base, index) => {
			const under = compositeOver(base as unknown as Rgba, pixel(stack.layers[0], index), stack.opacity[0]);
			const apart = compositeOver(compositeOver(under, pixel(stack.layers[1], index), stack.opacity[1]), pixel(stack.layers[2], index), stack.opacity[2]);
			const together = compositeOver(under, [merged[index * 4], merged[index * 4 + 1], merged[index * 4 + 2], merged[index * 4 + 3]], 1);
			together.forEach((value, channel) => expect(Math.abs(value - apart[channel])).toBeLessThanOrEqual(2));
		});
	});

	it('tells a one-PNG paint step from a kept layer', () => {
		expect(isFlattenedPaint({ tool: 'paint', params: { layer: 'p', url: 'u', layers: 3 } })).toBe(true);
		expect(isFlattenedPaint({ tool: 'paint', params: { layer: 'p', url: 'u', layers: 1, name: 'Sky' } })).toBe(false);
		expect(isFlattenedPaint({ tool: 'crop', params: {} })).toBe(false);
	});

	it('sends one paint_layer per layer, in order, each with its own fade', () => {
		const source = { width: 4, height: 3 };
		let state = createStack<unknown>();
		['Sky', 'Hills', 'Mist'].forEach((name, i) => {
			state = apply(beginEdit(state, paintDraft({ layer: `p${i}`, url: `u${i}`, layers: 1, name }, source)));
			if (i) state = setOpacity(state, state.steps[i].id, stack.opacity[i]);
		});
		const entries = runSteps(state.steps, { source });
		expect(entries).toEqual([
			{ tool: 'paint_layer', params: { layer: { item_id: 'p0' } } },
			{ tool: 'paint_layer', params: { layer: { item_id: 'p1' }, opacity: stack.opacity[1] } },
			{ tool: 'paint_layer', params: { layer: { item_id: 'p2' }, opacity: stack.opacity[2] } }
		]);
	});

	it('still reads a one-PNG paint step from an earlier version and writes it back as v1', () => {
		const old = { v: 1, seq: 1, cursor: 1, pending: null, steps: [{ id: 's1', tool: 'paint', icon: 'brush', label: 'Paint · 3 layers', summary: '640×480', params: { layer: 'p9', url: 'u9', layers: 3 }, hidden: false, lane: 'paint' }] };
		const stack = deserialize(old);
		expect(isFlattenedPaint(stack.steps[0])).toBe(true);
		expect(runSteps(stack.steps, { source: { width: 640, height: 480 } })).toEqual([{ tool: 'paint_layer', params: { layer: { item_id: 'p9' } } }]);
		expect(serialize(stack)).toEqual(old);
	});
});
