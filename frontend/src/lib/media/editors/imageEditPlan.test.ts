import { describe, it, expect } from 'vitest';
import {
	EMPTY_IMAGE_PLAN,
	planDisplaySize,
	planIsNoop,
	planOutputSize,
	planToOperations,
	type ImageEditPlan
} from './imageEditPlan';
import { describeRejection, MAX_EDIT_OPERATIONS } from './editOperations';

const SOURCE = { width: 1024, height: 1536 };

const plan = (overrides: Partial<ImageEditPlan> = {}): ImageEditPlan => ({
	...EMPTY_IMAGE_PLAN,
	...overrides
});

describe('planToOperations', () => {
	it('emits nothing for an untouched plan, which the server would refuse', () => {
		expect(planToOperations(EMPTY_IMAGE_PLAN, SOURCE)).toEqual([]);
	});

	it('applies the orientation BEFORE the crop, so the drawn rectangle lands where it was drawn', () => {
		const operations = planToOperations(
			plan({ rotation: 90, rect: { x: 0, y: 0, width: 0.5, height: 1 } }),
			SOURCE
		);
		expect(operations.map((operation) => operation.type)).toEqual(['rotate', 'crop']);
	});

	it('measures the crop against the ROTATED frame', () => {
		// A quarter turn of 1024×1536 displays as 1536×1024; half its width is
		// 768, not 512.
		const operations = planToOperations(
			plan({ rotation: 90, rect: { x: 0, y: 0, width: 0.5, height: 1 } }),
			SOURCE
		);
		expect(operations[1]).toEqual({ type: 'crop', x: 0, y: 0, width: 768, height: 1024 });
	});

	it('orders both flips after the rotation and before the crop', () => {
		const operations = planToOperations(
			plan({
				rotation: 180,
				flipHorizontal: true,
				flipVertical: true,
				rect: { x: 0.1, y: 0.1, width: 0.5, height: 0.5 }
			}),
			SOURCE
		);
		expect(operations.map((operation) => operation.type)).toEqual([
			'rotate',
			'flip',
			'flip',
			'crop'
		]);
		expect(operations[1]).toEqual({ type: 'flip', axis: 'horizontal' });
		expect(operations[2]).toEqual({ type: 'flip', axis: 'vertical' });
	});

	it('omits the crop when the rectangle is the whole frame', () => {
		const operations = planToOperations(plan({ flipHorizontal: true }), SOURCE);
		expect(operations).toEqual([{ type: 'flip', axis: 'horizontal' }]);
	});

	it('sends only the longest side of a resize, letting the API derive the other', () => {
		const operations = planToOperations(plan({ targetLongestSide: 768 }), SOURCE);
		expect(operations).toEqual([{ type: 'resize', height: 768 }]);
	});

	it('resizes on the width when the crop came out landscape', () => {
		const operations = planToOperations(
			plan({ rect: { x: 0, y: 0.4, width: 1, height: 0.2 }, targetLongestSide: 512 }),
			SOURCE
		);
		expect(operations[operations.length - 1]).toEqual({ type: 'resize', width: 512 });
	});

	it('drops a resize that asks for the size the crop already is', () => {
		const operations = planToOperations(plan({ targetLongestSide: SOURCE.height }), SOURCE);
		expect(operations).toEqual([]);
	});

	it('stays inside the operation cap even with everything switched on', () => {
		const operations = planToOperations(
			plan({
				rotation: 270,
				flipHorizontal: true,
				flipVertical: true,
				rect: { x: 0.1, y: 0.1, width: 0.6, height: 0.6 },
				targetLongestSide: 256
			}),
			SOURCE
		);
		expect(operations.length).toBeLessThanOrEqual(MAX_EDIT_OPERATIONS);
		expect(describeRejection(operations, 'image')).toBeNull();
	});
});

describe('planDisplaySize', () => {
	it('transposes for a quarter turn', () => {
		expect(planDisplaySize(plan({ rotation: 270 }), SOURCE)).toEqual({
			width: 1536,
			height: 1024
		});
	});
});

describe('planOutputSize', () => {
	it('is the source size for an untouched plan', () => {
		expect(planOutputSize(EMPTY_IMAGE_PLAN, SOURCE)).toEqual(SOURCE);
	});

	it('reports the cropped size', () => {
		expect(planOutputSize(plan({ rect: { x: 0, y: 0, width: 0.5, height: 0.5 } }), SOURCE)).toEqual({
			width: 512,
			height: 768
		});
	});

	it('keeps the aspect ratio through a resize', () => {
		const size = planOutputSize(plan({ targetLongestSide: 768 }), SOURCE);
		expect(size).toEqual({ width: 512, height: 768 });
	});

	it('agrees with what the operations will actually ask for', () => {
		const p = plan({ rotation: 90, rect: { x: 0.1, y: 0.2, width: 0.5, height: 0.5 } });
		const operations = planToOperations(p, SOURCE);
		const crop = operations.find((operation) => operation.type === 'crop');
		expect(planOutputSize(p, SOURCE)).toEqual({
			width: (crop as { width: number }).width,
			height: (crop as { height: number }).height
		});
	});
});

describe('planIsNoop', () => {
	it('is true only while nothing would change', () => {
		expect(planIsNoop(EMPTY_IMAGE_PLAN, SOURCE)).toBe(true);
		expect(planIsNoop(plan({ rotation: 90 }), SOURCE)).toBe(false);
		expect(planIsNoop(plan({ rect: { x: 0, y: 0, width: 0.9, height: 1 } }), SOURCE)).toBe(false);
	});
});

describe('describeRejection', () => {
	it('accepts a well-formed list', () => {
		expect(describeRejection([{ type: 'flip', axis: 'horizontal' }], 'image')).toBeNull();
	});

	it('refuses an empty list, as the server does', () => {
		expect(describeRejection([], 'image')).toBe('Nothing to apply yet.');
	});

	it('refuses an operation the medium has no meaning for', () => {
		expect(describeRejection([{ type: 'trim', start_seconds: 0, end_seconds: 1 }], 'image')).toBe(
			'An image cannot be trimmed.'
		);
		expect(describeRejection([{ type: 'flip', axis: 'vertical' }], 'audio')).toBe(
			'Audio cannot be flipped.'
		);
	});

	it('refuses two trims', () => {
		expect(
			describeRejection(
				[
					{ type: 'trim', start_seconds: 0, end_seconds: 1 },
					{ type: 'trim', start_seconds: 2, end_seconds: 3 }
				],
				'video'
			)
		).toBe('Only one trim can be applied at a time.');
	});

	it('refuses more operations than the server will take', () => {
		const many = Array.from({ length: MAX_EDIT_OPERATIONS + 1 }, () => ({
			type: 'flip' as const,
			axis: 'horizontal' as const
		}));
		expect(describeRejection(many, 'image')).toContain('at once');
	});
});
