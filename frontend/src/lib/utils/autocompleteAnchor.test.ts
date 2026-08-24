import { describe, expect, it } from 'vitest';
import {
	AUTOCOMPLETE_ESTIMATED_HEIGHT,
	AUTOCOMPLETE_MIN_WIDTH,
	computeAutocompletePlacement
} from './autocompleteAnchor';

const VIEWPORT = { width: 1440, height: 900 };

describe('computeAutocompletePlacement', () => {
	it('opens directly below a short anchor with room under it', () => {
		const placement = computeAutocompletePlacement(
			{ top: 100, bottom: 130, left: 200, width: 600 },
			VIEWPORT
		);

		expect(placement.openAbove).toBe(false);
		expect(placement.top).toBe(130);
		expect(placement.left).toBe(200);
		expect(placement.width).toBe(600);
	});

	it('flips above an anchor pinned to the bottom of the viewport', () => {
		// The chat composer sits here; a below-anchored list would render off-screen.
		const placement = computeAutocompletePlacement(
			{ top: 840, bottom: 880, left: 200, width: 600 },
			VIEWPORT
		);

		expect(placement.openAbove).toBe(true);
		expect(placement.bottom).toBe(VIEWPORT.height - 840);
	});

	it('widens a hairline inline anchor to the minimum list width', () => {
		const placement = computeAutocompletePlacement(
			{ top: 100, bottom: 120, left: 200, width: 4 },
			VIEWPORT
		);
		expect(placement.width).toBe(AUTOCOMPLETE_MIN_WIDTH);
	});

	it('keeps a list anchored near the right edge on screen', () => {
		const placement = computeAutocompletePlacement(
			{ top: 100, bottom: 120, left: 1400, width: 20 },
			VIEWPORT
		);
		expect(placement.left).toBe(VIEWPORT.width - AUTOCOMPLETE_MIN_WIDTH - 8);
	});

	it('never places the list off the left edge', () => {
		const placement = computeAutocompletePlacement(
			{ top: 100, bottom: 120, left: -240, width: 600 },
			VIEWPORT
		);
		expect(placement.left).toBeGreaterThanOrEqual(0);
	});

	describe('a tall multi-line segment card starting near the top of the viewport', () => {
		// The case a taller composer creates: too little room below to fit the
		// list, but the anchor's own top is not above that gap either, so the
		// flip rule declines and the list used to run off the bottom edge.
		const TALL = { top: 50, bottom: 700, left: 200, width: 600 };

		it('does not flip above — its top edge has even less room', () => {
			const placement = computeAutocompletePlacement(TALL, VIEWPORT);

			expect(VIEWPORT.height - TALL.bottom).toBeLessThan(AUTOCOMPLETE_ESTIMATED_HEIGHT);
			expect(placement.openAbove).toBe(false);
		});

		it('pulls the list up just enough to stay fully on screen', () => {
			const placement = computeAutocompletePlacement(TALL, VIEWPORT);

			expect(placement.top).toBeLessThan(TALL.bottom);
			expect(placement.top + AUTOCOMPLETE_ESTIMATED_HEIGHT).toBeLessThanOrEqual(VIEWPORT.height);
		});

		it('never drags the list below its anchor to do it', () => {
			const placement = computeAutocompletePlacement(TALL, VIEWPORT);
			expect(placement.top).toBeLessThanOrEqual(TALL.bottom);
		});
	});

	it('leaves the chat composer’s flip-above case exactly as it was', () => {
		// A tall composer pinned to the bottom must still open upward, not get
		// clamped down over the text being typed.
		const placement = computeAutocompletePlacement(
			{ top: 460, bottom: 880, left: 200, width: 600 },
			VIEWPORT
		);

		expect(placement.openAbove).toBe(true);
		expect(placement.bottom).toBe(VIEWPORT.height - 460);
	});

	it('clamps an anchor scrolled off the top of the viewport', () => {
		const placement = computeAutocompletePlacement(
			{ top: -500, bottom: -100, left: 200, width: 600 },
			VIEWPORT
		);

		expect(placement.top).toBeGreaterThanOrEqual(0);
		expect(placement.bottom).toBeLessThanOrEqual(VIEWPORT.height);
	});
});
