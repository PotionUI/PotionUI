import { describe, expect, it } from 'vitest';
import {
	MAX_SIDE,
	clampRectToBounds,
	fitInside,
	flipPosition,
	hitCorner,
	rotatePosition,
	scaleFromCorner,
	MIN_SIDE,
	clampSide,
	docToScreen,
	fitView,
	fitWithin,
	panBy,
	pixelRect,
	rectFromPoints,
	rectIntersect,
	rectUnion,
	screenToDoc,
	zoomAbout
} from './geometry';

describe('clampSide', () => {
	it('keeps sizes inside the 16 to 4096 range', () => {
		expect(clampSide(10)).toBe(MIN_SIDE);
		expect(clampSide(5000)).toBe(MAX_SIDE);
		expect(clampSide(512.4)).toBe(512);
	});

	it('falls back to the minimum for non-finite input', () => {
		expect(clampSide(Number.NaN)).toBe(MIN_SIDE);
		expect(clampSide(Number.POSITIVE_INFINITY)).toBe(MIN_SIDE);
	});
});

describe('fitWithin', () => {
	it('leaves small images untouched', () => {
		expect(fitWithin(800, 600)).toEqual({ width: 800, height: 600, scale: 1 });
	});

	it('scales the longest side down to the cap and keeps the aspect', () => {
		const fitted = fitWithin(8192, 4096);
		expect(fitted.width).toBe(4096);
		expect(fitted.height).toBe(2048);
		expect(fitted.scale).toBeCloseTo(0.5);
	});
});

describe('fitView', () => {
	it('centres the document in the stage', () => {
		const view = fitView({ width: 1000, height: 800 }, { width: 400, height: 200 }, 0, 10);
		expect(view.zoom).toBe(2.5);
		expect(view.panX).toBe(0);
		expect(view.panY).toBe(150);
	});

	it('never zooms in past the fit cap', () => {
		const view = fitView({ width: 2000, height: 2000 }, { width: 100, height: 100 });
		expect(view.zoom).toBe(2);
	});

	it('shrinks large documents to fit with padding', () => {
		const view = fitView({ width: 500, height: 500 }, { width: 2000, height: 1000 }, 25);
		expect(view.zoom).toBeCloseTo(0.225);
	});
});

describe('view transforms', () => {
	const view = { zoom: 2, panX: 10, panY: -20 };

	it('round-trips screen and document points', () => {
		const point = { x: 123, y: 45 };
		const back = screenToDoc(view, docToScreen(view, point));
		expect(back.x).toBeCloseTo(point.x);
		expect(back.y).toBeCloseTo(point.y);
	});

	it('keeps the anchor fixed while zooming', () => {
		const anchor = { x: 300, y: 200 };
		const before = screenToDoc(view, anchor);
		const next = zoomAbout(view, 1.5, anchor);
		const after = screenToDoc(next, anchor);
		expect(after.x).toBeCloseTo(before.x);
		expect(after.y).toBeCloseTo(before.y);
		expect(next.zoom).toBeCloseTo(3);
	});

	it('clamps zoom to the limits', () => {
		expect(zoomAbout(view, 100, { x: 0, y: 0 }).zoom).toBe(16);
		expect(zoomAbout(view, 0.0001, { x: 0, y: 0 }).zoom).toBe(0.05);
	});

	it('pans without touching zoom', () => {
		expect(panBy(view, 5, 5)).toEqual({ zoom: 2, panX: 15, panY: -15 });
	});
});

describe('rect helpers', () => {
	it('builds a padded rect from two points in any order', () => {
		expect(rectFromPoints({ x: 10, y: 20 }, { x: 4, y: 8 }, 2)).toEqual({
			x: 2,
			y: 6,
			width: 10,
			height: 16
		});
	});

	it('unions rects and treats null as empty', () => {
		const a = { x: 0, y: 0, width: 10, height: 10 };
		const b = { x: 20, y: 5, width: 5, height: 5 };
		expect(rectUnion(null, b)).toEqual(b);
		expect(rectUnion(a, b)).toEqual({ x: 0, y: 0, width: 25, height: 10 });
	});

	it('intersects and reports no overlap as null', () => {
		const a = { x: 0, y: 0, width: 10, height: 10 };
		expect(rectIntersect(a, { x: 5, y: 5, width: 10, height: 10 })).toEqual({
			x: 5,
			y: 5,
			width: 5,
			height: 5
		});
		expect(rectIntersect(a, { x: 10, y: 0, width: 5, height: 5 })).toBeNull();
	});

	it('snaps a fractional rect outward and clips it to the bounds', () => {
		expect(
			pixelRect({ x: -3.5, y: 1.2, width: 8, height: 2.1 }, { width: 100, height: 100 })
		).toEqual({
			x: 0,
			y: 1,
			width: 5,
			height: 3
		});
		expect(
			pixelRect({ x: 200, y: 0, width: 5, height: 5 }, { width: 100, height: 100 })
		).toBeNull();
	});
});

