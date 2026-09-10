// The suite runs in the 'node' environment (no DOM), so we stub globalThis.window
// with just the bits these functions read (innerWidth, and innerHeight for the
// vertical-flip helpers).
import { afterEach, describe, expect, it } from 'vitest';
import {
	computeAnchoredMenuPosition,
	computeFlippedMenuPosition,
	MENU_EDGE_GUTTER,
	MENU_GAP,
	MENU_HEIGHT_ESTIMATE,
	MENU_MIN_HEIGHT
} from './menuPosition';

function fakeTrigger(rect: { top: number; bottom: number; left: number; right: number }): HTMLElement {
	return { getBoundingClientRect: () => rect } as unknown as HTMLElement;
}

function stubViewport(innerWidth: number, innerHeight?: number) {
	(globalThis as { window?: unknown }).window = { innerWidth, innerHeight };
}

describe('computeAnchoredMenuPosition', () => {
	afterEach(() => {
		delete (globalThis as { window?: unknown }).window;
	});

	it('anchors below the trigger, left-aligned by default', () => {
		stubViewport(1440);
		const trigger = fakeTrigger({ top: 100, bottom: 130, left: 200, right: 260 });

		const pos = computeAnchoredMenuPosition(trigger, { width: 180 });

		expect(pos.top).toBe(130 + MENU_GAP);
		expect(pos.left).toBe(200);
	});

	it('aligns the panel to the trigger right edge when align is "right"', () => {
		// The overflow menu button hangs its panel from its own right edge
		// (mirrors the pre-portal `right-0` CSS shorthand).
		stubViewport(1440);
		const trigger = fakeTrigger({ top: 100, bottom: 130, left: 1100, right: 1180 });

		const pos = computeAnchoredMenuPosition(trigger, { width: 180, align: 'right' });

		expect(pos.left).toBe(1180 - 180);
	});

	it('clamps the panel before it runs off the right edge of the viewport', () => {
		stubViewport(1440);
		const trigger = fakeTrigger({ top: 100, bottom: 130, left: 1350, right: 1390 });

		const pos = computeAnchoredMenuPosition(trigger, { width: 200 });

		expect(pos.left).toBe(1440 - 200 - MENU_EDGE_GUTTER);
	});

	it('never places the panel off the left edge', () => {
		stubViewport(1440);
		const trigger = fakeTrigger({ top: 100, bottom: 130, left: -50, right: 10 });

		const pos = computeAnchoredMenuPosition(trigger, { width: 180 });

		expect(pos.left).toBe(MENU_EDGE_GUTTER);
	});

	it('falls back to a 200px width estimate when none is given', () => {
		stubViewport(1440);
		const trigger = fakeTrigger({ top: 100, bottom: 130, left: 1350, right: 1390 });

		const pos = computeAnchoredMenuPosition(trigger);

		expect(pos.left).toBe(1440 - 200 - MENU_EDGE_GUTTER);
	});
});

