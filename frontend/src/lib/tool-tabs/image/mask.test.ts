import { describe, expect, it } from 'vitest';
import { EMPTY_IMAGE_PLAN } from '$lib/media/imageEditPlan';
import { apply, appliedSteps, beginEdit, createStack, reopen, toggleHidden, undo, updatePending } from '$lib/tool-tabs/frame/editStack';
import { thresholdMaskPixels } from '$lib/media/editors/maskThreshold';
import { alphaToMask, geometryKey, liveMask, maskIsStale, maskOverlay, MASK_TOOLS, parseRgb, readMask, sendPatch, STORED_MASK_CUTOFF } from './mask';
import { cropDraft, lookDraft, paintDraft } from './steps';

function cropped() {
	return apply(
		beginEdit(
			createStack(),
			cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0, y: 0, width: 0.5, height: 0.5 } }, aspect: 'free' }, 'Free', { width: 512, height: 512 })
		)
	);
}

describe('mask channel', () => {
	it('is not a step and survives edits that leave the geometry alone', () => {
		const stack = cropped();
		const mask = { path: 'uploads/mask-1.png', url: '/m.png', geometry: geometryKey(appliedSteps(stack)) };
		const looked = apply(beginEdit(stack, lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 60 })));
		const painted = apply(beginEdit(looked, paintDraft({ layer: 'a', url: '/a.png', layers: 1 }, { width: 512, height: 512 })));
		expect(painted.steps.some((step) => step.tool === 'mask')).toBe(false);
		expect(liveMask(mask, appliedSteps(painted))).toBe(mask);
		expect(maskIsStale(mask, appliedSteps(painted))).toBe(false);
	});

	it('goes stale when a geometry step changes, is hidden or is undone', () => {
		const stack = cropped();
		const mask = { path: 'uploads/mask-1.png', url: '/m.png', geometry: geometryKey(appliedSteps(stack)) };
		expect(liveMask(mask, appliedSteps(toggleHidden(stack, stack.steps[0].id)))).toBeNull();
		expect(liveMask(mask, appliedSteps(undo(stack)))).toBeNull();
		const moved = apply(
			updatePending(reopen(stack, stack.steps[0].id), {
				params: { plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0.25, y: 0, width: 0.5, height: 0.5 } }, aspect: 'free' }
			})
		);
		expect(liveMask(mask, appliedSteps(moved))).toBeNull();
		expect(liveMask(mask, [])).toBeNull();
		expect(maskIsStale(mask, [])).toBe(true);
		expect(maskIsStale(null, [])).toBe(false);
	});

	it('rides along with Send as the field mask sibling only when the field takes one', () => {
		const mask = { path: 'uploads/mask-1.png', url: '/m.png', geometry: '[]' };
		const item = { path: 'uploads/edit.png', relative_path: 'uploads/edit.png' };
		expect(sendPatch('image', item, mask, true)).toEqual({
			image: item,
			image_inpaint_mask: 'uploads/mask-1.png',
			image__origin: undefined
		});
		expect(sendPatch('image', item, mask, false).image_inpaint_mask).toBeUndefined();
		expect(sendPatch('image', item, null, true).image_inpaint_mask).toBeUndefined();
	});

	it('tints lit mask pixels opaque and leaves the rest transparent', () => {
		const source = new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255, 129, 129, 129, 255, 128, 128, 128, 255]);
		const { data, lit } = maskOverlay(source, [10, 20, 30]);
		expect(lit).toBe(2);
		expect(Array.from(data)).toEqual([10, 20, 30, 255, 0, 0, 0, 0, 10, 20, 30, 255, 0, 0, 0, 0]);
		expect(parseRgb(' 77 154 255')).toEqual([77, 154, 255]);
		expect(parseRgb('77, 154, 255')).toEqual([77, 154, 255]);
		expect(parseRgb('', [1, 2, 3])).toEqual([1, 2, 3]);
	});

	it('reads back only a complete persisted mask', () => {
		expect(readMask({ path: 'p', url: 'u', geometry: '[]' })).toEqual({ path: 'p', url: 'u', geometry: '[]' });
		expect(readMask({ path: '', url: 'u', geometry: '[]' })).toBeNull();
		expect(readMask(null)).toBeNull();
	});
});

describe('stored mask encoding', () => {
	it('matches what the modal editor wrote for every stroke alpha', () => {
		for (let alpha = 0; alpha < 256; alpha += 1) {
			const legacy = new Uint8ClampedArray([alpha, alpha, alpha, 255]);
			thresholdMaskPixels(legacy);
			const painted = new Uint8ClampedArray([77, 154, 255, alpha]);
			const out = new Uint8ClampedArray(4);
			alphaToMask(painted, out, STORED_MASK_CUTOFF);
			expect(Array.from(out), `alpha ${alpha}`).toEqual(Array.from(legacy));
		}
	});

	it('is white on opaque black and counts the lit pixels', () => {
		const out = new Uint8ClampedArray(8);
		const lit = alphaToMask(new Uint8ClampedArray([1, 2, 3, 255, 1, 2, 3, 0]), out);
		expect(lit).toBe(1);
		expect(Array.from(out)).toEqual([255, 255, 255, 255, 0, 0, 0, 255]);
	});

	it('keeps the tab default cutoff and offers paint then erase', () => {
		const out = new Uint8ClampedArray(4);
		alphaToMask(new Uint8ClampedArray([0, 0, 0, 65]), out);
		expect(out[0]).toBe(255);
		expect(MASK_TOOLS.map((tool) => [tool.id, tool.erase])).toEqual([['paint', false], ['erase', true]]);
	});
});
