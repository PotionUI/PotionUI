import { describe, it, expect } from 'vitest';
import { formatCountdown, formatResetsIn, msUntil } from './countdown';

describe('countdown', () => {
	it('formats hours and minutes', () => {
		expect(formatCountdown((4 * 3600 + 12 * 60) * 1000)).toBe('4 h 12 min');
	});

	it('formats minutes and seconds under an hour', () => {
		expect(formatCountdown(125_000)).toBe('2 min 5 s');
	});

	it('formats seconds and never goes negative', () => {
		expect(formatCountdown(9_000)).toBe('9 s');
		expect(formatCountdown(-5)).toBe('0 s');
	});

	it('rounds a partial second up so the last tick shows 1 s', () => {
		expect(formatCountdown(400)).toBe('1 s');
	});

	it('computes the remaining time from an ISO timestamp', () => {
		const now = Date.parse('2026-10-05T20:00:00Z');
		expect(msUntil('2026-10-06T00:00:00Z', now)).toBe(4 * 3600 * 1000);
		expect(msUntil('2026-10-05T19:00:00Z', now)).toBe(0);
		expect(msUntil(null, now)).toBeNull();
		expect(msUntil('nonsense', now)).toBeNull();
	});

	it('words near resets in hours or minutes and far ones as a date', () => {
		expect(formatResetsIn(4 * 3600 * 1000, '2026-10-06T00:00:00Z')).toBe('resets in 4 h');
		expect(formatResetsIn(20 * 60 * 1000, '2026-10-06T00:00:00Z')).toBe('resets in 20 min');
		expect(formatResetsIn(20 * 24 * 3600 * 1000, '2026-11-01T12:00:00Z')).toMatch(/^resets Nov 1$/);
	});
});
