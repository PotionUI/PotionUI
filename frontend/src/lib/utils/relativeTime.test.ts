import { describe, it, expect } from 'vitest';
import { timeAgo, dayLabel, dayKey, parseServerDate } from './relativeTime';

const NOW = new Date(Date.UTC(2026, 6, 7, 12, 0, 0));

describe('parseServerDate', () => {
	it('returns null for empty/invalid input', () => {
		expect(parseServerDate(undefined)).toBeNull();
		expect(parseServerDate(null)).toBeNull();
		expect(parseServerDate('')).toBeNull();
		expect(parseServerDate('not a date')).toBeNull();
	});

	it('passes a Date or number through unchanged', () => {
		const d = new Date(Date.UTC(2026, 8, 14, 10, 0, 0));
		expect(parseServerDate(d)).toBe(d);
		const ms = d.getTime();
		expect(parseServerDate(ms)?.getTime()).toBe(ms);
	});

	it('treats an offset-less ISO string as UTC', () => {
		expect(parseServerDate('2026-09-14T10:00:00')?.getTime()).toBe(Date.UTC(2026, 8, 14, 10, 0, 0));
	});

	it('treats an offset-less sqlite "YYYY-MM-DD HH:MM:SS" string as UTC', () => {
		expect(parseServerDate('2026-09-14 10:00:00')?.getTime()).toBe(Date.UTC(2026, 8, 14, 10, 0, 0));
	});

	it('leaves a "Z"-suffixed string unchanged', () => {
		expect(parseServerDate('2026-09-14T10:00:00Z')?.getTime()).toBe(Date.UTC(2026, 8, 14, 10, 0, 0));
	});

	it('leaves a "+00:00"-suffixed string unchanged', () => {
		expect(parseServerDate('2026-09-14T10:00:00+00:00')?.getTime()).toBe(Date.UTC(2026, 8, 14, 10, 0, 0));
	});

	it('honours a non-zero offset', () => {
		expect(parseServerDate('2026-09-14T10:00:00+02:00')?.getTime()).toBe(Date.UTC(2026, 8, 14, 8, 0, 0));
	});
});

describe('timeAgo', () => {
	it('handles empty and invalid input', () => {
		expect(timeAgo(undefined, NOW)).toBe('');
		expect(timeAgo('not a date', NOW)).toBe('');
	});

	it('formats recent times compactly', () => {
		expect(timeAgo('2026-07-07T11:59:40', NOW)).toBe('now');
		expect(timeAgo('2026-07-07T11:25:00', NOW)).toBe('35m ago');
		expect(timeAgo('2026-07-07T09:00:00', NOW)).toBe('3h ago');
		expect(timeAgo('2026-07-04T12:00:00', NOW)).toBe('3d ago');
	});

	it('falls back to a date past one week', () => {
		expect(timeAgo('2026-06-20T12:00:00', NOW)).toBe('Jun 20');
		expect(timeAgo('2025-12-24T12:00:00', NOW)).toBe('Dec 24 2025');
	});

	it('treats an offset-aware string correctly regardless of the local offset-less reading', () => {
		expect(timeAgo('2026-07-07T11:59:40+00:00', NOW)).toBe('now');
		expect(timeAgo('2026-07-07T13:59:40+02:00', NOW)).toBe('now');
	});
});

describe('dayLabel', () => {
	it('labels today and yesterday', () => {
		expect(dayLabel('2026-07-07T03:00:00', NOW)).toBe('Today');
		expect(dayLabel('2026-07-06T23:59:00', NOW)).toBe('Yesterday');
	});

	it('labels older days with a date', () => {
		expect(dayLabel('2026-07-03T10:00:00', NOW)).toBe('Jul 3');
		expect(dayLabel('2025-07-03T10:00:00', NOW)).toBe('Jul 3 2025');
	});

	it('returns Unknown for invalid input', () => {
		expect(dayLabel('not a date', NOW)).toBe('Unknown');
	});
});

describe('dayKey', () => {
	it('buckets by local calendar day', () => {
		expect(dayKey('2026-07-07T00:10:00')).toBe(dayKey('2026-07-07T23:50:00'));
		expect(dayKey('2026-07-07T10:00:00')).not.toBe(dayKey('2026-07-06T10:00:00'));
	});

	it('returns unknown for invalid input', () => {
		expect(dayKey('not a date')).toBe('unknown');
	});
});
