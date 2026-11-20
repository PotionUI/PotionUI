import { describe, it, expect } from 'vitest';
import {
	CROP_ASPECTS,
	FULL_CROP,
	MIN_CROP_FRACTION,
	clampCropRect,
	cropOutputSize,
	displaySize,
	fitAspect,
	isFullFrame,
	mirrorCropRect,
	moveCropRect,
	normalisedRatio,
	resizeCropRect,
	rotateCropRect,
	toCropOperation,
	turn,
	type CropRect
} from './cropGeometry';

const rect = (x: number, y: number, width: number, height: number): CropRect => ({
	x,
	y,
	width,
	height
});

describe('clampCropRect', () => {
	it('pulls a rectangle that hangs off the frame back inside it', () => {
		expect(clampCropRect(rect(0.9, 0.9, 0.5, 0.5))).toEqual(rect(0.5, 0.5, 0.5, 0.5));
	});

	it('refuses a rectangle smaller than the minimum', () => {
		const clamped = clampCropRect(rect(0.5, 0.5, 0, 0));
		expect(clamped.width).toBe(MIN_CROP_FRACTION);
		expect(clamped.height).toBe(MIN_CROP_FRACTION);
	});

	it('caps a rectangle larger than the frame', () => {
		expect(clampCropRect(rect(-0.2, -0.2, 2, 2))).toEqual(FULL_CROP);
	});
});

describe('moveCropRect', () => {
	it('translates without resizing', () => {
		const moved = moveCropRect(rect(0.1, 0.1, 0.4, 0.4), 0.2, 0.1);
		expect(moved.x).toBeCloseTo(0.3);
		expect(moved.y).toBeCloseTo(0.2);
		expect(moved.width).toBeCloseTo(0.4);
		expect(moved.height).toBeCloseTo(0.4);
	});

	it('stops at the edge instead of shrinking', () => {
		const moved = moveCropRect(rect(0.5, 0.5, 0.4, 0.4), 0.5, 0.5);
		expect(moved).toEqual(rect(0.6, 0.6, 0.4, 0.4));
	});
});

describe('resizeCropRect', () => {
	const free = { ratio: null, stageAspect: 1 };

	it('grows from the south-east corner without moving the origin', () => {
		const next = resizeCropRect(rect(0.1, 0.1, 0.2, 0.2), 'se', 0.1, 0.1, free);
		expect(next.x).toBe(0.1);
		expect(next.y).toBe(0.1);
		expect(next.width).toBeCloseTo(0.3);
		expect(next.height).toBeCloseTo(0.3);
	});

	it('moves the origin when the north-west corner is dragged', () => {
		const next = resizeCropRect(rect(0.2, 0.2, 0.4, 0.4), 'nw', 0.1, 0.1, free);
		expect(next.x).toBeCloseTo(0.3);
		expect(next.y).toBeCloseTo(0.3);
		expect(next.width).toBeCloseTo(0.3);
		expect(next.height).toBeCloseTo(0.3);
	});

	it('anchors the opposite edge when a west drag runs past it', () => {
		// Dragging the west handle far to the right must not carry the east
		// edge along with it - the rectangle collapses against the anchor.
		const next = resizeCropRect(rect(0.1, 0.1, 0.3, 0.3), 'nw', 0.9, 0, free);
		expect(next.x + next.width).toBeCloseTo(0.4);
		expect(next.width).toBeCloseTo(MIN_CROP_FRACTION);
	});

	it('never leaves the frame when dragged outward', () => {
		const next = resizeCropRect(rect(0.5, 0.5, 0.4, 0.4), 'se', 5, 5, free);
		expect(next.x + next.width).toBeLessThanOrEqual(1);
		expect(next.y + next.height).toBeLessThanOrEqual(1);
	});

	it('derives the height from the width under a locked ratio', () => {
		// A square stage, so a 2:1 pixel ratio is a 2:1 normalised rectangle.
		const next = resizeCropRect(rect(0.1, 0.1, 0.2, 0.2), 'se', 0.2, 0, {
			ratio: 2,
			stageAspect: 1
		});
		expect(next.width).toBeCloseTo(0.4);
		expect(next.height).toBeCloseTo(0.2);
	});

	it('accounts for the stage aspect when locking the ratio', () => {
		// A 2:1 stage: a square pixel crop is a 0.5:1 normalised rectangle.
		const next = resizeCropRect(rect(0, 0, 0.2, 0.2), 'se', 0.2, 0, {
			ratio: 1,
			stageAspect: 2
		});
		expect(next.width).toBeCloseTo(0.4);
		expect(next.height).toBeCloseTo(0.8);
	});

	it('keeps the south edge still when the north handle drags under a lock', () => {
		const start = rect(0.1, 0.4, 0.4, 0.4);
		const next = resizeCropRect(start, 'nw', -0.2, 0, { ratio: 1, stageAspect: 1 });
		expect(next.y + next.height).toBeCloseTo(start.y + start.height);
	});
});

