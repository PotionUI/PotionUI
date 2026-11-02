import { describe, expect, it } from 'vitest';
import {
	actualSize,
	fitViewport,
	isActualSize,
	panView,
	pinchStep,
	resolveView,
	wheelFactor,
	zoomAt,
	zoomCentered,
	zoomPercent
} from './viewport';

const stage = { width: 1000, height: 800 };
const media = { width: 1024, height: 1024 };

describe('fit', () => {
	it('fits and centres the media inside the padded stage', () => {
		const view = resolveView(fitViewport(), stage, media, 60);
		expect(view.zoom).toBeCloseTo(680 / 1024);
		expect(view.panX).toBeCloseTo((1000 - 680) / 2);
		expect(view.panY).toBeCloseTo(60);
	});

	it('never upscales a small picture past 100% to fit', () => {
		const view = resolveView(fitViewport(), stage, { width: 100, height: 50 }, 60);
		expect(view.zoom).toBe(1);
		expect(view.panX).toBe(450);
	});

	it('keeps a manual view as it is', () => {
		const manual = { fit: false, zoom: 2, panX: -40, panY: 12 };
		expect(resolveView(manual, stage, media, 60)).toEqual({ zoom: 2, panX: -40, panY: 12 });
	});

	it('keeps the stored view while the media size is unknown', () => {
		expect(resolveView(fitViewport(), stage, { width: 0, height: 0 }, 60)).toEqual({ zoom: 1, panX: 0, panY: 0 });
	});
});

describe('zoom', () => {
	it('zooms about an anchor so the point under it stays put', () => {
		const view = { zoom: 1, panX: 100, panY: 50 };
		const next = zoomAt(view, 2, { x: 300, y: 250 });
		expect(next).toEqual({ fit: false, zoom: 2, panX: -100, panY: -150 });
	});

	it('clamps to the zoom range', () => {
		expect(zoomAt({ zoom: 10, panX: 0, panY: 0 }, 100, { x: 0, y: 0 }).zoom).toBe(16);
		expect(zoomAt({ zoom: 0.1, panX: 0, panY: 0 }, 0.001, { x: 0, y: 0 }).zoom).toBe(0.05);
	});

	it('zooms the buttons about the stage centre', () => {
		const next = zoomCentered({ zoom: 1, panX: 0, panY: 0 }, 2, stage);
		expect(next).toMatchObject({ zoom: 2, panX: -500, panY: -400 });
	});

	it('shows 1:1 as actual pixels, centred', () => {
		const view = actualSize(stage, media);
		expect(view).toEqual({ fit: false, zoom: 1, panX: -12, panY: -112 });
		expect(isActualSize(view)).toBe(true);
		expect(isActualSize(fitViewport())).toBe(false);
	});

	it('reports a rounded percentage', () => {
		expect(zoomPercent({ zoom: 0.6234, panX: 0, panY: 0 })).toBe(62);
	});

	it('turns wheel deltas into gentle factors, faster for a pinch', () => {
		expect(wheelFactor(-100, 0, false)).toBeGreaterThan(1);
		expect(wheelFactor(100, 0, false)).toBeLessThan(1);
		expect(wheelFactor(0, 0, false)).toBe(1);
		expect(wheelFactor(10, 0, true)).toBeLessThan(wheelFactor(10, 0, false));
		expect(wheelFactor(3, 1, false)).toBeCloseTo(wheelFactor(48, 0, false));
		expect(wheelFactor(1e6, 0, false)).toBeCloseTo(Math.exp(-0.6));
	});
});

describe('pan and pinch', () => {
	it('pans by a delta and leaves fit mode', () => {
		expect(panView({ zoom: 1.5, panX: 10, panY: 20 }, 5, -7)).toEqual({ fit: false, zoom: 1.5, panX: 15, panY: 13 });
	});

	it('pinch zooms by the finger distance ratio about the midpoint', () => {
		const view = { zoom: 1, panX: 0, panY: 0 };
		const next = pinchStep(view, [{ x: 100, y: 100 }, { x: 200, y: 100 }], [{ x: 50, y: 100 }, { x: 250, y: 100 }]);
		expect(next.zoom).toBeCloseTo(2);
		expect(next.panX).toBeCloseTo(-150);
		expect(next.panY).toBeCloseTo(-100);
	});

	it('two fingers moving together pan without zooming', () => {
		const view = { zoom: 1, panX: 0, panY: 0 };
		const next = pinchStep(view, [{ x: 0, y: 0 }, { x: 100, y: 0 }], [{ x: 30, y: 40 }, { x: 130, y: 40 }]);
		expect(next).toEqual({ fit: false, zoom: 1, panX: 30, panY: 40 });
	});

	it('ignores a degenerate pinch', () => {
		const view = { zoom: 1, panX: 0, panY: 0 };
		const same = { x: 10, y: 10 };
		expect(pinchStep(view, [same, same], [same, { x: 20, y: 10 }]).zoom).toBe(1);
	});
});
