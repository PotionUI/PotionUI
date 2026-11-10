import { describe, expect, it } from 'vitest';
import {
	drawRegion,
	moveRegion,
	moveRegionTo,
	nudgeRegion,
	parseCanvasSize,
	readRegionalValue,
	regionalFormDataFromResult,
	regionalSwitchCount,
	resizeRegion,
	resolveRegionalCaps,
	selectionAfterMove,
	selectionAfterRemove,
	type Region
} from './regions';

const r = (x: number, y: number, w: number, h: number, caption = ''): Region => ({ x, y, w, h, caption });

describe('canvas size', () => {
	it('parses WxH and falls back to 1024x1024', () => {
		expect(parseCanvasSize('1536x1024')).toEqual({ width: 1536, height: 1024 });
		expect(parseCanvasSize('768 × 1344')).toEqual({ width: 768, height: 1344 });
		expect(parseCanvasSize(undefined)).toEqual({ width: 1024, height: 1024 });
		expect(parseCanvasSize('auto')).toEqual({ width: 1024, height: 1024 });
	});
});

describe('region geometry', () => {
	it('clamps a move to the canvas', () => {
		const moved = moveRegion(r(0.5, 0.5, 0.3, 0.3), 0.9, -0.9);
		expect(moved.x).toBeCloseTo(0.7);
		expect(moved.y).toBe(0);
	});

	it('resizes from a corner and keeps the opposite corner fixed', () => {
		const resized = resizeRegion(r(0.2, 0.2, 0.3, 0.3), 'se', 0.1, 0.2);
		expect(resized.x).toBeCloseTo(0.2);
		expect(resized.w).toBeCloseTo(0.4);
		expect(resized.h).toBeCloseTo(0.5);
		const nw = resizeRegion(r(0.2, 0.2, 0.3, 0.3), 'nw', -0.1, -0.1);
		expect(nw.x + nw.w).toBeCloseTo(0.5);
		expect(nw.y + nw.h).toBeCloseTo(0.5);
	});

	it('never shrinks below the minimum or outside the canvas', () => {
		const tiny = resizeRegion(r(0.2, 0.2, 0.3, 0.3), 'se', -1, -1);
		expect(tiny.w).toBeGreaterThan(0);
		const big = resizeRegion(r(0.2, 0.2, 0.3, 0.3), 'se', 5, 5);
		expect(big.x + big.w).toBeLessThanOrEqual(1);
		expect(big.y + big.h).toBeLessThanOrEqual(1);
	});

	it('draws from any drag direction and rejects slivers', () => {
		const drawn = drawRegion({ x: 0.6, y: 0.7 }, { x: 0.2, y: 0.1 })!;
		expect(drawn).toMatchObject({ x: 0.2, y: 0.1, caption: '' });
		expect(drawn.w).toBeCloseTo(0.4);
		expect(drawRegion({ x: 0.2, y: 0.2 }, { x: 0.205, y: 0.5 })).toBeNull();
	});

	it('nudges by 1 percent and 5 percent with shift', () => {
		expect(nudgeRegion(r(0.5, 0.5, 0.2, 0.2), 'ArrowRight', false)!.x).toBeCloseTo(0.51);
		expect(nudgeRegion(r(0.5, 0.5, 0.2, 0.2), 'ArrowUp', true)!.y).toBeCloseTo(0.45);
		expect(nudgeRegion(r(0.5, 0.5, 0.2, 0.2), 'a', false)).toBeNull();
		expect(nudgeRegion(r(0, 0, 0.2, 0.2), 'ArrowLeft', false)!.x).toBe(0);
	});
});

describe('selection bookkeeping', () => {
	it('tracks selection through remove and reorder', () => {
		expect(selectionAfterRemove(3, 1, 1)).toBeNull();
		expect(selectionAfterRemove(3, 0, 2)).toBe(1);
		expect(selectionAfterRemove(3, 2, 0)).toBe(0);
		expect(selectionAfterMove(0, 0, 2)).toBe(2);
		expect(selectionAfterMove(1, 0, 2)).toBe(0);
		expect(selectionAfterMove(1, 2, 0)).toBe(2);
		expect(moveRegionTo([r(0, 0, 1, 1, 'a'), r(0, 0, 1, 1, 'b'), r(0, 0, 1, 1, 'c')], 0, 2).map((i) => i.caption)).toEqual(['b', 'c', 'a']);
	});
});

describe('form value', () => {
	it('reads nothing when the key is absent', () => {
		expect(readRegionalValue({})).toEqual({ enabled: false, items: [] });
		expect(readRegionalValue(undefined)).toEqual({ enabled: false, items: [] });
	});

	it('normalises stored items and counts only when on', () => {
		const value = readRegionalValue({
			regional_prompt: { enabled: true, items: [{ x: -1, y: 0.5, w: 2, h: 0.2, caption: 5 }, 'junk'] }
		});
		expect(value.items).toHaveLength(1);
		expect(value.items[0]).toMatchObject({ x: 0, w: 1, caption: '' });
		expect(regionalSwitchCount(value)).toBe(1);
		expect(regionalSwitchCount({ ...value, enabled: false })).toBeUndefined();
	});
});

describe('caps', () => {
	const vars = { regional_prompt: { modes: ['txt2img'], size_field: 'resolution' } };
	it('resolves only for a listed mode', () => {
		expect(resolveRegionalCaps(vars, 'txt2img')).toEqual({ sizeField: 'resolution' });
		expect(resolveRegionalCaps(vars, 'inpaint')).toBeNull();
		expect(resolveRegionalCaps({}, 'txt2img')).toBeNull();
		expect(resolveRegionalCaps(vars, null)).toBeNull();
	});
});

describe('chat tool result', () => {
	const caps = { sizeField: 'resolution' };
	const result = { enabled: true, items: [{ x: 0.1, y: 0.2, w: 0.5, h: 0.4, caption: 'a lighthouse' }] };

	it('writes the value next to the existing form data', () => {
		expect(regionalFormDataFromResult({ resolution: '1024x1024', steps: 20 }, caps, result)).toEqual({
			resolution: '1024x1024',
			steps: 20,
			regional_prompt: result
		});
	});

	it('does nothing when the preset or mode offers no option', () => {
		expect(regionalFormDataFromResult({ steps: 20 }, null, result)).toBeNull();
		expect(regionalFormDataFromResult({ steps: 20 }, caps, undefined)).toBeNull();
	});

	it('normalises bad boxes and treats a non-true enabled as off', () => {
		const out = regionalFormDataFromResult({}, caps, { enabled: 'yes', items: [{ x: 2, y: -1, w: 0.5, h: 0.5 }, 7] });
		expect(out!.regional_prompt).toMatchObject({ enabled: false });
		expect((out!.regional_prompt as { items: unknown[] }).items).toHaveLength(1);
	});
});
