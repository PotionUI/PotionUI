import { describe, it, expect } from 'vitest';
import { timeAgo, dayLabel, dayKey } from './relativeTime';

const NOW = new Date('2026-07-07T12:00:00');

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
});

describe('dayKey', () => {
	it('buckets by local calendar day', () => {
		expect(dayKey('2026-07-07T00:10:00')).toBe(dayKey('2026-07-07T23:50:00'));
		expect(dayKey('2026-07-07T10:00:00')).not.toBe(dayKey('2026-07-06T10:00:00'));
	});
});