describe('fitAspect', () => {
	it('fills the frame for a ratio matching the stage', () => {
		expect(fitAspect(rect(0.2, 0.2, 0.3, 0.3), 2, 2)).toEqual(FULL_CROP);
	});

	it('produces a square crop of a wide image as a tall-ish normalised rect', () => {
		// 16:9 stage, 1:1 requested → normalised 0.5625 wide, full height.
		const fitted = fitAspect(rect(0.4, 0.4, 0.2, 0.2), 1, 16 / 9);
		expect(fitted.height).toBeCloseTo(1);
		expect(fitted.width).toBeCloseTo(9 / 16);
	});

	it('centres the new rectangle on the old one', () => {
		const fitted = fitAspect(rect(0.6, 0.4, 0.2, 0.2), 1, 2);
		expect(fitted.x + fitted.width / 2).toBeCloseTo(0.7);
	});

	it('slides a centred rectangle back inside rather than overhanging', () => {
		const fitted = fitAspect(rect(0.95, 0.5, 0.05, 0.05), 1, 2);
		expect(fitted.x + fitted.width).toBeLessThanOrEqual(1.0001);
	});

	it('leaves the rectangle alone when the stage aspect is unknown', () => {
		expect(fitAspect(rect(0.1, 0.1, 0.5, 0.5), 1, 0)).toEqual(rect(0.1, 0.1, 0.5, 0.5));
	});

	it('reproduces every declared preset as a real pixel ratio', () => {
		const source = { width: 1024, height: 1536 };
		const stageAspect = source.width / source.height;
		for (const aspect of CROP_ASPECTS) {
			if (!aspect.ratio) continue;
			const size = cropOutputSize(fitAspect(FULL_CROP, aspect.ratio, stageAspect), source);
			expect(size.width / size.height).toBeCloseTo(aspect.ratio, 1);
		}
	});
});

describe('normalisedRatio', () => {
	it('is the identity when the stage is square', () => {
		expect(normalisedRatio(1.5, 1)).toBe(1.5);
	});
});

describe('isFullFrame', () => {
	it('recognises an untouched rectangle', () => {
		expect(isFullFrame(FULL_CROP)).toBe(true);
	});

	it('rejects anything the user actually dragged', () => {
		expect(isFullFrame(rect(0, 0, 0.99, 1))).toBe(false);
	});
});

describe('toCropOperation', () => {
	it('converts a normalised rectangle to source pixels', () => {
		expect(toCropOperation(rect(0.25, 0.5, 0.5, 0.25), { width: 1024, height: 1536 })).toEqual({
			type: 'crop',
			x: 256,
			y: 768,
			width: 512,
			height: 384
		});
	});

	it('never lets x + width run past the source, which the server refuses', () => {
		const source = { width: 999, height: 501 };
		const operation = toCropOperation(rect(0.333333, 0.666666, 0.666667, 0.333334), source);
		expect(operation.x + operation.width).toBeLessThanOrEqual(source.width);
		expect(operation.y + operation.height).toBeLessThanOrEqual(source.height);
	});

	it('keeps at least one pixel of each side', () => {
		const operation = toCropOperation(rect(0.999, 0.999, 0.0001, 0.0001), { width: 10, height: 10 });
		expect(operation.width).toBeGreaterThanOrEqual(1);
		expect(operation.height).toBeGreaterThanOrEqual(1);
		expect(operation.x + operation.width).toBeLessThanOrEqual(10);
	});

	it('survives a degenerate source size', () => {
		const operation = toCropOperation(FULL_CROP, { width: 0, height: 0 });
		expect(operation).toEqual({ type: 'crop', x: 0, y: 0, width: 1, height: 1 });
	});
});

