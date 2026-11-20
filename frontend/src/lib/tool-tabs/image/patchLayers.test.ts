import { describe, expect, it } from 'vitest';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import { aboveKeyOf, aboveLayerOf, patchMaskOf } from './layers';
import { maskOutline } from './maskOutline';

function generated(over: Record<string, unknown> = {}, step: Partial<EditStep> = {}): EditStep {
	return {
		id: 's2',
		tool: 'generate',
		icon: 'x',
		label: 'Add clouds',
		summary: 'Edit a region',
		lane: 'paint',
		hidden: false,
		params: {
			v: 2,
			task: 'edit_region',
			prompt: 'clouds',
			setup: { preset_id: 'p', mode: 'editor:edit', form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' },
			area: { region: { item_id: 'r1', url: '/api/mask.png' }, size: null },
			candidates: 1,
			results: [{ patch: { item_id: 'patch-1' }, url: '/api/patch.png', seed: 1 }],
			chosen: 0,
			seed: 1,
			rect: { x: 0.1, y: 0.2, width: 0.5, height: 0.25 },
			soft: false,
			run: { id: 'er_1', status: 'done', error: null },
			...over
		},
		...step
	} as EditStep;
}

const paint = (step: Partial<EditStep> = {}): EditStep => ({ id: 's1', tool: 'paint', icon: 'x', label: 'Sketch', summary: '', lane: 'paint', hidden: false, params: { url: '/api/sketch.png' }, ...step }) as EditStep;

describe('layers drawn above a layer being painted', () => {
	it('places a finished patch in its rect with its opacity', () => {
		expect(aboveLayerOf(generated({}, { layer: { opacity: 0.4 } } as never))).toEqual({ url: '/api/patch.png', rect: { x: 0.1, y: 0.2, width: 0.5, height: 0.25 }, opacity: 0.4 });
	});

	it('keeps paint layers full frame and skips hidden, unfinished and unplaced ones', () => {
		expect(aboveLayerOf(paint())).toEqual({ url: '/api/sketch.png', rect: null, opacity: 1 });
		expect(aboveLayerOf(paint({ hidden: true }))).toBeNull();
		expect(aboveLayerOf(generated({ run: { id: 'er_1', status: 'running', error: null } }))).toBeNull();
		expect(aboveLayerOf(generated({ rect: null }))).toBeNull();
		expect(aboveLayerOf({ ...paint(), tool: 'effects' })).toBeNull();
	});

	it('changes its key with opacity and placement', () => {
		const a = aboveLayerOf(generated())!;
		expect(aboveKeyOf([a])).not.toBe(aboveKeyOf([{ ...a, opacity: 0.5 }]));
		expect(aboveKeyOf([a])).not.toBe(aboveKeyOf([{ ...a, rect: null }]));
	});
});

describe('the mask of a patch layer', () => {
	it('reads the stored mask picture and the placed rect', () => {
		expect(patchMaskOf(generated())).toEqual({ url: '/api/mask.png', rect: { x: 0.1, y: 0.2, width: 0.5, height: 0.25 } });
	});

	it('falls back to the rect for a patch made before masks were kept', () => {
		expect(patchMaskOf(generated({ area: { region: { item_id: 'r1' }, size: null } }))?.url).toBeNull();
	});

	it('has none for other steps or a run that has not finished', () => {
		expect(patchMaskOf(paint())).toBeNull();
		expect(patchMaskOf(generated({ run: { id: 'er_1', status: 'queued', error: null } }))).toBeNull();
	});
});

describe('mask outline pixels', () => {
	it('tints the inside faintly and the border strongly', () => {
		const size = 12;
		const pixels = new Uint8ClampedArray(size * size * 4);
		for (let y = 3; y < 9; y++) for (let x = 3; x < 9; x++) pixels.set([255, 255, 255, 255], (y * size + x) * 4);
		const out = maskOutline(pixels, size, size, [91, 157, 255]);
		const alpha = (x: number, y: number) => out[(y * size + x) * 4 + 3];
		expect(alpha(0, 0)).toBe(0);
		expect(alpha(5, 5)).toBe(31);
		expect(Array.from(out.slice((5 * size + 5) * 4, (5 * size + 5) * 4 + 3))).toEqual([91, 157, 255]);
		const edges = [3, 4, 5, 6, 7, 8].map((x) => alpha(x, 3));
		expect(Math.max(...edges)).toBe(255);
	});
});
