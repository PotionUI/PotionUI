import { describe, it, expect } from 'vitest';
import {
	MIN_TRIM_SECONDS,
	clampTrimPoints,
	fractionOfTime,
	fullTrim,
	isFullClip,
	safeDuration,
	setTrimEnd,
	setTrimStart,
	timeAtFraction,
	toTrimOperation,
	trimLength
} from './trimPoints';

describe('safeDuration', () => {
	it('rejects everything a media element reports before metadata', () => {
		expect(safeDuration(null)).toBe(0);
		expect(safeDuration(undefined)).toBe(0);
		expect(safeDuration(NaN)).toBe(0);
		expect(safeDuration(Infinity)).toBe(0);
		expect(safeDuration(0)).toBe(0);
		expect(safeDuration(-3)).toBe(0);
	});
});

describe('fullTrim', () => {
	it('opens on the whole medium', () => {
		expect(fullTrim(8.4)).toEqual({ start: 0, end: 8.4 });
	});

	it('collapses to nothing when the duration is unknown', () => {
		expect(fullTrim(NaN)).toEqual({ start: 0, end: 0 });
	});
});

describe('clampTrimPoints', () => {
	it('leaves a valid selection alone', () => {
		expect(clampTrimPoints({ start: 1, end: 5 }, 10)).toEqual({ start: 1, end: 5 });
	});

	it('pulls both points inside the medium', () => {
		expect(clampTrimPoints({ start: -4, end: 99 }, 10)).toEqual({ start: 0, end: 10 });
	});

	it('refuses an inverted selection, which the server rejects outright', () => {
		const points = clampTrimPoints({ start: 8, end: 2 }, 10);
		expect(points.end).toBeGreaterThan(points.start);
		expect(points.end - points.start).toBeCloseTo(MIN_TRIM_SECONDS);
	});

	it('keeps the start inside the medium even when it is dragged past the end', () => {
		const points = clampTrimPoints({ start: 20, end: 20 }, 10);
		expect(points.start).toBeLessThan(10);
		expect(points.end).toBeLessThanOrEqual(10);
	});

	it('gives up on the minimum length rather than invent duration', () => {
		expect(clampTrimPoints({ start: 0, end: 1 }, 0.02)).toEqual({ start: 0, end: 0.02 });
	});
});

describe('setTrimStart', () => {
	it('moves the in point', () => {
		expect(setTrimStart({ start: 1, end: 8 }, 3, 10)).toEqual({ start: 3, end: 8 });
	});

	it('stops short of the out point instead of crossing it', () => {
		const points = setTrimStart({ start: 1, end: 5 }, 9, 10);
		expect(points.end).toBe(5);
		expect(points.start).toBeCloseTo(5 - MIN_TRIM_SECONDS);
	});

	it('never goes below zero', () => {
		expect(setTrimStart({ start: 2, end: 8 }, -5, 10).start).toBe(0);
	});
});

describe('setTrimEnd', () => {
	it('moves the out point', () => {
		expect(setTrimEnd({ start: 1, end: 8 }, 6, 10)).toEqual({ start: 1, end: 6 });
	});

	it('stops short of the in point instead of crossing it', () => {
		const points = setTrimEnd({ start: 4, end: 8 }, 1, 10);
		expect(points.start).toBe(4);
		expect(points.end).toBeCloseTo(4 + MIN_TRIM_SECONDS);
	});

	it('never runs past the medium', () => {
		expect(setTrimEnd({ start: 1, end: 8 }, 50, 10).end).toBe(10);
	});
});

describe('trimLength', () => {
	it('is the distance between the points', () => {
		expect(trimLength({ start: 1.5, end: 4 })).toBeCloseTo(2.5);
	});

	it('is never negative', () => {
		expect(trimLength({ start: 4, end: 1 })).toBe(0);
	});
});

describe('isFullClip', () => {
	it('recognises an untouched selection, which needs no trim at all', () => {
		expect(isFullClip({ start: 0, end: 8.4 }, 8.4)).toBe(true);
	});

	it('tolerates a strip dragged to within a millisecond of the ends', () => {
		expect(isFullClip({ start: 0.002, end: 8.398 }, 8.4)).toBe(true);
	});

	it('sees a real selection', () => {
		expect(isFullClip({ start: 1, end: 8.4 }, 8.4)).toBe(false);
		expect(isFullClip({ start: 0, end: 7 }, 8.4)).toBe(false);
	});

	it('treats an unknown duration as nothing to apply', () => {
		expect(isFullClip({ start: 0, end: 0 }, null)).toBe(true);
	});
});

describe('timeAtFraction / fractionOfTime', () => {
	it('round-trips', () => {
		expect(timeAtFraction(0.25, 8)).toBe(2);
		expect(fractionOfTime(2, 8)).toBe(0.25);
	});

	it('clamps a pointer dragged off either end of the strip', () => {
		expect(timeAtFraction(-0.4, 8)).toBe(0);
		expect(timeAtFraction(1.7, 8)).toBe(8);
	});

	it('answers 0 rather than dividing by an unknown duration', () => {
		expect(fractionOfTime(3, 0)).toBe(0);
		expect(fractionOfTime(3, null)).toBe(0);
	});
});

describe('toTrimOperation', () => {
	it('emits the API shape', () => {
		expect(toTrimOperation({ start: 1.2, end: 6.5 }, 8.4)).toEqual({
			type: 'trim',
			start_seconds: 1.2,
			end_seconds: 6.5
		});
	});

	it('rounds off the drift a pointer drag accumulates', () => {
		const operation = toTrimOperation({ start: 1.20000000004, end: 6.4999999 }, 8.4);
		expect(operation.start_seconds).toBe(1.2);
		expect(operation.end_seconds).toBe(6.5);
	});

	it('never sends an end past the duration', () => {
		expect(toTrimOperation({ start: 0, end: 99 }, 8.4).end_seconds).toBe(8.4);
	});

	it('never sends an inverted selection', () => {
		const operation = toTrimOperation({ start: 6, end: 2 }, 8.4);
		expect(operation.end_seconds).toBeGreaterThan(operation.start_seconds);
	});
});
