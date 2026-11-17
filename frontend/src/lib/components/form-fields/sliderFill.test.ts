import { describe, expect, it } from 'vitest';
import { SLIDER_NAVIGATION_KEYS, fillSpan, fillsFromDefault, trackOffset, twoSidedFill } from './sliderGeometry';

describe('fillSpan', () => {
	it('paints from the default towards a value below it', () => {
		expect(fillSpan(-50, 0, -100, 100)).toEqual({ from: 0.25, to: 0.5 });
	});

	it('paints nothing at the default', () => {
		expect(fillSpan(0, 0, -100, 100)).toEqual({ from: 0.5, to: 0.5 });
	});

	it('paints from the default towards a value above it', () => {
		expect(fillSpan(25, 0, -100, 100)).toEqual({ from: 0.5, to: 0.625 });
	});

	it('clamps a value outside the range to the track ends', () => {
		expect(fillSpan(500, 0, -100, 100)).toEqual({ from: 0.5, to: 1 });
	});
});

describe('twoSidedFill', () => {
	it('stops the signal colour at the default and at the value, using the thumb inset', () => {
		const start = trackOffset(0.25);
		const end = trackOffset(0.5);

		expect(twoSidedFill(-50, 0, -100, 100)).toBe(
			`linear-gradient(to right, rgb(var(--surface-3)) ${start}, rgb(var(--signal)) ${start}, rgb(var(--signal)) ${end}, rgb(var(--surface-3)) ${end})`
		);
	});
});

describe('fillsFromDefault', () => {
	it('needs the from-default mode and a default inside the range', () => {
		expect(fillsFromDefault('from-default', 0, -100, 100)).toBe(true);
		expect(fillsFromDefault(undefined, 0, -100, 100)).toBe(false);
		expect(fillsFromDefault('from-start', 0, -100, 100)).toBe(false);
		expect(fillsFromDefault('from-default', undefined, -100, 100)).toBe(false);
		expect(fillsFromDefault('from-default', 500, -100, 100)).toBe(false);
		expect(fillsFromDefault('from-default', 0, 5, 5)).toBe(false);
	});

	it('accepts a default sitting on a bound', () => {
		expect(fillsFromDefault('from-default', -100, -100, 100)).toBe(true);
	});
});

describe('SLIDER_NAVIGATION_KEYS', () => {
	it('lists the keys that move a range input', () => {
		expect([...SLIDER_NAVIGATION_KEYS].sort()).toEqual(
			['ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'End', 'Home', 'PageDown', 'PageUp'].sort()
		);
	});
});
