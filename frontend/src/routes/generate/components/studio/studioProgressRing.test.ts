import { describe, it, expect } from 'vitest';
import { formatElapsedClock, ringCircumference, ringDashOffset } from './studioProgressRing';

describe('formatElapsedClock', () => {
	it('formats zero as 00:00', () => {
		expect(formatElapsedClock(0)).toBe('00:00');
	});

	it('pads single-digit seconds', () => {
		expect(formatElapsedClock(7000)).toBe('00:07');
	});

	it('rolls seconds into minutes', () => {
		expect(formatElapsedClock(75_000)).toBe('01:15');
	});

	it('truncates a partial second rather than rounding up', () => {
		expect(formatElapsedClock(59_999)).toBe('00:59');
	});

	it('falls back to 00:00 for null, negative, or non-finite input', () => {
		expect(formatElapsedClock(null)).toBe('00:00');
		expect(formatElapsedClock(-500)).toBe('00:00');
		expect(formatElapsedClock(NaN)).toBe('00:00');
	});
});

describe('ringCircumference', () => {
	it('matches the design mock radius (32px -> ~201)', () => {
		expect(ringCircumference(32)).toBeCloseTo(201.06, 1);
	});
});

describe('ringDashOffset', () => {
	const c = ringCircumference(32);

	it('is null when progress has not reported a fraction yet', () => {
		expect(ringDashOffset(null, c)).toBeNull();
	});

	it('is 0 (full ring drawn) at 100% progress', () => {
		expect(ringDashOffset(1, c)).toBeCloseTo(0, 5);
	});

	it('equals the full circumference (nothing drawn) at 0% progress', () => {
		expect(ringDashOffset(0, c)).toBeCloseTo(c, 5);
	});

	it('is half the circumference at 50% progress', () => {
		expect(ringDashOffset(0.5, c)).toBeCloseTo(c / 2, 5);
	});

	it('clamps out-of-range progress into [0, 1]', () => {
		expect(ringDashOffset(1.5, c)).toBeCloseTo(0, 5);
		expect(ringDashOffset(-0.5, c)).toBeCloseTo(c, 5);
	});
});
