import { describe, it, expect } from 'vitest';
import {
	CELL_MAX,
	CELL_MIN,
	ZOOM_MAX,
	ZOOM_MIN,
	clampCellDimension,
	clampMargin,
	clampTransform,
	clampZoom,
	computeDrawRect,
	fitWithMargin,
	isInsideSafeArea,
	maxMargin,
	panLimit,
	safeArea,
	viewportScale,
	zoomStep
} from './cropGeometry';

describe('clampCellDimension', () => {
	it('rounds and clamps to the supported cell range', () => {
		expect(clampCellDimension(512)).toBe(512);
		expect(clampCellDimension(511.6)).toBe(512);
		expect(clampCellDimension(0)).toBe(CELL_MIN);
		expect(clampCellDimension(99999)).toBe(CELL_MAX);
	});

	it('falls back when the input is not a number', () => {
		expect(clampCellDimension(Number.NaN)).toBe(512);
		expect(clampCellDimension(Number.NaN, 256)).toBe(256);
	});
});

describe('margin', () => {
	it('caps at a quarter of the shorter cell side', () => {
		expect(maxMargin({ width: 512, height: 512 })).toBe(128);
		expect(maxMargin({ width: 1024, height: 256 })).toBe(64);
	});

	it('clamps out-of-range values instead of inverting the safe area', () => {
		const cell = { width: 512, height: 512 };
		expect(clampMargin(500, cell)).toBe(128);
		expect(clampMargin(-5, cell)).toBe(0);
		expect(clampMargin(32.4, cell)).toBe(32);
	});

	it('insets the safe area on all four sides', () => {
		expect(safeArea({ width: 512, height: 512 }, 32)).toEqual({
			x: 32,
			y: 32,
			width: 448,
			height: 448
		});
	});

	it('uses the clamped margin when building the safe area', () => {
		// 400 would invert a 512-cell; the cap (128) keeps it a real rectangle.
		expect(safeArea({ width: 512, height: 512 }, 400)).toEqual({
			x: 128,
			y: 128,
			width: 256,
			height: 256
		});
	});
});

describe('fitWithMargin', () => {
	it('contains a landscape image inside the safe area', () => {
		// safe area 448x448; contain = min(448/800, 448/400) = 0.56.
		// `cover` would be max(...) = 1.12 and push the width outside the cell.
		const t = fitWithMargin({ width: 800, height: 400 }, { width: 512, height: 512 }, 32);
		expect(t.zoom).toBeCloseTo(0.56, 12);
		expect(t.offsetX).toBe(0);
		expect(t.offsetY).toBe(0);

		const rect = computeDrawRect({ width: 800, height: 400 }, { width: 512, height: 512 }, t);
		expect(rect.x).toBeCloseTo(32, 9);
		expect(rect.y).toBeCloseTo(144, 9);
		expect(rect.width).toBeCloseTo(448, 9);
		expect(rect.height).toBeCloseTo(224, 9);
	});

	it('contains a portrait image inside the safe area', () => {
		const t = fitWithMargin({ width: 400, height: 800 }, { width: 512, height: 512 }, 32);
		expect(t.zoom).toBeCloseTo(0.56, 12);

		const rect = computeDrawRect({ width: 400, height: 800 }, { width: 512, height: 512 }, t);
		expect(rect.x).toBeCloseTo(144, 12);
		expect(rect.y).toBeCloseTo(32, 12);
		expect(rect.width).toBeCloseTo(224, 12);
		expect(rect.height).toBeCloseTo(448, 12);
	});

	it('shrinks an oversized image', () => {
		const t = fitWithMargin({ width: 4000, height: 4000 }, { width: 256, height: 256 }, 0);
		expect(t.zoom).toBeCloseTo(0.064, 12);
	});

	it('enlarges an undersized image up to the safe area', () => {
		// safe area 384x384; contain = min(384/100, 384/50) = 3.84.
		const t = fitWithMargin({ width: 100, height: 50 }, { width: 512, height: 512 }, 64);
		expect(t.zoom).toBeCloseTo(3.84, 12);
	});

	it('respects a non-square cell', () => {
		// safe area 1024x448; contain = min(1024/512, 448/512) = 0.875.
		const t = fitWithMargin({ width: 512, height: 512 }, { width: 1024, height: 512 }, 32);
		expect(t.zoom).toBeCloseTo(0.875, 12);
	});

	// Bite-check: this is the assertion that fails outright if `fitWithMargin`
	// ever switches from contain (min) to cover (max) - every non-square aspect
	// then overflows the safe area on its long axis.
	it('leaves the whole image inside the safe area for every aspect ratio', () => {
		const cell = { width: 512, height: 384 };
		const margin = 24;
		const safe = safeArea(cell, margin);
		const images = [
			{ width: 800, height: 400 },
			{ width: 400, height: 800 },
			{ width: 4000, height: 4000 },
			{ width: 100, height: 50 },
			{ width: 512, height: 384 },
			{ width: 1920, height: 1080 },
			{ width: 37, height: 1301 }
		];
		for (const image of images) {
			const rect = computeDrawRect(image, cell, fitWithMargin(image, cell, margin));
			expect(isInsideSafeArea(rect, safe)).toBe(true);
		}
	});

	it('degrades to identity for a degenerate image', () => {
		expect(fitWithMargin({ width: 0, height: 0 }, { width: 512, height: 512 }, 0)).toEqual({
			zoom: 1,
			offsetX: 0,
			offsetY: 0
		});
	});
});

