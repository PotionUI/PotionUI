import { describe, expect, it } from 'vitest';
import type { EditStackState, EditStep } from '$lib/tool-tabs/frame/editStack';
import { ensureCoreImageEditor } from './coreEditor';
import { generateDraft, readGenerateParams, type GenerateParams } from './generate/generateStep';
import { geometryKey } from './mask';
import { landUpscale, rebaseStep } from './upscaleRebase';

ensureCoreImageEditor();

const step = (id: string, tool: string, params: unknown, extra: Partial<EditStep> = {}): EditStep => ({ id, tool, icon: 'x', label: tool, summary: '', params, hidden: false, ...extra });

function upscaleParams(over: Partial<GenerateParams> = {}): GenerateParams {
	return {
		v: 2,
		task: 'upscale',
		prompt: '',
		setup: { preset_id: 'p', mode: 'editor:upscale', form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' },
		area: { region: null, size: null },
		candidates: 1,
		results: [{ patch: { item_id: 'patch-1' }, url: '/patch-1.png', seed: 1, size: { width: 2048, height: 1536 } }],
		chosen: 0,
		seed: null,
		rect: null,
		soft: false,
		run: { id: 'er_1', status: 'done', error: null },
		basis: { geometry: '[]', size: { width: 1024, height: 768 } },
		options: { scale: 2 },
		...over
	};
}

function upscaleStep(id: string, params: GenerateParams): EditStep {
	const draft = generateDraft(params, 'Upscale', 'mode-upscale');
	return { ...draft, id, hidden: false };
}

function stackOf(steps: EditStep[]): EditStackState<unknown> {
	return { steps, cursor: steps.length, pending: null, seq: steps.length };
}

describe('rebaseStep', () => {
	it('scales a resize target and its summary', () => {
		const next = rebaseStep(step('s2', 'resize', { width: 800, height: 600, lock: true }), 2);
		expect(next.params).toEqual({ width: 1600, height: 1200, lock: true });
		expect(next.summary).toBe('1600×1200 · Lanczos');
	});

	it('scales canvas sizes and leaves percent and anchors alone', () => {
		const next = rebaseStep(step('s2', 'canvas', { values: { width: 500, height: 400, scale_percent: 100, anchor: 'center' } }), 2);
		expect((next.params as { values: Record<string, unknown> }).values).toEqual({ width: 1000, height: 800, scale_percent: 100, anchor: 'center' });
	});

	it('scales the crop output side and leaves the normalised rectangle alone', () => {
		const plan = { rotation: 0, flipHorizontal: false, flipVertical: false, rect: { x: 0.1, y: 0.1, width: 0.5, height: 0.5 }, targetLongestSide: 1000 };
		const next = rebaseStep(step('s2', 'crop', { plan, aspect: 'free' }), 3);
		expect((next.params as { plan: typeof plan }).plan).toEqual({ ...plan, targetLongestSide: 3000 });
		const kept = rebaseStep(step('s2', 'crop', { plan: { ...plan, targetLongestSide: null }, aspect: 'free' }), 3);
		expect((kept.params as { plan: typeof plan }).plan.targetLongestSide).toBeNull();
	});

	it('leaves normalised and unknown steps as they are, and a factor of one', () => {
		const paint = step('s2', 'paint', { layer: 'l1', url: '/l.png', layers: 1 });
		expect(rebaseStep(paint, 4)).toBe(paint);
		const resize = step('s3', 'resize', { width: 800, height: 600, lock: false });
		expect(rebaseStep(resize, 1)).toBe(resize);
	});

	it('scales the marked area size and the recorded picture of a region edit', () => {
		const region = upscaleStep('s4', upscaleParams({ task: 'edit_region', area: { region: { item_id: 'm' }, size: { width: 100, height: 50 } }, basis: { geometry: '[]', size: { width: 1000, height: 500 } }, results: [] }));
		const next = readGenerateParams(rebaseStep(region, 2).params) as GenerateParams;
		expect(next.area.size).toEqual({ width: 200, height: 100 });
		expect(next.basis?.size).toEqual({ width: 2000, height: 1000 });
	});
});

describe('landUpscale', () => {
	it('records the output size and scales what sits above, leaving what is beneath untouched', () => {
		const beneath = step('s1', 'resize', { width: 1024, height: 768, lock: true });
		const above = step('s3', 'resize', { width: 500, height: 400, lock: false });
		const stack = stackOf([beneath, upscaleStep('s2', upscaleParams()), above]);
		const next = landUpscale(stack, 's2', { width: 2048, height: 1536 });
		expect(next.steps[0]).toBe(beneath);
		expect(next.steps[2].params).toEqual({ width: 1000, height: 800, lock: false });
		expect(readGenerateParams(next.steps[1].params)?.out).toEqual({ width: 2048, height: 1536 });
	});

	it('rescales only by the change when it lands again at another size', () => {
		const landed = upscaleStep('s2', upscaleParams({ out: { width: 2048, height: 1536 }, results: [{ patch: { item_id: 'p2' }, url: '/p2.png', seed: 1, size: { width: 4096, height: 3072 } }] }));
		const above = step('s3', 'resize', { width: 1000, height: 800, lock: false });
		const next = landUpscale(stackOf([landed, above]), 's2', { width: 4096, height: 3072 });
		expect(next.steps[1].params).toEqual({ width: 2000, height: 1600, lock: false });
	});

	it('does nothing a second time at the same size', () => {
		const landed = upscaleStep('s2', upscaleParams({ out: { width: 2048, height: 1536 } }));
		const above = step('s3', 'resize', { width: 1000, height: 800, lock: false });
		const stack = stackOf([landed, above]);
		expect(landUpscale(stack, 's2', { width: 2048, height: 1536 })).toBe(stack);
	});

	it('keeps a region edit above in sync with the new geometry so it is not marked out of date', () => {
		const region = (geometry: string) => upscaleStep('s3', upscaleParams({ task: 'edit_region', results: [{ patch: { item_id: 'r' }, url: '/r.png', seed: 1 }], area: { region: { item_id: 'm' }, size: { width: 100, height: 100 } }, basis: { geometry, size: { width: 1024, height: 768 } } }));
		const inFlight = upscaleStep('s2', upscaleParams({ run: { id: 'er_1', status: 'running', error: null }, results: [] }));
		const was = geometryKey([inFlight]);
		const stack = stackOf([upscaleStep('s2', upscaleParams()), { ...region(was), lane: 'paint' }]);
		const next = landUpscale(stack, 's2', { width: 2048, height: 1536 });
		const nowKey = geometryKey([next.steps[0]]);
		expect(nowKey).not.toBe(was);
		expect(readGenerateParams(next.steps[1].params)?.basis?.geometry).toBe(nowKey);
		expect(readGenerateParams(next.steps[1].params)?.area.size).toEqual({ width: 200, height: 200 });
	});

	it('answers the same stack for an unknown step', () => {
		const stack = stackOf([step('s1', 'resize', { width: 1, height: 1, lock: false })]);
		expect(landUpscale(stack, 'nope', { width: 2, height: 2 })).toBe(stack);
	});
});
