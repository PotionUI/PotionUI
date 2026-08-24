import { describe, it, expect } from 'vitest';
import { formatTimecode, formatClipLength, formatPreciseTime } from './timecode';

describe('formatTimecode', () => {
	it('writes a clock under an hour without an hour field', () => {
		expect(formatTimecode(0)).toBe('0:00');
		expect(formatTimecode(7)).toBe('0:07');
		expect(formatTimecode(83)).toBe('1:23');
	});

	it('adds the hour field past an hour', () => {
		expect(formatTimecode(3723)).toBe('1:02:03');
	});

	it('floors rather than rounds, so it never names an unreached second', () => {
		expect(formatTimecode(59.9)).toBe('0:59');
	});

	it('answers 0:00 for the values a media element reports before metadata', () => {
		expect(formatTimecode(NaN)).toBe('0:00');
		expect(formatTimecode(Infinity)).toBe('0:00');
		expect(formatTimecode(-4)).toBe('0:00');
	});
});

describe('formatClipLength', () => {
	it('keeps a tenth under a minute', () => {
		expect(formatClipLength(8.44)).toBe('8.4s');
	});

	it('drops a trailing .0', () => {
		expect(formatClipLength(5)).toBe('5s');
		expect(formatClipLength(5.02)).toBe('5s');
	});

	it('switches to a clock at a minute', () => {
		expect(formatClipLength(60)).toBe('1:00');
		expect(formatClipLength(124)).toBe('2:04');
	});

	it('rounds up to the minute boundary rather than printing 60s', () => {
		expect(formatClipLength(59.99)).toBe('1:00');
	});
});

describe('formatPreciseTime', () => {
	it('carries hundredths', () => {
		expect(formatPreciseTime(12.34)).toBe('0:12.34');
	});

	it('truncates, so it never names a moment past the point that was set', () => {
		expect(formatPreciseTime(12.349)).toBe('0:12.34');
		expect(formatPreciseTime(12.999)).toBe('0:12.99');
	});

	it('pads a single hundredth', () => {
		expect(formatPreciseTime(3.05)).toBe('0:03.05');
	});
});
