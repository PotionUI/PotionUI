import { describe, expect, it } from 'vitest';
import { createFrameCache, settleWidth, tileLayout, tileTimes } from './stripTiles';

describe('tileLayout', () => {
	it('sizes tiles from the clip aspect at the strip height', () => {
		expect(tileLayout(1175, 48, 16 / 9)).toEqual({ tileWidth: 85, count: 14, pixelWidth: 85, pixelHeight: 48 });
	});

	it('covers the strip edge to edge with a cropped last tile', () => {
		const layout = tileLayout(1175, 48, 16 / 9)!;
		expect(layout.count * layout.tileWidth).toBeGreaterThanOrEqual(1175);
		expect((layout.count - 1) * layout.tileWidth).toBeLessThan(1175);
	});

	it('scales the pixel size by the device pixel ratio but not the tile width', () => {
		expect(tileLayout(1175, 48, 16 / 9, 2)).toEqual({ tileWidth: 85, count: 14, pixelWidth: 170, pixelHeight: 96 });
	});

	it('handles portrait clips and phone widths', () => {
		expect(tileLayout(360, 48, 9 / 16)).toEqual({ tileWidth: 27, count: 14, pixelWidth: 27, pixelHeight: 48 });
		expect(tileLayout(360, 48, 16 / 9)!.count).toBe(5);
	});

	it('returns null without a size and falls back on a bad aspect', () => {
		expect(tileLayout(0, 48, 1.7)).toBeNull();
		expect(tileLayout(300, 48, NaN)!.tileWidth).toBe(85);
	});
});

describe('tileTimes', () => {
	it('samples the visible centre of every tile', () => {
		const layout = tileLayout(200, 50, 2)!;
		expect(layout.tileWidth).toBe(100);
		expect(tileTimes(10, 200, layout)).toEqual([2.5, 7.5]);
		const cropped = tileLayout(150, 50, 2)!;
		expect(tileTimes(6, 150, cropped)).toEqual([2, 5]);
	});

	it('stays inside the clip', () => {
		const layout = tileLayout(100, 50, 2)!;
		expect(Math.max(...tileTimes(0.04, 100, layout))).toBeLessThanOrEqual(0.04);
	});
});

describe('settleWidth', () => {
	it('keeps the width until the change passes the threshold', () => {
		expect(settleWidth(1000, 1050)).toBe(1000);
		expect(settleWidth(1000, 1200)).toBe(1200);
		expect(settleWidth(0, 800)).toBe(800);
		expect(settleWidth(800, 0)).toBe(800);
	});
});

describe('frame cache', () => {
	const layout = tileLayout(400, 48, 16 / 9)!;

	it('returns stored frames for the same clip and size', () => {
		const cache = createFrameCache();
		const key = cache.key('a.mp4', layout, 5);
		cache.get(key, layout.count);
		cache.set(key, 1, 'frame');
		expect(cache.get(key, layout.count)[1]).toBe('frame');
	});

	it('keys per clip, width and duration', () => {
		const cache = createFrameCache();
		const key = cache.key('a.mp4', layout, 5);
		cache.get(key, layout.count);
		cache.set(key, 0, 'frame');
		expect(cache.get(cache.key('b.mp4', layout, 5), layout.count)[0]).toBeNull();
		expect(cache.get(cache.key('a.mp4', tileLayout(800, 48, 16 / 9)!, 5), 9)[0]).toBeNull();
		expect(cache.get(cache.key('a.mp4', layout, 6), layout.count)[0]).toBeNull();
	});

	it('evicts the oldest set past the limit', () => {
		const cache = createFrameCache(2);
		const a = cache.key('a', layout, 1);
		cache.get(a, 3);
		cache.set(a, 0, 'x');
		cache.get(cache.key('b', layout, 1), 3);
		cache.get(cache.key('c', layout, 1), 3);
		expect(cache.get(a, 3)[0]).toBeNull();
	});
});
