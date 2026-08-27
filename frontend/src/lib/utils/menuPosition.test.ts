// The suite runs in the 'node' environment (no DOM), so we stub globalThis.window
// with just the bit computeAnchoredMenuPosition reads (innerWidth).
import { afterEach, describe, expect, it } from 'vitest';
import { computeAnchoredMenuPosition, MENU_EDGE_GUTTER, MENU_GAP } from './menuPosition';

function fakeTrigger(rect: { top: number; bottom: number; left: number; right: number }): HTMLElement {
	return { getBoundingClientRect: () => rect } as unknown as HTMLElement;
}

function stubViewport(innerWidth: number) {
	(globalThis as { window?: unknown }).window = { innerWidth };
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