describe('zoom', () => {
	it('clamps to the supported range', () => {
		expect(clampZoom(1)).toBe(1);
		expect(clampZoom(0)).toBe(ZOOM_MIN);
		expect(clampZoom(1000)).toBe(ZOOM_MAX);
		expect(clampZoom(Number.NaN)).toBe(ZOOM_MIN);
	});

	it('steps multiplicatively in both directions', () => {
		expect(zoomStep(1, 1)).toBeCloseTo(1.1, 12);
		expect(zoomStep(1, -1)).toBeCloseTo(1 / 1.1, 12);
		expect(zoomStep(1, 0)).toBe(1);
		expect(zoomStep(ZOOM_MAX, 1)).toBe(ZOOM_MAX);
	});
});

describe('panLimit', () => {
	it('keeps an oversized image covering the cell', () => {
		// 800 wide in a 512 cell: 144 either way before a gap appears.
		expect(panLimit(800, 512)).toBe(144);
	});

	it('keeps an undersized image inside the cell', () => {
		// 224 wide in a 512 cell: 144 either way before it leaves.
		expect(panLimit(224, 512)).toBe(144);
	});

	it('pins an exactly-fitting image', () => {
		expect(panLimit(512, 512)).toBe(0);
	});
});

describe('clampTransform', () => {
	const cell = { width: 512, height: 512 };

	it('clamps pan on both axes independently', () => {
		const image = { width: 1024, height: 256 };
		// drawn 1024x256 at zoom 1 -> limits 256 (x) and 128 (y).
		const t = clampTransform({ zoom: 1, offsetX: 900, offsetY: -900 }, image, cell);
		expect(t).toEqual({ zoom: 1, offsetX: 256, offsetY: -128 });
	});

	it('leaves an in-range transform untouched', () => {
		const image = { width: 1024, height: 1024 };
		expect(clampTransform({ zoom: 1, offsetX: 100, offsetY: -50 }, image, cell)).toEqual({
			zoom: 1,
			offsetX: 100,
			offsetY: -50
		});
	});

	it('clamps zoom before deriving the pan limits', () => {
		const image = { width: 1024, height: 1024 };
		const t = clampTransform({ zoom: 1e9, offsetX: 1e9, offsetY: 0 }, image, cell);
		expect(t.zoom).toBe(ZOOM_MAX);
		expect(t.offsetX).toBe((1024 * ZOOM_MAX - 512) / 2);
	});

	it('recentres when the image is degenerate', () => {
		expect(clampTransform({ zoom: 2, offsetX: 40, offsetY: 40 }, { width: 0, height: 0 }, cell)).toEqual(
			{ zoom: 2, offsetX: 0, offsetY: 0 }
		);
	});
});

describe('computeDrawRect', () => {
	it('centres at zero offset', () => {
		expect(
			computeDrawRect({ width: 200, height: 100 }, { width: 512, height: 512 }, {
				zoom: 1,
				offsetX: 0,
				offsetY: 0
			})
		).toEqual({ x: 156, y: 206, width: 200, height: 100 });
	});

	it('translates by the offset in cell pixels', () => {
		expect(
			computeDrawRect({ width: 200, height: 100 }, { width: 512, height: 512 }, {
				zoom: 2,
				offsetX: -30,
				offsetY: 12
			})
		).toEqual({ x: 26, y: 168, width: 400, height: 200 });
	});
});

describe('viewportScale', () => {
	it('shrinks a cell larger than the viewport budget', () => {
		expect(viewportScale({ width: 1024, height: 512 }, 460)).toBeCloseTo(460 / 1024, 12);
	});

	it('never magnifies past 1:1', () => {
		expect(viewportScale({ width: 128, height: 128 }, 460)).toBe(1);
	});
});
