import { describe, expect, it } from 'vitest';
import { apply, beginEdit, createStack, setOpacity, type EditStep } from '$lib/tool-tabs/frame/editStack';
import golden from './layerParity.golden.json';
import { appliesBeneath, compositeOver, isLayerStep, layerThumb, withLayerOpacity, type Rgba } from './layers';
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