describe('computeFlippedMenuPosition', () => {
	afterEach(() => {
		delete (globalThis as { window?: unknown }).window;
	});

	it('opens downward, left-aligned, when there is room below', () => {
		stubViewport(1440, 900);
		const trigger = fakeTrigger({ top: 100, bottom: 130, left: 200, right: 260 });

		const pos = computeFlippedMenuPosition(trigger, { width: 280 });

		expect(pos).toEqual({ left: 200, top: 130 + MENU_GAP, maxHeight: 900 - 130 - MENU_GAP - MENU_EDGE_GUTTER });
	});

	it('flips upward when there is not enough room below but more room above', () => {
		stubViewport(1440, 900);
		// 850..880: only 20px below (900-880), 850px above -- flips.
		const trigger = fakeTrigger({ top: 850, bottom: 880, left: 200, right: 260 });

		const pos = computeFlippedMenuPosition(trigger, { width: 280, heightEstimate: 300 });

		expect(pos.left).toBe(200);
		expect(pos.top).toBeUndefined();
		expect(pos.bottom).toBe(900 - 850 + MENU_GAP);
	});

	it('stays downward when space below is tight but space above is tighter still', () => {
		stubViewport(1440, 900);
		// spaceBelow = 900-800 = 100 (< the 200 heightEstimate, so the first
		// flip condition is true) but spaceAbove = 50 is NOT > spaceBelow, so
		// flipping would gain nothing -- this exercises that second guard
		// directly, independent of the heightEstimate check above.
		const trigger = fakeTrigger({ top: 50, bottom: 800, left: 200, right: 260 });

		const pos = computeFlippedMenuPosition(trigger, { width: 280, heightEstimate: 200 });

		expect(pos.top).toBe(800 + MENU_GAP);
		expect(pos.bottom).toBeUndefined();
	});

	it('clamps left before it runs off the right edge of the viewport', () => {
		stubViewport(1440, 900);
		const trigger = fakeTrigger({ top: 100, bottom: 130, left: 1350, right: 1390 });

		const pos = computeFlippedMenuPosition(trigger, { width: 200 });

		expect(pos.left).toBe(1440 - 200 - MENU_EDGE_GUTTER);
	});

	it('never places the panel off the left edge', () => {
		stubViewport(1440, 900);
		const trigger = fakeTrigger({ top: 100, bottom: 130, left: -50, right: 10 });

		const pos = computeFlippedMenuPosition(trigger, { width: 180 });

		expect(pos.left).toBe(MENU_EDGE_GUTTER);
	});

	it('falls back to default width/heightEstimate when none is given', () => {
		stubViewport(1440, 900);
		const trigger = fakeTrigger({ top: 100, bottom: 130, left: 1350, right: 1390 });

		const pos = computeFlippedMenuPosition(trigger);

		expect(pos.left).toBe(1440 - 200 - MENU_EDGE_GUTTER);
		expect(MENU_HEIGHT_ESTIMATE).toBeGreaterThan(0);
	});

	it('aligns the panel to the trigger right edge when align is "right"', () => {
		stubViewport(1440, 900);
		const trigger = fakeTrigger({ top: 100, bottom: 130, left: 1100, right: 1180 });

		const pos = computeFlippedMenuPosition(trigger, { width: 320, align: 'right' });

		expect(pos.left).toBe(1180 - 320);
	});

	it('stays on the preferred "up" side when it fits, even with room below too', () => {
		stubViewport(1440, 900);
		const trigger = fakeTrigger({ top: 500, bottom: 530, left: 200, right: 260 });

		const pos = computeFlippedMenuPosition(trigger, { width: 280, heightEstimate: 300, preferred: 'up' });

		expect(pos.top).toBeUndefined();
		expect(pos.bottom).toBe(900 - 500 + MENU_GAP);
	});

	it('flips a preferred "up" panel down when there is no room above but room below', () => {
		stubViewport(1440, 900);
		// 20..50: only 20px above, 850px below -- the preferred side doesn't fit
		// and the other side is bigger, so it flips down.
		const trigger = fakeTrigger({ top: 20, bottom: 50, left: 200, right: 260 });

		const pos = computeFlippedMenuPosition(trigger, { width: 280, heightEstimate: 300, preferred: 'up' });

		expect(pos.bottom).toBeUndefined();
		expect(pos.top).toBe(50 + MENU_GAP);
	});

	it('keeps a preferred "up" panel up when neither side fits but up still has more room', () => {
		stubViewport(1440, 900);
		// 600..630: 600px above, 270px below (900-630) -- neither fits the 1000
		// estimate, but up is still the bigger side, so it stays up.
		const trigger = fakeTrigger({ top: 600, bottom: 630, left: 200, right: 260 });

		const pos = computeFlippedMenuPosition(trigger, { width: 280, heightEstimate: 1000, preferred: 'up' });

		expect(pos.top).toBeUndefined();
		expect(pos.bottom).toBe(900 - 600 + MENU_GAP);
	});

	it('caps maxHeight to the room below when opening down', () => {
		stubViewport(1440, 900);
		const trigger = fakeTrigger({ top: 100, bottom: 200, left: 200, right: 260 });

		const pos = computeFlippedMenuPosition(trigger, { width: 280 });

		// spaceBelow = 900 - 200 = 700
		expect(pos.top).toBe(200 + MENU_GAP);
		expect(pos.maxHeight).toBe(700 - MENU_GAP - MENU_EDGE_GUTTER);
	});

	it('caps maxHeight to the room above when opening up', () => {
		stubViewport(1440, 900);
		// 850..880: only 20px below, 850px above -- flips up.
		const trigger = fakeTrigger({ top: 850, bottom: 880, left: 200, right: 260 });

		const pos = computeFlippedMenuPosition(trigger, { width: 280, heightEstimate: 300 });

		expect(pos.bottom).toBe(900 - 850 + MENU_GAP);
		expect(pos.maxHeight).toBe(850 - MENU_GAP - MENU_EDGE_GUTTER);
	});

	it('never returns a maxHeight below the MENU_MIN_HEIGHT floor even when the chosen side is cramped', () => {
		// A short viewport where neither side has real room: spaceAbove = 20,
		// spaceBelow = 10 -- "up" wins (bigger of the two), but 20 - gap -
		// edgeGutter would go negative without the floor.
		stubViewport(1440, 40);
		const trigger = fakeTrigger({ top: 20, bottom: 30, left: 200, right: 260 });

		const pos = computeFlippedMenuPosition(trigger, { width: 280, heightEstimate: 300, preferred: 'up' });

		expect(pos.top).toBeUndefined();
		expect(pos.bottom).toBe(40 - 20 + MENU_GAP);
		expect(pos.maxHeight).toBe(MENU_MIN_HEIGHT);
	});
});
