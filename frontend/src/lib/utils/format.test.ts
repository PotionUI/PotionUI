import { describe, it, expect } from 'vitest';
import { formatBytes, formatDuration, formatCount, formatSeconds } from './format';

describe('formatBytes', () => {
	it('handles zero bytes', () => {
		expect(formatBytes(0)).toBe('0 B');
	});

	it('formats sub-KB values as bytes', () => {
		expect(formatBytes(512)).toBe('512 B');
		expect(formatBytes(1023)).toBe('1023 B');
	});

	it('formats exact unit boundaries', () => {
		expect(formatBytes(1024)).toBe('1 KB');
		expect(formatBytes(1024 * 1024)).toBe('1 MB');
		expect(formatBytes(1024 * 1024 * 1024)).toBe('1 GB');
		expect(formatBytes(1024 * 1024 * 1024 * 1024)).toBe('1 TB');
	});

	it('formats TB-scale values', () => {
		expect(formatBytes(2.5 * 1024 * 1024 * 1024 * 1024)).toBe('2.5 TB');
	});

	it('respects the decimals argument', () => {
		expect(formatBytes(1536, 1)).toBe('1.5 KB');
		expect(formatBytes(1536, 0)).toBe('2 KB');
	});
});

describe('formatDuration', () => {
	it('formats sub-second durations', () => {
		expect(formatDuration(400)).toBe('0.4s');
	});

	it('formats seconds', () => {
		expect(formatDuration(17000)).toBe('17s');
		expect(formatDuration(59000)).toBe('59s');
	});

	it('formats minutes', () => {
		expect(formatDuration(93000)).toBe('1m 33s');
		expect(formatDuration(60000)).toBe('1m');
	});

	it('formats hours', () => {
		expect(formatDuration(2 * 3600000 + 5 * 60000)).toBe('2h 5m');
		expect(formatDuration(3600000)).toBe('1h');
	});

	it('handles zero/negative', () => {
		expect(formatDuration(0)).toBe('0s');
		expect(formatDuration(-100)).toBe('0s');
	});
});

describe('formatSeconds', () => {
	it('handles zero/negative', () => {
		expect(formatSeconds(0)).toBe('0s');
		expect(formatSeconds(-1)).toBe('0s');
	});

	it('formats sub-minute durations with one decimal', () => {
		expect(formatSeconds(5)).toBe('5.0s');
		expect(formatSeconds(5.2)).toBe('5.2s');
		expect(formatSeconds(59.95)).toBe('60.0s');
	});

	it('delegates to formatDuration at and above a minute', () => {
		expect(formatSeconds(60)).toBe('1m');
		expect(formatSeconds(93)).toBe('1m 33s');
	});
});

describe('formatCount', () => {
	it('adds thousands separators', () => {
		expect(formatCount(0)).toBe('0');
		expect(formatCount(999)).toBe('999');
		expect(formatCount(1000)).toBe('1,000');
		expect(formatCount(1234567)).toBe('1,234,567');
	});
});
