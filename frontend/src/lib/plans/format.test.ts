import { describe, it, expect } from 'vitest';
import { formatBytes, formatLimitChip, formatLimitValue, formatResetText, formatUsd, percent } from './format';
import type { LimitKindDescriptor } from './types';

const GB = 1024 ** 3;
const storage: LimitKindDescriptor = { key: 's', label: 'Storage space', description: '', value_type: 'bytes', unit: 'bytes', window: 'none' };
const daily: LimitKindDescriptor = { key: 'd', label: 'Generations per day', description: '', value_type: 'count', unit: 'count', window: 'day' };
const cloud: LimitKindDescriptor = { key: 'c', label: 'Cloud spend per month', description: '', value_type: 'usd', unit: 'usd', window: 'month' };

describe('format', () => {
	it('formats bytes, currency and no limit', () => {
		expect(formatBytes(20 * GB)).toBe('20 GB');
		expect(formatBytes(18.6 * GB)).toBe('18.6 GB');
		expect(formatBytes(2 * 1024 ** 4)).toBe('2 TB');
		expect(formatUsd(50)).toBe('$50');
		expect(formatLimitValue(storage, null)).toBe('no limit');
	});

	it('builds the plan chips', () => {
		expect(formatLimitChip(storage, 5 * GB)).toBe('Storage 5 GB');
		expect(formatLimitChip(daily, 20)).toBe('Generations 20 / day');
		expect(formatLimitChip(cloud, 50)).toBe('Cloud $50 / mo');
	});

	it('computes percent and reset text', () => {
		expect(percent(37, 50)).toBe(74);
		expect(percent(60, 50)).toBe(100);
		expect(percent(1, null)).toBeNull();
		const now = new Date('2026-10-05T20:00:00Z');
		expect(formatResetText('2026-10-06T00:12:00Z', now)).toBe('resets in 4 h 12 min');
		expect(formatResetText('2026-10-05T20:30:00Z', now)).toBe('resets in 30 min');
	});
});