describe('cropOutputSize', () => {
	it('reports exactly what toCropOperation will ask for', () => {
		const source = { width: 1920, height: 1080 };
		const r = rect(0.1, 0.2, 0.37, 0.41);
		const operation = toCropOperation(r, source);
		expect(cropOutputSize(r, source)).toEqual({
			width: operation.width,
			height: operation.height
		});
	});
});

describe('displaySize', () => {
	it('transposes on a quarter turn and not on a half one', () => {
		const source = { width: 1024, height: 1536 };
		expect(displaySize(source, 0)).toEqual(source);
		expect(displaySize(source, 180)).toEqual(source);
		expect(displaySize(source, 90)).toEqual({ width: 1536, height: 1024 });
		expect(displaySize(source, 270)).toEqual({ width: 1536, height: 1024 });
	});
});

describe('turn', () => {
	it('wraps in both directions', () => {
		expect(turn(270, 1)).toBe(0);
		expect(turn(0, -1)).toBe(270);
		expect(turn(90, -2)).toBe(270);
	});
});

describe('rotateCropRect', () => {
	it('carries a corner selection round with the frame', () => {
		// Top-left quarter, turned clockwise, becomes the top-right quarter.
		const rotated = rotateCropRect(rect(0, 0, 0.5, 0.5), 1);
		expect(rotated.x).toBeCloseTo(0.5);
		expect(rotated.y).toBeCloseTo(0);
		expect(rotated.width).toBeCloseTo(0.5);
		expect(rotated.height).toBeCloseTo(0.5);
	});

	it('transposes a non-square selection', () => {
		const rotated = rotateCropRect(rect(0.1, 0.2, 0.6, 0.3), 1);
		expect(rotated.width).toBeCloseTo(0.3);
		expect(rotated.height).toBeCloseTo(0.6);
	});

	it('returns to where it started after four turns', () => {
		const start = rect(0.1, 0.2, 0.6, 0.3);
		const round = rotateCropRect(start, 4);
		expect(round.x).toBeCloseTo(start.x);
		expect(round.y).toBeCloseTo(start.y);
		expect(round.width).toBeCloseTo(start.width);
		expect(round.height).toBeCloseTo(start.height);
	});

	it('treats a counter-clockwise turn as three clockwise ones', () => {
		const start = rect(0.1, 0.2, 0.6, 0.3);
		expect(rotateCropRect(start, -1)).toEqual(rotateCropRect(start, 3));
	});

	it('never leaves the frame', () => {
		const rotated = rotateCropRect(rect(0.6, 0.7, 0.4, 0.3), 1);
		expect(rotated.x).toBeGreaterThanOrEqual(0);
		expect(rotated.x + rotated.width).toBeLessThanOrEqual(1.0001);
	});
});

describe('mirrorCropRect', () => {
	it('mirrors horizontally so the selection keeps its content', () => {
		const mirrored = mirrorCropRect(rect(0.1, 0.2, 0.3, 0.4), 'horizontal');
		expect(mirrored.x).toBeCloseTo(0.6);
		expect(mirrored.y).toBeCloseTo(0.2);
	});

	it('mirrors vertically', () => {
		const mirrored = mirrorCropRect(rect(0.1, 0.2, 0.3, 0.4), 'vertical');
		expect(mirrored.x).toBeCloseTo(0.1);
		expect(mirrored.y).toBeCloseTo(0.4);
	});

	it('is its own inverse', () => {
		const start = rect(0.1, 0.2, 0.3, 0.4);
		const round = mirrorCropRect(mirrorCropRect(start, 'horizontal'), 'horizontal');
		expect(round.x).toBeCloseTo(start.x);
		expect(round.width).toBeCloseTo(start.width);
	});

	it('leaves a full-frame selection full-frame', () => {
		expect(mirrorCropRect(FULL_CROP, 'horizontal')).toEqual(FULL_CROP);
	});
});
