import { describe, expect, it } from 'vitest';
import { EMPTY_IMAGE_PLAN } from '$lib/media/editors/imageEditPlan';
import { rectInPixels, withAspect, withOrientation, withRectField } from './cropActions';
import type { CropParams } from './steps';

const frame = { width: 1000, height: 500 };
const base: CropParams = { aspect: 'free', plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0.1, y: 0.2, width: 0.5, height: 0.4 } } };

describe('crop rectangle in pixels', () => {
	it('reads the rectangle against the displayed frame', () => {
		expect(rectInPixels(base, frame)).toEqual({ x: 100, y: 100, width: 500, height: 200 });
	});

	it('writes one field and frees the aspect', () => {
		const next = withRectField({ ...base, aspect: '4:3' }, frame, 'width', 600);
		expect(next.aspect).toBe('free');
		expect(next.plan.rect).toEqual({ x: 0.1, y: 0.2, width: 0.6, height: 0.4 });
	});

	it('keeps the rectangle inside the frame', () => {
		expect(withRectField(base, frame, 'x', 900).plan.rect.x).toBeCloseTo(0.5);
		expect(withRectField(base, frame, 'width', 5000).plan.rect.width).toBeCloseTo(0.9);
		expect(withRectField(base, frame, 'y', -50).plan.rect.y).toBe(0);
		expect(withRectField(base, frame, 'height', 5000).plan.rect.height).toBeCloseTo(0.8);
	});

	it('ignores a value that is not a number', () => {
		expect(withRectField(base, frame, 'x', Number.NaN)).toBe(base);
		expect(withRectField(base, { width: 0, height: 0 }, 'x', 5)).toBe(base);
	});
});

describe('crop aspect and orientation', () => {
	it('fits a ratio around the current centre', () => {
		const next = withAspect(base, '1:1', 1, frame);
		expect(next.aspect).toBe('1:1');
		const { rect } = next.plan;
		expect((rect.width * frame.width) / (rect.height * frame.height)).toBeCloseTo(1);
	});

	it('leaves the rectangle alone for a free aspect', () => {
		expect(withAspect(base, 'free', null, frame).plan.rect).toEqual(base.plan.rect);
	});

	it('turns a quarter and toggles a flip', () => {
		expect(withOrientation(base, 'rotate-right').plan.rotation).toBe(90);
		expect(withOrientation(base, 'rotate-left').plan.rotation).toBe(270);
		expect(withOrientation(base, 'flip-h').plan.flipHorizontal).toBe(true);
		expect(withOrientation(withOrientation(base, 'flip-v'), 'flip-v').plan.flipVertical).toBe(false);
	});
});