describe('document transforms', () => {
	const doc = { width: 100, height: 60 };
	const layer = { x: 10, y: 5, width: 30, height: 20 };

	it('mirrors a layer position across the document', () => {
		expect(flipPosition('horizontal', doc, layer)).toEqual({ x: 60, y: 5 });
		expect(flipPosition('vertical', doc, layer)).toEqual({ x: 10, y: 35 });
	});

	it('flipping twice returns to the start', () => {
		const once = flipPosition('horizontal', doc, layer);
		expect(flipPosition('horizontal', doc, { ...layer, ...once })).toEqual({ x: 10, y: 5 });
	});

	it('moves a layer to its rotated position and back', () => {
		const cw = rotatePosition('cw', doc, layer);
		expect(cw).toEqual({ x: 35, y: 10 });
		const rotatedDoc = { width: doc.height, height: doc.width };
		const back = rotatePosition('ccw', rotatedDoc, {
			x: cw.x,
			y: cw.y,
			width: layer.height,
			height: layer.width
		});
		expect(back).toEqual({ x: 10, y: 5 });
	});
});

describe('clampRectToBounds', () => {
	it('clips and rounds a rect to the document', () => {
		expect(
			clampRectToBounds({ x: -5.4, y: 2.6, width: 20, height: 100 }, { width: 50, height: 40 })
		).toEqual({
			x: 0,
			y: 3,
			width: 15,
			height: 37
		});
	});

	it('returns null when nothing is left', () => {
		expect(
			clampRectToBounds({ x: 60, y: 0, width: 5, height: 5 }, { width: 50, height: 40 })
		).toBeNull();
	});
});

describe('scaleFromCorner', () => {
	const base = { x: 100, y: 100, width: 200, height: 100 };

	it('keeps the opposite corner fixed and the aspect ratio', () => {
		const scaled = scaleFromCorner(base, 'br', { x: 500, y: 130 });
		expect(scaled).toEqual({ x: 100, y: 100, width: 400, height: 200 });
	});

	it('scales from the top-left toward the anchor at the bottom-right', () => {
		const scaled = scaleFromCorner(base, 'tl', { x: 200, y: 0 });
		expect(scaled.x + scaled.width).toBe(300);
		expect(scaled.y + scaled.height).toBe(200);
		expect(scaled.width).toBe(100);
		expect(scaled.height).toBe(50);
	});

	it('never collapses below the minimum side', () => {
		expect(scaleFromCorner(base, 'br', { x: 100, y: 100 }).width).toBe(8);
	});
});

describe('fitInside', () => {
	it('shrinks big content to a fraction of the frame and keeps the aspect', () => {
		const fitted = fitInside({ width: 2000, height: 1000 }, { width: 1000, height: 1000 });
		expect(fitted).toEqual({ width: 600, height: 300 });
	});

	it('never scales small content up', () => {
		expect(fitInside({ width: 50, height: 40 }, { width: 1000, height: 1000 })).toEqual({
			width: 50,
			height: 40
		});
	});
});

describe('hitCorner', () => {
	const rect = { x: 10, y: 10, width: 100, height: 50 };

	it('finds the corner under the point within the radius', () => {
		expect(hitCorner(rect, { x: 112, y: 62 }, 5)).toBe('br');
		expect(hitCorner(rect, { x: 8, y: 9 }, 5)).toBe('tl');
	});

	it('returns null away from every corner', () => {
		expect(hitCorner(rect, { x: 60, y: 35 }, 5)).toBeNull();
	});
});
