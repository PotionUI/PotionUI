import { describe, it, expect } from 'vitest';
import {
	TRIM_IN_AFTER_OUT_ERROR,
	TRIM_OUT_BEFORE_IN_ERROR,
	TRIM_TIME_INPUT_ERROR,
	commitTrimIn,
	commitTrimOut,
	parseTimeInput
} from './trimTimeEntry';

describe('parseTimeInput', () => {
	it('reads plain seconds', () => {
		expect(parseTimeInput('45')).toBe(45);
		expect(parseTimeInput('45.5')).toBe(45.5);
	});

	it('reads m:ss and m:ss.cc', () => {
		expect(parseTimeInput('0:45')).toBe(45);
		expect(parseTimeInput('0:45.00')).toBe(45);
		expect(parseTimeInput('1:05.5')).toBe(65.5);
	});

	it('reads h:mm:ss', () => {
		expect(parseTimeInput('1:02:03.45')).toBeCloseTo(3723.45);
	});

	it('tolerates surrounding whitespace', () => {
		expect(parseTimeInput('  50  ')).toBe(50);
	});

	it('accepts an unpadded segment', () => {
		expect(parseTimeInput('1:5')).toBe(65);
	});

	it('rejects an empty or blank string', () => {
		expect(parseTimeInput('')).toBeNull();
		expect(parseTimeInput('   ')).toBeNull();
	});

	it('rejects text that is not a time', () => {
		expect(parseTimeInput('abc')).toBeNull();
		expect(parseTimeInput('1:ab')).toBeNull();
	});

	it('rejects a negative or signed value', () => {
		expect(parseTimeInput('-5')).toBeNull();
		expect(parseTimeInput('0:-5')).toBeNull();
	});

	it('rejects more than an hours:minutes:seconds split', () => {
		expect(parseTimeInput('1:02:03:04')).toBeNull();
	});

	it('rejects a decimal minutes or hours segment', () => {
		expect(parseTimeInput('1.5:00')).toBeNull();
	});

	it('rejects an empty segment', () => {
		expect(parseTimeInput(':30')).toBeNull();
		expect(parseTimeInput('1:')).toBeNull();
	});
});

describe('commitTrimIn', () => {
	it('moves the in point to the typed time', () => {
		const result = commitTrimIn('45', { start: 0, end: 60 }, 60);
		expect(result.error).toBeNull();
		expect(result.points).toEqual({ start: 45, end: 60 });
	});

	it('parses the m:ss.cc shape the readout displays', () => {
		const result = commitTrimIn('0:45.00', { start: 0, end: 60 }, 60);
		expect(result.error).toBeNull();
		expect(result.points.start).toBe(45);
	});

	it('refuses an in at or past the current out, with an inline message', () => {
		const result = commitTrimIn('55', { start: 0, end: 50 }, 60);
		expect(result.error).toBe(TRIM_IN_AFTER_OUT_ERROR);
		expect(result.points).toEqual({ start: 0, end: 50 });
	});

	it('refuses unparsable text, with an inline message, and changes nothing', () => {
		const points = { start: 10, end: 50 };
		const result = commitTrimIn('not a time', points, 60);
		expect(result.error).toBe(TRIM_TIME_INPUT_ERROR);
		expect(result.points).toBe(points);
	});
});

describe('commitTrimOut', () => {
	it('moves the out point to the typed time', () => {
		const result = commitTrimOut('50', { start: 45, end: 60 }, 60);
		expect(result.error).toBeNull();
		expect(result.points).toEqual({ start: 45, end: 50 });
	});

	it('clamps a time past the medium down to its duration', () => {
		const result = commitTrimOut('9999', { start: 0, end: 10 }, 60);
		expect(result.error).toBeNull();
		expect(result.points).toEqual({ start: 0, end: 60 });
	});

	it('refuses an out at or before the current in, with an inline message', () => {
		const result = commitTrimOut('5', { start: 10, end: 50 }, 60);
		expect(result.error).toBe(TRIM_OUT_BEFORE_IN_ERROR);
		expect(result.points).toEqual({ start: 10, end: 50 });
	});

	it('refuses unparsable text, with an inline message, and changes nothing', () => {
		const points = { start: 10, end: 50 };
		const result = commitTrimOut('12:', points, 60);
		expect(result.error).toBe(TRIM_TIME_INPUT_ERROR);
		expect(result.points).toBe(points);
	});
});
