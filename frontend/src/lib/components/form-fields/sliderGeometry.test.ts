import { describe, expect, it } from 'vitest';
import { THUMB_PX, trackFraction, trackOffset } from './sliderGeometry';

describe('trackFraction', () => {
	it('maps a value onto 0..1 across min..max', () => {
		expect(trackFraction(0.5, 0, 1)).toBe(0.5);
		expect(trackFraction(0, 0, 1)).toBe(0);
		expect(trackFraction(1, 0, 1)).toBe(1);
	});

	it('handles a non-zero minimum', () => {
		expect(trackFraction(3.5, 1, 10)).toBeCloseTo(2.5 / 9, 10);
		expect(trackFraction(1, 1, 2)).toBe(0);
		expect(trackFraction(1.5, 1, 2)).toBe(0.5);
	});

	it('clamps out-of-range values instead of overflowing the track', () => {
		expect(trackFraction(-5, 0, 1)).toBe(0);
		expect(trackFraction(99, 0, 1)).toBe(1);
	});

	it('is 0 for a degenerate or non-numeric input rather than NaN', () => {
		expect(trackFraction(5, 10, 10)).toBe(0);
		expect(trackFraction(5, 10, 1)).toBe(0);
		expect(trackFraction(NaN, 0, 1)).toBe(0);
	});
});

describe('trackOffset', () => {
	it('insets by half a thumb at each end so the fill meets the thumb centre', () => {
		expect(trackOffset(0)).toBe('calc(7px + 0 * (100% - 14px))');
		expect(trackOffset(1)).toBe('calc(7px + 1 * (100% - 14px))');
	});

	it('is not a plain percentage of the full width - that is the drift bug', () => {
		expect(trackOffset(0.5)).not.toBe('50%');
		expect(trackOffset(0.5)).toContain(`100% - ${THUMB_PX}px`);
	});

	it('clamps its fraction so a stray value cannot escape the track', () => {
		expect(trackOffset(-1)).toBe(trackOffset(0));
		expect(trackOffset(2)).toBe(trackOffset(1));
		expect(trackOffset(NaN)).toBe(trackOffset(0));
	});
});
